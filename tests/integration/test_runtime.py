import asyncio
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from pydantic import ValidationError

from atlas_agent.config import Settings
from atlas_agent.memory import VectorMemory
from atlas_agent.runtime import AtlasRuntime, ThreadConflictError, open_runtime
from atlas_agent.schemas import (
    ApprovalResponse,
    MemoryExtraction,
    ReviewDecision,
    RunStatus,
    TaskPlan,
)
from atlas_agent.tools.registry import build_tool_bundle


class NoNetworkSearch:
    name = "offline"

    def search(self, query: str, *, max_results: int, topic: str) -> list[Any]:
        raise AssertionError("runtime test must not call the network")


class DeterministicBrain:
    def __init__(self) -> None:
        self.message_counts: list[int] = []

    async def plan(self, *, task: str, memories: list[str]) -> TaskPlan:
        return TaskPlan.model_validate(
            {
                "goal": task,
                "reasoning_summary": "One deterministic step is enough.",
                "steps": [
                    {
                        "id": "step_1",
                        "description": "Produce the response",
                        "success_criteria": "A verified answer exists",
                    }
                ],
            }
        )

    async def act(
        self,
        *,
        messages: list[AnyMessage],
        plan: list[dict[str, Any]],
        memories: list[str],
        review_feedback: str,
        allow_tools: bool,
    ) -> AIMessage:
        self.message_counts.append(len(messages))
        return AIMessage(content="Evidence-backed draft")

    async def review(
        self,
        *,
        task: str,
        plan: list[dict[str, Any]],
        messages: list[AnyMessage],
    ) -> ReviewDecision:
        return ReviewDecision(
            complete=True,
            rationale="The deterministic draft satisfies the task.",
            completed_step_ids=["step_1"],
        )

    async def finalize(
        self,
        *,
        task: str,
        plan: list[dict[str, Any]],
        messages: list[AnyMessage],
        review: dict[str, Any],
        sources: list[str],
        artifacts: list[str],
    ) -> AIMessage:
        return AIMessage(content=f"Verified: {task}")

    async def extract_memories(self, *, task: str, answer: str) -> MemoryExtraction:
        return MemoryExtraction(memories=[])


class ApprovalBrain(DeterministicBrain):
    async def act(
        self,
        *,
        messages: list[AnyMessage],
        plan: list[dict[str, Any]],
        memories: list[str],
        review_feedback: str,
        allow_tools: bool,
    ) -> AIMessage:
        self.message_counts.append(len(messages))
        if not any(isinstance(message, ToolMessage) for message in messages):
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "execute_python",
                        "args": {"code": "print(6 * 7)"},
                        "id": "code-approval-1",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="The approved tool call returned a bounded result.")


class ConcurrencyTrackingBrain(DeterministicBrain):
    def __init__(self) -> None:
        super().__init__()
        self.active_plans = 0
        self.max_active_plans = 0

    async def plan(self, *, task: str, memories: list[str]) -> TaskPlan:
        self.active_plans += 1
        self.max_active_plans = max(self.max_active_plans, self.active_plans)
        try:
            await asyncio.sleep(0.05)
            return await super().plan(task=task, memories=memories)
        finally:
            self.active_plans -= 1


def make_settings(tmp_path: Path, *, memory_enabled: bool = False) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        workspace_dir=tmp_path / "workspace",
        memory_enabled=memory_enabled,
        require_code_approval=False,
        require_overwrite_approval=False,
        code_execution_backend="disabled",
    )


async def test_runtime_runs_real_graph_with_sqlite_checkpoint(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    brain = DeterministicBrain()
    bundle = build_tool_bundle(settings, search_provider=NoNetworkSearch())

    async with open_runtime(settings, brain=brain, tool_bundle=bundle) as runtime:
        result = await runtime.run(
            message="Prepare the brief",
            user_id="alice",
            thread_id="thread-1",
        )
        snapshot = await runtime.state(user_id="alice", thread_id="thread-1")

    assert result.status == RunStatus.COMPLETED
    assert result.answer == "Verified: Prepare the brief"
    assert result.iterations == 1
    assert result.review_cycles == 1
    assert [step.id for step in result.plan] == ["step_1"]
    assert snapshot["next"] == []
    assert len(snapshot["values"]["messages"]) == 3
    assert settings.checkpoint_path.is_file()


async def test_runtime_opens_the_default_sqlite_vector_memory(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, memory_enabled=True)
    brain = DeterministicBrain()
    bundle = build_tool_bundle(settings, search_provider=NoNetworkSearch())

    async with open_runtime(settings, brain=brain, tool_bundle=bundle) as runtime:
        result = await runtime.run(
            message="Prepare a memory-enabled brief",
            user_id="alice",
            thread_id="thread-memory",
        )
        assert isinstance(runtime.memory, VectorMemory)

    assert result.status == RunStatus.COMPLETED
    assert (settings.vector_path / "atlas_memories.sqlite3").is_file()


async def test_same_thread_accumulates_conversation_but_users_are_isolated(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    brain = DeterministicBrain()
    bundle = build_tool_bundle(settings, search_provider=NoNetworkSearch())

    async with open_runtime(settings, brain=brain, tool_bundle=bundle) as runtime:
        await runtime.run(message="First turn", user_id="alice", thread_id="shared-name")
        await runtime.run(message="Second turn", user_id="alice", thread_id="shared-name")
        await runtime.run(message="Bob turn", user_id="bob", thread_id="shared-name")
        alice = await runtime.state(user_id="alice", thread_id="shared-name")
        bob = await runtime.state(user_id="bob", thread_id="shared-name")

    assert len(alice["values"]["messages"]) == 6
    assert len(bob["values"]["messages"]) == 3
    assert brain.message_counts == [1, 4, 1]


async def test_concurrent_same_thread_runs_are_serialized_without_lost_updates(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    brain = DeterministicBrain()
    bundle = build_tool_bundle(settings, search_provider=NoNetworkSearch())

    async with open_runtime(settings, brain=brain, tool_bundle=bundle) as runtime:
        first, second = await asyncio.gather(
            runtime.run(message="Concurrent first", user_id="alice", thread_id="shared"),
            runtime.run(message="Concurrent second", user_id="alice", thread_id="shared"),
        )
        snapshot = await runtime.state(user_id="alice", thread_id="shared")

    contents = [message["content"] for message in snapshot["values"]["messages"]]
    assert first.status == RunStatus.COMPLETED
    assert second.status == RunStatus.COMPLETED
    assert "Concurrent first" in contents
    assert "Concurrent second" in contents
    assert len(contents) == 6


async def test_two_runtime_instances_serialize_the_same_durable_thread(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    brain = ConcurrencyTrackingBrain()
    first_bundle = build_tool_bundle(settings, search_provider=NoNetworkSearch())
    second_bundle = build_tool_bundle(settings, search_provider=NoNetworkSearch())

    async with (
        open_runtime(
            settings,
            brain=brain,
            tool_bundle=first_bundle,
        ) as first_runtime,
        open_runtime(
            settings,
            brain=brain,
            tool_bundle=second_bundle,
        ) as second_runtime,
    ):
        first, second = await asyncio.gather(
            first_runtime.run(
                message="First process-shaped turn",
                user_id="alice",
                thread_id="cross-runtime",
            ),
            second_runtime.run(
                message="Second process-shaped turn",
                user_id="alice",
                thread_id="cross-runtime",
            ),
        )
        snapshot = await first_runtime.state(
            user_id="alice",
            thread_id="cross-runtime",
        )

    contents = [message["content"] for message in snapshot["values"]["messages"]]
    assert first.status == RunStatus.COMPLETED
    assert second.status == RunStatus.COMPLETED
    assert brain.max_active_plans == 1
    assert "First process-shaped turn" in contents
    assert "Second process-shaped turn" in contents
    assert len(contents) == 6


async def test_checkpoint_survives_runtime_restart(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    bundle = build_tool_bundle(settings, search_provider=NoNetworkSearch())
    async with open_runtime(
        settings,
        brain=DeterministicBrain(),
        tool_bundle=bundle,
    ) as runtime:
        await runtime.run(message="Persist me", user_id="alice", thread_id="restart")

    reopened_bundle = build_tool_bundle(settings, search_provider=NoNetworkSearch())
    async with open_runtime(
        settings,
        brain=DeterministicBrain(),
        tool_bundle=reopened_bundle,
    ) as reopened:
        snapshot = await reopened.state(user_id="alice", thread_id="restart")

    contents = [message["content"] for message in snapshot["values"]["messages"]]
    assert "Persist me" in contents
    assert "Verified: Persist me" in contents


async def test_runtime_entrypoints_enforce_public_input_bounds(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    bundle = build_tool_bundle(settings, search_provider=NoNetworkSearch())
    async with open_runtime(
        settings,
        brain=DeterministicBrain(),
        tool_bundle=bundle,
    ) as runtime:
        with pytest.raises(ValidationError):
            await runtime.run(
                message="x" * 20_001,
                user_id="alice",
                thread_id="bounded",
            )
        with pytest.raises(ValidationError):
            await runtime.resume(
                user_id="a" * 101,
                thread_id="bounded",
                response=ApprovalResponse(interrupt_id="not-pending", action="reject"),
            )
        with pytest.raises(ValidationError):
            await runtime.state(user_id="alice", thread_id=" ")
        stream_events = [
            event
            async for event in runtime.stream(
                message="x" * 20_001,
                user_id="alice",
                thread_id="bounded",
            )
        ]

    assert len(stream_events) == 1
    assert stream_events[0].event == "error"
    assert stream_events[0].data["type"] == "ValidationError"


async def test_stream_projects_graph_stages_and_final_result(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    bundle = build_tool_bundle(settings, search_provider=NoNetworkSearch())
    async with open_runtime(
        settings,
        brain=DeterministicBrain(),
        tool_bundle=bundle,
    ) as runtime:
        events = [
            event
            async for event in runtime.stream(
                message="Stream this task",
                user_id="alice",
                thread_id="stream",
            )
        ]

    stages = [event.data["stage"] for event in events if event.event == "stage"]
    assert stages == ["recall", "plan", "agent", "review", "finalize", "remember"]
    assert events[-1].event == "result"
    assert events[-1].data["answer"] == "Verified: Stream this task"


async def test_python_approval_interrupt_resumes_from_durable_checkpoint(tmp_path: Path) -> None:
    settings = make_settings(tmp_path).model_copy(
        update={
            "require_code_approval": True,
            "code_execution_backend": "docker",
        }
    )
    brain = ApprovalBrain()
    bundle = build_tool_bundle(settings, search_provider=NoNetworkSearch())

    async with open_runtime(settings, brain=brain, tool_bundle=bundle) as runtime:
        interrupted = await runtime.run(
            message="Calculate the answer with Python",
            user_id="alice",
            thread_id="approval",
        )
        paused_state = await runtime.state(user_id="alice", thread_id="approval")
        with pytest.raises(ThreadConflictError, match="pending approval"):
            await runtime.run(
                message="Do not supersede the pause",
                user_id="alice",
                thread_id="approval",
            )

    reopened_bundle = build_tool_bundle(settings, search_provider=NoNetworkSearch())
    async with open_runtime(
        settings,
        brain=ApprovalBrain(),
        tool_bundle=reopened_bundle,
    ) as reopened:
        completed = await reopened.resume(
            user_id="alice",
            thread_id="approval",
            response=ApprovalResponse(
                interrupt_id=interrupted.interrupt.id,
                action="approve",
            ),
        )
        with pytest.raises(ThreadConflictError, match="stale"):
            await reopened.resume(
                user_id="alice",
                thread_id="approval",
                response=ApprovalResponse(
                    interrupt_id=interrupted.interrupt.id,
                    action="approve",
                ),
            )

    assert interrupted.status == RunStatus.INTERRUPTED
    assert interrupted.interrupt is not None
    assert interrupted.interrupt.action == "execute_python"
    assert paused_state["next"] == ["tools"]
    assert completed.status == RunStatus.COMPLETED
    assert completed.answer == "Verified: Calculate the answer with Python"
    assert completed.iterations == 2


def test_public_result_marks_exhausted_incomplete_review_as_partial() -> None:
    result = AtlasRuntime._run_result(
        value={
            "review": {"complete": False, "missing_requirements": ["citation"]},
            "final_answer": "Best bounded answer with a disclosed limitation.",
            "plan": [],
        },
        interrupts=(),
        user_id="alice",
        thread_id="partial",
    )

    assert result.status == RunStatus.PARTIAL
    assert "limitation" in result.answer
