from __future__ import annotations

import io
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

from atlas_agent import cli
from atlas_agent.config import Settings
from atlas_agent.schemas import (
    ApprovalRequest,
    MemoryRecord,
    PlanStep,
    RunResult,
    RunStatus,
)


def completed_result() -> RunResult:
    return RunResult(
        user_id="alice",
        thread_id="thread-1",
        status=RunStatus.COMPLETED,
        answer="Verified answer",
        plan=[
            PlanStep(
                id="step_1",
                description="Complete the task",
                success_criteria="Evidence exists",
            )
        ],
        sources=["https://example.com/source"],
        artifacts=["report.md"],
        iterations=2,
        review_cycles=1,
    )


class FakeMemory:
    def list(self, *, user_id: str) -> list[MemoryRecord]:
        return [
            MemoryRecord(
                id="memory-1",
                user_id=user_id,
                content="Prefers concise reports",
                category="preference",
                importance=4,
                source_thread="thread-1",
                created_at=datetime.now(UTC),
            )
        ]

    def clear(self, *, user_id: str) -> int:
        return 1 if user_id else 0


class FakeRuntime:
    def __init__(self, *, paused: bool = False) -> None:
        self.memory = FakeMemory()
        self.paused = paused
        self.resume_actions: list[str] = []

    async def run(self, *, message: str, user_id: str, thread_id: str) -> RunResult:
        assert message and user_id and thread_id
        return completed_result()

    async def resume(self, *, user_id: str, thread_id: str, response: Any) -> RunResult:
        assert user_id and thread_id
        self.resume_actions.append(response.action)
        return completed_result()

    async def state(self, *, user_id: str, thread_id: str) -> dict[str, Any]:
        interrupts = (
            [
                {
                    "id": "interrupt-1",
                    "action": "execute_python",
                    "question": "Approve code?",
                    "details": {"code": "print(42)"},
                }
            ]
            if self.paused
            else []
        )
        return {"values": {"user_id": user_id}, "next": [], "interrupts": interrupts}

    def graph_mermaid(self) -> str:
        return "graph TD; recall-->plan;"


def fake_runtime_context(runtime: FakeRuntime) -> Any:
    @asynccontextmanager
    async def context(*args: Any, **kwargs: Any) -> Any:
        yield runtime

    return context


def test_cli_help_and_result_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    output = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, force_terminal=False, width=100))

    help_result = CliRunner().invoke(cli.app, ["--help"])
    cli._render_result(completed_result())

    assert help_result.exit_code == 0
    assert "Durable LangGraph agent" in help_result.stdout
    rendered = output.getvalue()
    assert "Execution plan" in rendered
    assert "Verified answer" in rendered
    assert "report.md" in rendered


async def test_finish_with_approval_resumes_the_same_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FakeRuntime()
    monkeypatch.setattr(cli.typer, "confirm", lambda *args, **kwargs: True)
    interrupted = RunResult(
        user_id="alice",
        thread_id="thread-1",
        status=RunStatus.INTERRUPTED,
        interrupt=ApprovalRequest(
            id="interrupt-1",
            action="execute_python",
            question="Approve code?",
            details={"code": "print(42)"},
        ),
    )

    result = await cli._finish_with_approvals(runtime, interrupted)  # type: ignore[arg-type]

    assert result.status == RunStatus.COMPLETED
    assert runtime.resume_actions == ["approve"]


async def test_runtime_backed_cli_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime(paused=True)
    output = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, force_terminal=False, width=100))
    monkeypatch.setattr(cli, "open_runtime", fake_runtime_context(runtime))
    monkeypatch.setattr(cli.typer, "confirm", lambda *args, **kwargs: False)

    await cli._run_once("Complete it", "alice", "thread-1")
    await cli._print_graph()
    await cli._list_memories("alice")
    await cli._clear_memories("alice")
    await cli._resume_paused("alice", "thread-1")

    rendered = output.getvalue()
    assert "Verified answer" in rendered
    assert "recall-->plan" in rendered
    assert "Prefers concise reports" in rendered
    assert "Deleted 1 memories" in rendered
    assert runtime.resume_actions == ["reject"]


def test_command_wrappers_and_server_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fake_run_once(*args: Any) -> None:
        calls.append(("run", args))

    async def fake_chat(*args: Any) -> None:
        calls.append(("chat", args))

    async def fake_print_graph() -> None:
        calls.append(("graph", ()))

    async def fake_list(*args: Any) -> None:
        calls.append(("list", args))

    async def fake_clear(*args: Any) -> None:
        calls.append(("clear", args))

    async def fake_resume(*args: Any) -> None:
        calls.append(("resume", args))

    monkeypatch.setattr(cli, "_run_once", fake_run_once)
    monkeypatch.setattr(cli, "_chat", fake_chat)
    monkeypatch.setattr(cli, "_print_graph", fake_print_graph)
    monkeypatch.setattr(cli, "_list_memories", fake_list)
    monkeypatch.setattr(cli, "_clear_memories", fake_clear)
    monkeypatch.setattr(cli, "_resume_paused", fake_resume)
    monkeypatch.setattr(cli.typer, "confirm", lambda *args, **kwargs: True)

    cli.run_command("Task", "alice", "thread-fixed")
    cli.chat_command("alice", "thread-fixed")
    cli.graph_command()
    cli.memory_list("alice")
    cli.memory_clear("alice")
    cli.resume_command("thread-fixed", "alice")

    server: dict[str, Any] = {}
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: Settings(_env_file=None, api_host="127.0.0.1", api_port=9000),
    )
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: server.update(kwargs))
    cli.serve_command(None, None, False)

    assert [name for name, _ in calls] == ["run", "chat", "graph", "list", "clear", "resume"]
    assert server["host"] == "127.0.0.1"
    assert server["port"] == 9000


def test_doctor_json_is_actionable_and_never_exposes_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        workspace_dir=tmp_path / "workspace",
        memory_enabled=True,
        model="openai:gpt-4.1-mini",
        OPENAI_API_KEY="doctor-secret-value",
        TAVILY_API_KEY="search-secret-value",
        code_execution_backend="disabled",
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    result = CliRunner().invoke(cli.app, ["doctor", "--json"])

    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["ready"] is True
    assert report["model"] == {
        "provider": "openai",
        "configured": True,
        "credential_configured": True,
        "integration_available": True,
        "model": "gpt-4.1-mini",
    }
    assert report["search"]["mode"] == "tavily"
    assert report["python"]["status"] == "disabled"
    assert report["next_actions"] == []
    assert "doctor-secret-value" not in result.stdout
    assert "search-secret-value" not in result.stdout


def test_doctor_strict_fails_when_required_model_setup_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        workspace_dir=tmp_path / "workspace",
        memory_enabled=False,
        model="openai:gpt-4.1-mini",
        OPENAI_API_KEY="",
        code_execution_backend="docker",
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)

    result = CliRunner().invoke(cli.app, ["doctor", "--json", "--strict"])

    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["ready"] is False
    assert report["model"]["configured"] is False
    assert report["python"]["status"] == "docker-not-found"
    assert report["next_actions"] == [
        "Add OPENAI_API_KEY to .env, then restart Atlas.",
        "Install Docker or set ATLAS_CODE_EXECUTION_BACKEND=disabled.",
    ]


def test_doctor_human_output_keeps_secondary_capabilities_compact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        workspace_dir=tmp_path / "workspace",
        memory_enabled=True,
        model="anthropic:claude-sonnet-4-5",
        ANTHROPIC_API_KEY="",
        code_execution_backend="disabled",
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    result = CliRunner().invoke(cli.app, ["doctor"])

    assert result.exit_code == 0
    assert "Atlas setup" in result.stdout
    assert "Action required" in result.stdout
    assert "ANTHROPIC_API_KEY" in result.stdout
    assert "disabled (safe default)" in result.stdout


def test_doctor_describes_local_model_setup_without_a_network_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        workspace_dir=tmp_path / "workspace",
        memory_enabled=False,
        model="openai:gpt-4.1-mini",
        OPENAI_API_KEY="test-only-value",
        code_execution_backend="disabled",
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    result = CliRunner().invoke(cli.app, ["doctor"])

    assert result.exit_code == 0
    assert "configured" in result.stdout
    assert "connected" not in result.stdout


def test_doctor_checks_the_selected_docker_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        workspace_dir=tmp_path / "workspace",
        memory_enabled=False,
        model="openai:gpt-4.1-mini",
        OPENAI_API_KEY="test-only-value",
        code_execution_backend="docker",
    )
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(cli.PythonExecutor, "docker_is_ready", lambda self: False)

    report = cli._doctor_report(settings)

    assert report["ready"] is False
    assert report["python"]["status"] == "docker-not-ready"
    assert report["next_actions"] == [
        "Start Docker and pull python:3.12-alpine, or set ATLAS_CODE_EXECUTION_BACKEND=disabled."
    ]
