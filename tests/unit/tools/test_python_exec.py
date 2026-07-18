"""Offline tests for Docker-only Python execution guards."""

from __future__ import annotations

import io
import signal
import subprocess
import sys
from typing import Any

import pytest
from pydantic import ValidationError

from atlas_agent.tools import python_exec
from atlas_agent.tools.python_exec import (
    PythonExecutionInput,
    PythonExecutor,
    validate_python_source,
)


def test_validate_python_source_allows_analysis_standard_library() -> None:
    validate_python_source(
        "import math\n"
        "from statistics import mean\n"
        "values = [1, 2, 3]\n"
        "print(math.sqrt(81), mean(values))"
    )


@pytest.mark.parametrize(
    "code",
    [
        "import os",
        "from pathlib import Path",
        "from . import helper",
        "open('payload.txt', 'w')",
        "eval('1 + 1')",
        "exec('print(1)')",
        "(1).__class__",
        "getattr(object, '__subclasses__')",
    ],
)
def test_validate_python_source_rejects_host_and_introspection_access(code: str) -> None:
    with pytest.raises(ValueError):
        validate_python_source(code)


def test_validate_python_source_rejects_match_class_dunder_recovery() -> None:
    bypass = """\
fn = lambda: 0
match fn:
    case object(__globals__=namespace):
        pass
builtins = namespace["__builtins__"]
match builtins:
    case object(__import__=importer):
        pass
importer("os")
"""

    with pytest.raises(ValueError, match="structural pattern matching"):
        validate_python_source(bypass)


def test_disabled_backend_still_validates_source_before_returning() -> None:
    executor = PythonExecutor(backend="disabled")

    with pytest.raises(ValueError, match="module is not allowed"):
        executor.execute("import os")

    result = executor.execute("print(2 + 2)")
    assert result.status == "disabled"
    assert result.backend == "disabled"
    assert result.hardened is True
    assert result.exit_code is None


def test_executor_defaults_to_the_safe_disabled_backend() -> None:
    result = PythonExecutor().execute("print(42)")

    assert result.status == "disabled"
    assert result.backend == "disabled"


def test_docker_backend_fails_closed_when_runtime_or_image_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = PythonExecutor(backend="docker")
    monkeypatch.setattr(executor, "_docker_ready", lambda: False)

    with pytest.raises(RuntimeError, match="Docker backend requires"):
        executor.execute("print(42)")


def test_run_streams_into_a_bounded_capture() -> None:
    executor = PythonExecutor(backend="disabled", output_limit=16)

    result = executor._run(
        [sys.executable, "-I", "-c", "print('x' * 100)"],
        timeout=2,
        backend="test-container",
        hardened=True,
    )

    assert result.status == "succeeded"
    assert result.stdout.startswith("x" * 16)
    assert result.stdout.endswith("...[output truncated]")
    assert result.truncated is True


def test_run_passes_only_minimal_environment_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class CompletedProcess:
        pid = 1234
        returncode = 0
        stdout = io.StringIO("ok")
        stderr = io.StringIO("")

        def wait(self, timeout: int | None = None) -> int:
            assert timeout == 2
            return 0

    def fake_popen(command: list[str], **kwargs: Any) -> CompletedProcess:
        captured["command"] = command
        captured.update(kwargs)
        return CompletedProcess()

    monkeypatch.setattr(python_exec.subprocess, "Popen", fake_popen)
    result = PythonExecutor(backend="disabled")._run(
        ["docker", "run"],
        timeout=2,
        backend="docker",
        hardened=True,
    )

    assert result.status == "succeeded"
    assert set(captured["env"]) == {
        "PATH",
        "PYTHONIOENCODING",
        "PYTHONDONTWRITEBYTECODE",
    }
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["start_new_session"] is True


def test_run_removes_container_and_kills_client_group_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    killed: list[tuple[int, int]] = []
    cleanup_calls: list[str] = []

    class TimedOutProcess:
        pid = 4321
        returncode = -signal.SIGKILL
        stdout = io.StringIO("partial")
        stderr = io.StringIO("child stopped")
        calls = 0

        def wait(self, timeout: int | None = None) -> int:
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(cmd=["docker"], timeout=timeout or 0)
            return self.returncode

    monkeypatch.setattr(python_exec.subprocess, "Popen", lambda *args, **kwargs: TimedOutProcess())
    monkeypatch.setattr(
        python_exec.os,
        "killpg",
        lambda process_id, sent_signal: killed.append((process_id, sent_signal)),
    )

    result = PythonExecutor(backend="disabled")._run(
        ["docker", "run"],
        timeout=1,
        backend="docker",
        hardened=True,
        timeout_cleanup=lambda: cleanup_calls.append("removed"),
    )

    assert cleanup_calls == ["removed", "removed"]
    assert killed == [(4321, signal.SIGKILL)]
    assert result.status == "timeout"
    assert "1-second limit" in result.stderr


def test_timeout_cleanup_survives_process_group_permission_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_calls: list[str] = []

    class TimedOutProcess:
        pid = 4321
        returncode = 137
        stdout = io.StringIO("")
        stderr = io.StringIO("")
        calls = 0

        def wait(self, timeout: int | None = None) -> int:
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(cmd=["docker"], timeout=timeout or 0)
            assert timeout == 3
            return self.returncode

    def permission_denied(process_id: int, sent_signal: int) -> None:
        raise PermissionError(process_id, sent_signal)

    monkeypatch.setattr(python_exec.subprocess, "Popen", lambda *args, **kwargs: TimedOutProcess())
    monkeypatch.setattr(python_exec.os, "killpg", permission_denied)

    result = PythonExecutor(backend="disabled")._run(
        ["docker", "run"],
        timeout=1,
        backend="docker",
        hardened=True,
        timeout_cleanup=lambda: cleanup_calls.append("removed"),
    )

    assert cleanup_calls == ["removed", "removed"]
    assert result.status == "timeout"


def test_windows_timeout_uses_a_process_group_and_direct_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class TimedOutProcess:
        pid = 4321
        returncode = 1
        stdout = io.StringIO("")
        stderr = io.StringIO("")
        calls = 0
        kills = 0

        def wait(self, timeout: int | None = None) -> int:
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(cmd=["docker"], timeout=timeout or 0)
            return self.returncode

        def kill(self) -> None:
            self.kills += 1

    process = TimedOutProcess()

    def fake_popen(command: list[str], **kwargs: Any) -> TimedOutProcess:
        captured["command"] = command
        captured.update(kwargs)
        return process

    monkeypatch.setattr(python_exec.os, "name", "nt")
    monkeypatch.setattr(python_exec.subprocess, "Popen", fake_popen)

    result = PythonExecutor(backend="disabled")._run(
        ["docker", "run"],
        timeout=1,
        backend="docker",
        hardened=True,
    )

    assert captured["creationflags"] == python_exec._WINDOWS_CREATE_NEW_PROCESS_GROUP
    assert "start_new_session" not in captured
    assert process.kills == 1
    assert result.status == "timeout"


def test_docker_command_enforces_isolation_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(python_exec.shutil, "which", lambda executable: "/usr/bin/docker")
    executor = PythonExecutor(backend="docker", memory_mb=128)

    command = executor._docker_command(
        "print(1)",
        timeout=3,
        container_name="atlas-exec-test",
    )

    assert command[:3] == ["/usr/bin/docker", "run", "--rm"]
    assert command[3:5] == ["--name", "atlas-exec-test"]
    assert command[5:7] == ["--network", "none"]
    assert "--read-only" in command
    assert command[command.index("--cap-drop") : command.index("--cap-drop") + 2] == [
        "--cap-drop",
        "ALL",
    ]
    assert command[command.index("--security-opt") : command.index("--security-opt") + 2] == [
        "--security-opt",
        "no-new-privileges",
    ]
    assert command[command.index("--memory") : command.index("--memory") + 2] == [
        "--memory",
        "128m",
    ]
    assert command[command.index("--user") : command.index("--user") + 2] == [
        "--user",
        "65534:65534",
    ]
    assert "--volume" not in command
    assert "-v" not in command
    assert command[-5:] == [PythonExecutor.image, "python", "-I", "-c", "print(1)"]


def test_remove_container_is_forceful_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(python_exec.shutil, "which", lambda executable: "/usr/bin/docker")

    def fake_run(command: list[str], **kwargs: Any) -> Any:
        captured["command"] = command
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(python_exec.subprocess, "run", fake_run)
    PythonExecutor(backend="disabled")._remove_container("atlas-exec-test")

    assert captured["command"] == [
        "/usr/bin/docker",
        "rm",
        "--force",
        "atlas-exec-test",
    ]
    assert captured["timeout"] == 3
    assert captured["check"] is False


def test_python_execution_input_forbids_extra_fields_and_invalid_bounds() -> None:
    with pytest.raises(ValidationError):
        PythonExecutionInput(code="print(1)", unexpected=True)
    with pytest.raises(ValidationError):
        PythonExecutionInput(code="")
    with pytest.raises(ValidationError):
        PythonExecutionInput(code="print(1)", timeout_seconds=31)
    with pytest.raises(ValidationError):
        PythonExecutionInput(code="#" * 20_001)
