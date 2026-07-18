"""Deterministic, test-only FastAPI fixture for local browser proof.

This module intentionally avoids model providers, web requests, secrets, and code
execution. It exists only to exercise the packaged task workspace against stable API
contracts during local visual testing.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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

FIXTURE_ROOT = Path(
    "/tmp/atlas-agent-phase-2-browser-proof"  # noqa: S108 - test-only fixture root
)
FIXTURE_SOURCE = "https://docs.langchain.com/oss/python/langgraph/overview"
FIXTURE_ARTIFACT = "reports/deterministic-local-brief.md"
FIXTURE_TIME = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)

settings = Settings(
    _env_file=None,
    model="fixture:deterministic-local",
    custom_model_configured=True,
    data_dir=FIXTURE_ROOT / "data",
    workspace_dir=FIXTURE_ROOT / "workspace",
    memory_enabled=True,
    code_execution_backend="disabled",
)


class FixtureMemory:
    """Small mutable memory collection implementing the API's local contract."""

    def __init__(self) -> None:
        self._records = {
            "fixture-memory-1": MemoryRecord(
                id="fixture-memory-1",
                user_id="local-user",
                content=(
                    "Deterministic local fixture context: concise project summaries are preferred."
                ),
                category="preference",
                importance=4,
                source_thread="fixture-thread",
                created_at=FIXTURE_TIME,
            )
        }

    def list(self, *, user_id: str) -> list[MemoryRecord]:
        return [record for record in self._records.values() if record.user_id == user_id]

    def delete(self, *, user_id: str, memory_id: str) -> bool:
        record = self._records.get(memory_id)
        if record is None or record.user_id != user_id:
            return False
        del self._records[memory_id]
        return True

    def clear(self, *, user_id: str) -> int:
        matching_ids = [
            memory_id for memory_id, record in self._records.items() if record.user_id == user_id
        ]
        for memory_id in matching_ids:
            del self._records[memory_id]
        return len(matching_ids)


@dataclass(frozen=True)
class FixtureToolBundle:
    workspace: WorkspaceFiles


class FakeRuntime:
    """In-process deterministic runtime matching the production-facing API contract."""

    def __init__(self, active_settings: Settings) -> None:
        active_settings.ensure_directories()
        workspace = WorkspaceFiles(
            active_settings.workspace_dir,
            lock_dir=active_settings.file_lock_dir,
            max_file_bytes=active_settings.max_file_bytes,
            max_output_chars=active_settings.max_tool_output_chars,
        )
        self.tool_bundle = FixtureToolBundle(workspace=workspace)
        unsupported_preview = workspace.root / "fixtures" / "unsupported-preview.bin"
        unsupported_preview.parent.mkdir(parents=True, exist_ok=True)
        unsupported_preview.write_bytes(b"\xff\x00fixture")
        self.memory = FixtureMemory()
        self._results: dict[tuple[str, str], RunResult] = {}
        self._tasks: dict[tuple[str, str], str] = {}

    @staticmethod
    def _key(user_id: str, thread_id: str) -> tuple[str, str]:
        return user_id, thread_id

    @staticmethod
    def _plan() -> list[PlanStep]:
        return [
            PlanStep(
                id="inspect",
                description="Inspect the deterministic local brief",
                success_criteria="The fixture records a stable source reference",
                tool_hint="web_search",
            ),
            PlanStep(
                id="deliver",
                description="Create a browser-previewable project artifact",
                success_criteria="The fixture artifact is available in the workspace",
                tool_hint="write_file",
                depends_on=["inspect"],
            ),
        ]

    @staticmethod
    def _approval_id(user_id: str, thread_id: str) -> str:
        digest = hashlib.sha256(f"{user_id}\x00{thread_id}".encode()).hexdigest()[:16]
        return f"fixture-approval-{digest}"

    def _write_artifact(self) -> None:
        content = (
            "# Deterministic local fixture brief\n\n"
            "This file is fixture data generated for local browser proof.\n\n"
            f"Source: {FIXTURE_SOURCE}\n"
        )
        workspace = self.tool_bundle.workspace
        workspace.write(
            FIXTURE_ARTIFACT,
            content,
            overwrite=workspace.exists(FIXTURE_ARTIFACT),
        )

    def _completed_result(
        self,
        *,
        user_id: str,
        thread_id: str,
        approved: bool | None = None,
    ) -> RunResult:
        if approved is not False:
            self._write_artifact()
        if approved is False:
            answer = (
                "## Fixture result\n\n"
                "The approval was **not granted**, so the guarded fixture action did not run.\n\n"
                "- No file was created\n"
                "- The saved decision remains deterministic\n\n"
                "Status: `not allowed`"
            )
            artifacts: list[str] = []
        else:
            answer = (
                "## Fixture result\n\n"
                "The **sample brief** was completed from a fixed source.\n\n"
                "- Reviewed the deterministic source\n"
                "- Saved a workspace artifact\n\n"
                f"Read the [LangGraph overview]({FIXTURE_SOURCE}).\n\n"
                f"Created `{FIXTURE_ARTIFACT}`.\n\n"
                "```text\nstatus: complete\n```"
            )
            artifacts = [FIXTURE_ARTIFACT]
        return RunResult(
            user_id=user_id,
            thread_id=thread_id,
            status=RunStatus.COMPLETED,
            answer=answer,
            plan=self._plan(),
            sources=[FIXTURE_SOURCE],
            artifacts=artifacts,
            iterations=2,
            review_cycles=1,
        )

    async def run(self, *, message: str, user_id: str, thread_id: str) -> RunResult:
        key = self._key(user_id, thread_id)
        self._tasks[key] = message
        if "approval" in message.casefold():
            result = RunResult(
                user_id=user_id,
                thread_id=thread_id,
                status=RunStatus.INTERRUPTED,
                plan=self._plan(),
                iterations=1,
                interrupt=ApprovalRequest(
                    id=self._approval_id(user_id, thread_id),
                    action="write_fixture_artifact",
                    question="Approve creating the deterministic local fixture artifact?",
                    details={
                        "path": FIXTURE_ARTIFACT,
                        "fixture_only": True,
                    },
                ),
            )
        else:
            result = self._completed_result(user_id=user_id, thread_id=thread_id)
        self._results[key] = result
        return result

    async def resume(
        self,
        *,
        user_id: str,
        thread_id: str,
        response: ApprovalResponse,
    ) -> RunResult:
        key = self._key(user_id, thread_id)
        current = self._results.get(key)
        if (
            current is None
            or current.interrupt is None
            or current.interrupt.id != response.interrupt_id
        ):
            raise ThreadConflictError("The deterministic fixture approval is stale.")
        result = self._completed_result(
            user_id=user_id,
            thread_id=thread_id,
            approved=response.action == "approve",
        )
        self._results[key] = result
        return result

    async def stream(
        self,
        *,
        message: str | None,
        user_id: str,
        thread_id: str,
        response: ApprovalResponse | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if response is not None:
            yield StreamEvent(
                event="stage",
                data={"stage": "resume", "message": "Applying the fixture approval decision"},
            )
            result = await self.resume(
                user_id=user_id,
                thread_id=thread_id,
                response=response,
            )
        elif message is not None:
            for stage, stage_message in (
                ("recall", "Loading deterministic saved context"),
                ("plan", "Preparing the fixed local execution plan"),
                ("agent", "Producing deterministic fixture output"),
            ):
                yield StreamEvent(
                    event="stage",
                    data={"stage": stage, "message": stage_message},
                )
            if "truncated stream" in message.casefold():
                return
            result = await self.run(
                message=message,
                user_id=user_id,
                thread_id=thread_id,
            )
        else:
            raise ValueError("message or approval response is required")

        if result.interrupt is not None:
            yield StreamEvent(
                event="interrupt",
                data=result.interrupt.model_dump(mode="json"),
            )
        elif result.answer:
            yield StreamEvent(event="token", data={"content": result.answer})
        yield StreamEvent(event="result", data=result.model_dump(mode="json"))

    async def state(self, *, user_id: str, thread_id: str) -> dict[str, object]:
        key = self._key(user_id, thread_id)
        result = self._results.get(key)
        if result is None:
            return {
                "values": {},
                "next": [],
                "checkpoint_created_at": None,
                "interrupts": [],
            }
        task = self._tasks.get(key, "Deterministic local fixture task")
        values = {
            "task": task,
            "user_id": user_id,
            "public_thread_id": thread_id,
            "plan": [step.model_dump(mode="json") for step in result.plan],
            "final_answer": result.answer,
            "sources": result.sources,
            "artifacts": result.artifacts,
            "agent_iterations": result.iterations,
            "review_cycles": result.review_cycles,
            "review": {"complete": result.status == RunStatus.COMPLETED},
            "messages": [
                {"type": "human", "content": task, "name": None},
                {"type": "ai", "content": result.answer, "name": None},
            ],
        }
        interrupts = (
            [result.interrupt.model_dump(mode="json")] if result.interrupt is not None else []
        )
        return {
            "values": values,
            "next": ["tools"] if interrupts else [],
            "checkpoint_created_at": FIXTURE_TIME.isoformat(),
            "interrupts": interrupts,
        }

    @staticmethod
    def graph_mermaid() -> str:
        return "graph TD; recall-->plan; plan-->agent; agent-->review; review-->finalize;"


fixture_runtime = FakeRuntime(settings)
app = create_app(settings, runtime_override=fixture_runtime)
