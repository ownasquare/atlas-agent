"""Approval-aware Python execution with a fail-closed Docker-only backend."""

from __future__ import annotations

import ast
import asyncio
import json
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Literal

from langchain.tools import BaseTool, tool
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field


class PythonExecutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=20_000)
    timeout_seconds: int | None = Field(default=None, ge=1, le=30)


_ALLOWED_MODULES = {
    "collections",
    "csv",
    "datetime",
    "decimal",
    "fractions",
    "functools",
    "itertools",
    "json",
    "math",
    "random",
    "re",
    "statistics",
}
_BLOCKED_NAMES = {
    "__import__",
    "breakpoint",
    "compile",
    "delattr",
    "dir",
    "eval",
    "exec",
    "getattr",
    "globals",
    "help",
    "input",
    "locals",
    "memoryview",
    "open",
    "setattr",
    "vars",
}
_WINDOWS_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


def validate_python_source(code: str) -> None:
    """Reject obvious host, process, network, filesystem, and introspection access."""
    try:
        tree = ast.parse(code, mode="exec")
    except (SyntaxError, RecursionError) as exc:
        raise ValueError("invalid Python source") from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > 1_000:
        raise ValueError("Python source is too complex")
    for node in nodes:
        if isinstance(node, ast.Match):
            raise ValueError("structural pattern matching is not allowed")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] not in _ALLOWED_MODULES:
                    raise ValueError(f"module is not allowed: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".", 1)[0]
            if node.level or module not in _ALLOWED_MODULES:
                raise ValueError(f"module is not allowed: {node.module or 'relative import'}")
        elif isinstance(node, ast.Name) and node.id in _BLOCKED_NAMES:
            raise ValueError(f"name is not allowed: {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError("private and dunder attributes are not allowed")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _BLOCKED_NAMES
        ):
            raise ValueError(f"call is not allowed: {node.func.id}")


@dataclass(frozen=True)
class ExecutionResult:
    status: Literal["succeeded", "failed", "timeout", "disabled"]
    backend: str
    hardened: bool
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "backend": self.backend,
            "hardened": self.hardened,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "truncated": self.truncated,
        }


class PythonExecutor:
    image = "python:3.12-alpine"

    def __init__(
        self,
        *,
        backend: Literal["docker", "disabled"] = "disabled",
        timeout_seconds: int = 10,
        memory_mb: int = 256,
        output_limit: int = 12_000,
    ) -> None:
        self.backend = backend
        self.timeout_seconds = timeout_seconds
        self.memory_mb = memory_mb
        self.output_limit = output_limit

    def execute(self, code: str, *, timeout_seconds: int | None = None) -> ExecutionResult:
        validate_python_source(code)
        timeout = min(timeout_seconds or self.timeout_seconds, 30)
        selected = self._select_backend()
        if selected == "disabled":
            return ExecutionResult(
                status="disabled",
                backend="disabled",
                hardened=True,
                stdout="",
                stderr="Python execution is disabled by configuration.",
                exit_code=None,
                duration_ms=0,
            )
        if selected == "docker":
            container_name = f"atlas-exec-{uuid.uuid4().hex}"
            command = self._docker_command(code, timeout, container_name=container_name)
            return self._run(
                command,
                timeout=timeout,
                backend="docker",
                hardened=True,
                timeout_cleanup=lambda: self._remove_container(container_name),
            )
        raise AssertionError("unreachable execution backend")

    def _select_backend(self) -> Literal["docker", "disabled"]:
        if self.backend == "disabled":
            return "disabled"
        if not self._docker_ready():
            raise RuntimeError(
                f"Docker backend requires a running daemon and the local image '{self.image}'"
            )
        return "docker"

    def _docker_ready(self) -> bool:
        return self.docker_is_ready()

    def docker_is_ready(self) -> bool:
        """Confirm both the Docker daemon and Atlas's expected local image are available."""
        docker = shutil.which("docker")
        if docker is None:
            return False
        try:
            probe = subprocess.run(  # noqa: S603
                [docker, "image", "inspect", self.image],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return probe.returncode == 0

    def _docker_command(self, code: str, timeout: int, *, container_name: str) -> list[str]:
        docker = shutil.which("docker")
        if docker is None:
            raise RuntimeError("Docker executable was not found")
        return [
            docker,
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            f"{self.memory_mb}m",
            "--cpus",
            "0.5",
            "--user",
            "65534:65534",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",  # noqa: S108 - isolated container tmpfs
            self.image,
            "python",
            "-I",
            "-c",
            code,
        ]

    def _remove_container(self, container_name: str) -> None:
        docker = shutil.which("docker")
        if docker is None:
            return
        with suppress(OSError, subprocess.SubprocessError):
            subprocess.run(  # noqa: S603
                [docker, "rm", "--force", container_name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )

    def _run(
        self,
        command: list[str],
        *,
        timeout: int,
        backend: str,
        hardened: bool,
        timeout_cleanup: Callable[[], None] | None = None,
    ) -> ExecutionResult:
        started = time.monotonic()
        clean_environment = {
            "PATH": os.defpath,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        process_options: dict[str, Any]
        if os.name == "nt":
            process_options = {"creationflags": _WINDOWS_CREATE_NEW_PROCESS_GROUP}
        else:
            process_options = {"start_new_session": True}
        process = subprocess.Popen(  # noqa: S603
            command,
            env=clean_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **process_options,
        )
        if process.stdout is None or process.stderr is None:  # pragma: no cover - Popen contract
            raise RuntimeError("execution output pipes were not created")
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        remaining = [self.output_limit]
        output_lock = threading.Lock()
        truncated = threading.Event()

        def drain(stream: Any, chunks: list[str]) -> None:
            while chunk := stream.read(4_096):
                with output_lock:
                    accepted = min(len(chunk), remaining[0])
                    if accepted:
                        chunks.append(chunk[:accepted])
                        remaining[0] -= accepted
                    if accepted < len(chunk):
                        truncated.set()

        readers = [
            threading.Thread(target=drain, args=(process.stdout, stdout_chunks), daemon=True),
            threading.Thread(target=drain, args=(process.stderr, stderr_chunks), daemon=True),
        ]
        for reader in readers:
            reader.start()
        try:
            process.wait(timeout=timeout)
            status: Literal["succeeded", "failed", "timeout", "disabled"] = (
                "succeeded" if process.returncode == 0 else "failed"
            )
        except subprocess.TimeoutExpired:
            if timeout_cleanup is not None:
                timeout_cleanup()
            if os.name == "nt":
                with suppress(AttributeError, OSError):
                    process.kill()
            else:
                with suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGKILL)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                with suppress(AttributeError, OSError):
                    process.kill()
                if timeout_cleanup is not None:
                    timeout_cleanup()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired as final_exc:
                    raise RuntimeError(
                        "sandbox client did not terminate after forced cleanup"
                    ) from final_exc
            if timeout_cleanup is not None:
                timeout_cleanup()
            status = "timeout"
        for reader in readers:
            reader.join()
        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)
        if status == "timeout":
            stderr = f"Execution exceeded the {timeout}-second limit.\n{stderr}".strip()
        duration_ms = int((time.monotonic() - started) * 1_000)
        if truncated.is_set():
            suffix = "\n...[output truncated]"
            if stderr:
                stderr = self._truncate(stderr)[0] + suffix
            else:
                stdout = self._truncate(stdout)[0] + suffix
        return ExecutionResult(
            status=status,
            backend=backend,
            hardened=hardened,
            stdout=stdout,
            stderr=stderr,
            exit_code=process.returncode,
            duration_ms=duration_ms,
            truncated=truncated.is_set(),
        )

    def _truncate(self, value: str) -> tuple[str, bool]:
        if len(value) <= self.output_limit:
            return value, False
        return value[: self.output_limit] + "\n...[output truncated]", True


def build_python_tool(
    executor: PythonExecutor,
    *,
    require_approval: bool = True,
) -> BaseTool:
    @tool(args_schema=PythonExecutionInput)
    async def execute_python(code: str, timeout_seconds: int | None = None) -> str:
        """Run Python for analysis after safety validation; this may pause for human approval."""
        final_code = code
        if require_approval:
            response = interrupt(
                {
                    "action": "execute_python",
                    "question": "Approve this generated Python code?",
                    "details": {"code": code, "timeout_seconds": timeout_seconds},
                }
            )
            if not isinstance(response, dict) or response.get("action") != "approve":
                return json.dumps({"status": "rejected", "reason": "Human rejected execution."})
            edited = response.get("edited_arguments")
            if isinstance(edited, dict) and isinstance(edited.get("code"), str):
                final_code = edited["code"]
        validated = PythonExecutionInput(code=final_code, timeout_seconds=timeout_seconds)
        result = await asyncio.to_thread(
            executor.execute,
            validated.code,
            timeout_seconds=validated.timeout_seconds,
        )
        return json.dumps(result.as_dict(), ensure_ascii=False)

    return execute_python
