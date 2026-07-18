from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from atlas_agent.api import create_app
from atlas_agent.config import Settings
from atlas_agent.runtime import ThreadConflictError
from atlas_agent.schemas import (
    ApprovalRequest,
    ApprovalResponse,
    MemoryRecord,
    PlanStep,
    RunResult,
    RunStatus,
    StreamEvent,
)
from atlas_agent.tools.files import WorkspaceFiles


class FakeMemory:
    def __init__(self) -> None:
        self.records = {
            "memory-1": MemoryRecord(
                id="memory-1",
                user_id="local-user",
                content="Prefers concise reports",
                category="preference",
                importance=4,
                source_thread="thread-1",
                created_at=datetime.now(UTC),
            )
        }

    def list(self, *, user_id: str) -> list[MemoryRecord]:
        return [record for record in self.records.values() if record.user_id == user_id]

    def delete(self, *, user_id: str, memory_id: str) -> bool:
        record = self.records.get(memory_id)
        if record is None or record.user_id != user_id:
            return False
        del self.records[memory_id]
        return True

    def clear(self, *, user_id: str) -> int:
        matches = [key for key, value in self.records.items() if value.user_id == user_id]
        for key in matches:
            del self.records[key]
        return len(matches)


class FakeRuntime:
    def __init__(self) -> None:
        self.memory = FakeMemory()

    @staticmethod
    def result(user_id: str, thread_id: str) -> RunResult:
        return RunResult(
            user_id=user_id,
            thread_id=thread_id,
            status=RunStatus.COMPLETED,
            answer="The verified result.",
            plan=[
                PlanStep(
                    id="step_1",
                    description="Verify the task",
                    success_criteria="Evidence is recorded",
                )
            ],
            sources=["https://example.com/source"],
            artifacts=["report.md"],
            iterations=2,
            review_cycles=1,
        )

    async def run(self, *, message: str, user_id: str, thread_id: str) -> RunResult:
        assert message
        return self.result(user_id, thread_id)

    async def resume(
        self,
        *,
        user_id: str,
        thread_id: str,
        response: ApprovalResponse,
    ) -> RunResult:
        assert response.action in {"approve", "reject"}
        return self.result(user_id, thread_id)

    async def stream(
        self,
        *,
        message: str | None,
        user_id: str,
        thread_id: str,
        response: ApprovalResponse | None = None,
    ) -> AsyncIterator[StreamEvent]:
        assert message or response
        yield StreamEvent(event="stage", data={"stage": "plan", "message": "Planning"})
        yield StreamEvent(
            event="result",
            data=self.result(user_id, thread_id).model_dump(mode="json"),
        )

    async def state(self, *, user_id: str, thread_id: str) -> dict[str, object]:
        return {"values": {"user_id": user_id}, "next": [], "thread_id": thread_id}

    def graph_mermaid(self) -> str:
        return "graph TD; recall-->plan;"


def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        workspace_dir=tmp_path / "workspace",
        memory_enabled=True,
        code_execution_backend="disabled",
    )


def test_health_static_workspace_and_openapi(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), runtime_override=FakeRuntime())
    with TestClient(app) as client:
        health = client.get("/api/health")
        root = client.get("/")
        css = client.get("/static/styles.css")
        openapi = client.get("/openapi.json")

    assert health.status_code == 200
    assert health.json()["memory_enabled"] is True
    assert isinstance(health.json()["model_configured"], bool)
    assert "<title>Atlas · Workspace</title>" in root.text
    assert ":root" in css.text
    assert openapi.json()["info"]["title"] == "Atlas Agent API"


def test_workspace_endpoints_list_and_read_confined_artifacts(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    workspace = WorkspaceFiles(tmp_path / "workspace")
    workspace.write("reports/summary.md", "# Summary\n\nVerified findings.")
    runtime.tool_bundle = SimpleNamespace(workspace=workspace)
    app = create_app(settings(tmp_path), runtime_override=runtime)

    with TestClient(app) as client:
        listed = client.get("/api/workspace", params={"pattern": "**/*"})
        opened = client.get("/api/workspace/file", params={"path": "reports/summary.md"})

    assert listed.status_code == 200
    assert listed.json()["entries"] == [
        {"path": "reports", "type": "directory", "bytes": None},
        {"path": "reports/summary.md", "type": "file", "bytes": 29},
    ]
    assert opened.status_code == 200
    assert opened.json()["path"] == "reports/summary.md"
    assert opened.json()["content"] == "# Summary\n\nVerified findings."


def test_workspace_endpoints_reject_unsafe_or_missing_paths(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    runtime.tool_bundle = SimpleNamespace(workspace=WorkspaceFiles(tmp_path / "workspace"))
    app = create_app(settings(tmp_path), runtime_override=runtime)

    with TestClient(app) as client:
        traversal = client.get("/api/workspace/file", params={"path": "../secret.txt"})
        missing = client.get("/api/workspace/file", params={"path": "missing.txt"})

    assert traversal.status_code == 400
    assert traversal.json()["detail"] == "The requested workspace path is not allowed."
    assert missing.status_code == 404
    assert missing.json()["detail"] == "The requested workspace file was not found."


def test_workspace_preview_reports_unsupported_and_oversized_files(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    workspace = WorkspaceFiles(tmp_path / "workspace", max_file_bytes=4)
    (workspace.root / "binary.bin").write_bytes(b"\xff")
    (workspace.root / "large.txt").write_text("12345", encoding="utf-8")
    runtime.tool_bundle = SimpleNamespace(workspace=workspace)
    app = create_app(settings(tmp_path), runtime_override=runtime)

    with TestClient(app) as client:
        binary = client.get("/api/workspace/file", params={"path": "binary.bin"})
        oversized = client.get("/api/workspace/file", params={"path": "large.txt"})

    assert binary.status_code == 415
    assert binary.json()["detail"] == "Only UTF-8 text files can be previewed."
    assert oversized.status_code == 413
    assert oversized.json()["detail"] == ("The requested workspace file exceeds the preview limit.")


def test_workspace_download_serves_a_bounded_attachment(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    workspace = WorkspaceFiles(tmp_path / "workspace")
    workspace.write("reports/final report.md", "# Final report\n\nVerified.")
    runtime.tool_bundle = SimpleNamespace(workspace=workspace)
    app = create_app(settings(tmp_path), runtime_override=runtime)

    with TestClient(app) as client:
        response = client.get(
            "/api/workspace/download",
            params={"path": "reports/final report.md"},
        )

    assert response.status_code == 200
    assert response.content == b"# Final report\n\nVerified."
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert "final%20report.md" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_workspace_download_rejects_unsafe_missing_and_oversized_files(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    workspace = WorkspaceFiles(tmp_path / "workspace", max_file_bytes=4)
    (workspace.root / "large.txt").write_text("12345", encoding="utf-8")
    runtime.tool_bundle = SimpleNamespace(workspace=workspace)
    app = create_app(settings(tmp_path), runtime_override=runtime)

    with TestClient(app) as client:
        traversal = client.get(
            "/api/workspace/download",
            params={"path": "../secret.txt"},
        )
        missing = client.get(
            "/api/workspace/download",
            params={"path": "missing.txt"},
        )
        directory = client.get(
            "/api/workspace/download",
            params={"path": "."},
        )
        oversized = client.get(
            "/api/workspace/download",
            params={"path": "large.txt"},
        )

    assert traversal.status_code == 400
    assert missing.status_code == 404
    assert directory.status_code == 404
    assert oversized.status_code == 413
    assert oversized.json()["detail"] == (
        "The requested workspace file exceeds the download limit."
    )


def test_workspace_endpoints_report_an_unavailable_capability(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), runtime_override=FakeRuntime())

    with TestClient(app) as client:
        listed = client.get("/api/workspace")
        opened = client.get("/api/workspace/file", params={"path": "report.md"})
        downloaded = client.get("/api/workspace/download", params={"path": "report.md"})

    assert listed.status_code == 503
    assert opened.status_code == 503
    assert downloaded.status_code == 503
    assert listed.json()["detail"] == "Workspace files are unavailable."
    assert opened.json()["detail"] == "Workspace files are unavailable."
    assert downloaded.json()["detail"] == "Workspace files are unavailable."


def test_chat_and_thread_state_contracts(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), runtime_override=FakeRuntime())
    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={"message": "Complete the brief", "user_id": "alice", "thread_id": "thread-a"},
        )
        thread = client.get("/api/threads/thread-a", params={"user_id": "alice"})
        invalid = client.post(
            "/api/chat",
            json={"message": "Task", "thread_id": "thread", "unexpected": True},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["artifacts"] == ["report.md"]
    assert thread.json()["values"]["user_id"] == "alice"
    assert invalid.status_code == 422


def test_chat_rejects_a_new_run_on_a_paused_thread(tmp_path: Path) -> None:
    class PausedRuntime(FakeRuntime):
        async def run(self, *, message: str, user_id: str, thread_id: str) -> RunResult:
            raise ThreadConflictError("The thread has a pending approval.")

    app = create_app(settings(tmp_path), runtime_override=PausedRuntime())
    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={"message": "Do not supersede", "user_id": "alice", "thread_id": "paused"},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["type"] == "ThreadConflictError"


def test_chat_and_resume_stream_are_typed_sse(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), runtime_override=FakeRuntime())
    with TestClient(app) as client:
        chat = client.post(
            "/api/chat/stream",
            json={"message": "Complete the brief", "user_id": "alice", "thread_id": "thread-a"},
        )
        resume = client.post(
            "/api/resume/stream",
            json={
                "user_id": "alice",
                "thread_id": "thread-a",
                "response": {
                    "interrupt_id": "interrupt-1",
                    "action": "approve",
                    "edited_arguments": None,
                },
            },
        )

    for response in (chat, resume):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "event: stage" in response.text
        assert "event: result" in response.text


def test_memory_endpoints_preserve_user_scope(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), runtime_override=FakeRuntime())
    with TestClient(app) as client:
        listed = client.get("/api/memories", params={"user_id": "local-user"})
        wrong_user_delete = client.delete(
            "/api/memories/memory-1", params={"user_id": "different-user"}
        )
        deleted = client.delete("/api/memories/memory-1", params={"user_id": "local-user"})
        cleared = client.delete("/api/memories", params={"user_id": "local-user"})

    assert listed.json()[0]["content"] == "Prefers concise reports"
    assert wrong_user_delete.status_code == 404
    assert deleted.status_code == 204
    assert cleared.json() == {"deleted": 0}


def test_resume_request_rejects_unknown_action(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), runtime_override=FakeRuntime())
    with TestClient(app) as client:
        response = client.post(
            "/api/resume",
            json={
                "user_id": "alice",
                "thread_id": "thread-a",
                "response": {"interrupt_id": "interrupt-1", "action": "execute"},
            },
        )

    assert response.status_code == 422


def test_run_result_can_represent_an_interrupt() -> None:
    result = RunResult(
        user_id="alice",
        thread_id="thread",
        status=RunStatus.INTERRUPTED,
        interrupt=ApprovalRequest(
            id="interrupt-1",
            action="execute_python",
            question="Approve code?",
            details={"code": "print(1)"},
        ),
    )

    assert result.interrupt is not None
    assert result.interrupt.action == "execute_python"
