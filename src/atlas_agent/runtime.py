"""Shared durable runtime used by the CLI, API, and tests."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from filelock import FileLock
from filelock import Timeout as FileLockTimeout
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from atlas_agent.brain import AgentBrain, LangChainBrain
from atlas_agent.config import Settings, get_settings
from atlas_agent.graph import build_graph
from atlas_agent.memory import MemoryStore, VectorMemory
from atlas_agent.schemas import (
    ApprovalRequest,
    ApprovalResponse,
    ChatRequest,
    PlanStep,
    ResumeRequest,
    RunResult,
    RunStatus,
    StreamEvent,
    ThreadIdentity,
)
from atlas_agent.state import AgentState, initial_state
from atlas_agent.tools.registry import ToolBundle, build_tool_bundle


class ThreadConflictError(RuntimeError):
    """A thread is paused, busy, or no longer matches the supplied approval nonce."""


class AtlasRuntime:
    """Project LangGraph internals into a stable application-facing API."""

    def __init__(
        self,
        *,
        settings: Settings,
        graph: CompiledStateGraph[AgentState, None, AgentState, AgentState],
        memory: MemoryStore | None,
        tool_bundle: ToolBundle,
    ) -> None:
        self.settings = settings
        self.graph = graph
        self.memory = memory
        self.tool_bundle = tool_bundle
        self._thread_locks: dict[str, asyncio.Lock] = {}

    def _thread_lock(self, *, user_id: str, thread_id: str) -> asyncio.Lock:
        key = self.settings.checkpoint_thread_id(user_id, thread_id)
        return self._thread_locks.setdefault(key, asyncio.Lock())

    @asynccontextmanager
    async def _thread_guard(self, *, user_id: str, thread_id: str) -> AsyncIterator[None]:
        """Serialize one thread across coroutines and runtime processes sharing data_dir."""
        async with self._thread_lock(user_id=user_id, thread_id=thread_id):
            process_lock = FileLock(self.settings.thread_lock_path(user_id, thread_id))
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.settings.thread_lock_timeout_seconds
            while True:
                try:
                    process_lock.acquire(timeout=0)
                    break
                except FileLockTimeout as exc:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise ThreadConflictError(
                            "Another runtime is already working on this thread."
                        ) from exc
                    await asyncio.sleep(min(0.05, remaining))
            try:
                yield
            finally:
                process_lock.release()

    @staticmethod
    def _snapshot_interrupts(snapshot: Any) -> tuple[Any, ...]:
        return tuple(
            interrupt_item
            for task in snapshot.tasks
            for interrupt_item in getattr(task, "interrupts", ())
        )

    @classmethod
    def _interrupt_payloads(cls, snapshot: Any) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for interrupt_item in cls._snapshot_interrupts(snapshot):
            if isinstance(interrupt_item.value, dict):
                payloads.append({"id": interrupt_item.id, **interrupt_item.value})
        return payloads

    async def _assert_thread_can_start(self, config: RunnableConfig) -> None:
        snapshot = await self.graph.aget_state(config)
        if self._snapshot_interrupts(snapshot):
            raise ThreadConflictError(
                "The thread has a pending approval and must be resumed before a new run."
            )

    async def _resume_command(
        self,
        *,
        config: RunnableConfig,
        response: ApprovalResponse,
    ) -> Command[Any]:
        snapshot = await self.graph.aget_state(config)
        interrupts = self._snapshot_interrupts(snapshot)
        if len(interrupts) != 1 or interrupts[0].id != response.interrupt_id:
            raise ThreadConflictError("The approval is stale or no longer pending for this thread.")
        decision = response.model_dump(mode="json", exclude={"interrupt_id"})
        return Command(resume={response.interrupt_id: decision})

    def config(self, *, user_id: str, thread_id: str) -> RunnableConfig:
        identity = ThreadIdentity(user_id=user_id, thread_id=thread_id)
        return {
            "configurable": {
                "thread_id": self.settings.checkpoint_thread_id(
                    identity.user_id,
                    identity.thread_id,
                ),
            },
            "recursion_limit": (
                self.settings.max_agent_iterations + self.settings.max_review_cycles + 5
            )
            * 3,
            "tags": ["atlas-agent"],
            "metadata": {
                "user_id": identity.user_id,
                "public_thread_id": identity.thread_id,
            },
        }

    async def run(self, *, message: str, user_id: str, thread_id: str) -> RunResult:
        request = ChatRequest(message=message, user_id=user_id, thread_id=thread_id)
        config = self.config(user_id=request.user_id, thread_id=request.thread_id)
        async with self._thread_guard(user_id=request.user_id, thread_id=request.thread_id):
            await self._assert_thread_can_start(config)
            output = await self.graph.ainvoke(
                initial_state(
                    message=request.message,
                    user_id=request.user_id,
                    thread_id=request.thread_id,
                ),
                config,
                durability="sync",
                version="v2",
            )
        return self._run_result(
            value=cast(dict[str, Any], output.value),
            interrupts=output.interrupts,
            user_id=request.user_id,
            thread_id=request.thread_id,
        )

    async def resume(
        self,
        *,
        user_id: str,
        thread_id: str,
        response: ApprovalResponse,
    ) -> RunResult:
        request = ResumeRequest(user_id=user_id, thread_id=thread_id, response=response)
        config = self.config(user_id=request.user_id, thread_id=request.thread_id)
        async with self._thread_guard(user_id=request.user_id, thread_id=request.thread_id):
            command = await self._resume_command(config=config, response=request.response)
            output = await self.graph.ainvoke(
                command,
                config,
                durability="sync",
                version="v2",
            )
        return self._run_result(
            value=cast(dict[str, Any], output.value),
            interrupts=output.interrupts,
            user_id=request.user_id,
            thread_id=request.thread_id,
        )

    async def stream(
        self,
        *,
        message: str | None,
        user_id: str,
        thread_id: str,
        response: ApprovalResponse | None = None,
    ) -> AsyncIterator[StreamEvent]:
        try:
            graph_input: AgentState | Command[Any]
            validated_message: str | None = None
            validated_response: ApprovalResponse | None = None
            if response is not None:
                resume_request = ResumeRequest(
                    user_id=user_id,
                    thread_id=thread_id,
                    response=response,
                )
                validated_user_id = resume_request.user_id
                validated_thread_id = resume_request.thread_id
                validated_response = resume_request.response
            elif message is not None:
                chat_request = ChatRequest(
                    message=message,
                    user_id=user_id,
                    thread_id=thread_id,
                )
                validated_user_id = chat_request.user_id
                validated_thread_id = chat_request.thread_id
                validated_message = chat_request.message
            else:
                raise ValueError("message or approval response is required")
            config = self.config(
                user_id=validated_user_id,
                thread_id=validated_thread_id,
            )
            async with self._thread_guard(
                user_id=validated_user_id,
                thread_id=validated_thread_id,
            ):
                if validated_response is not None:
                    graph_input = await self._resume_command(
                        config=config,
                        response=validated_response,
                    )
                else:
                    if validated_message is None:  # pragma: no cover - validated above
                        raise ValueError("validated message is required")
                    await self._assert_thread_can_start(config)
                    graph_input = initial_state(
                        message=validated_message,
                        user_id=validated_user_id,
                        thread_id=validated_thread_id,
                    )
                async for part in self.graph.astream(
                    graph_input,
                    config,
                    stream_mode=["custom", "messages"],
                    durability="sync",
                    version="v2",
                ):
                    if part["type"] == "custom" and isinstance(part["data"], dict):
                        yield StreamEvent(event="stage", data=part["data"])
                    elif part["type"] == "messages":
                        message_chunk, metadata = part["data"]
                        if metadata.get("langgraph_node") != "finalize":
                            continue
                        content = getattr(message_chunk, "content", "")
                        if isinstance(content, str) and content:
                            yield StreamEvent(event="token", data={"content": content})
                snapshot = await self.graph.aget_state(config)
                interrupts = self._snapshot_interrupts(snapshot)
                result = self._run_result(
                    value=snapshot.values,
                    interrupts=interrupts,
                    user_id=validated_user_id,
                    thread_id=validated_thread_id,
                )
                if result.interrupt is not None:
                    yield StreamEvent(
                        event="interrupt",
                        data=result.interrupt.model_dump(mode="json"),
                    )
                yield StreamEvent(event="result", data=result.model_dump(mode="json"))
        except Exception as exc:
            yield StreamEvent(
                event="error",
                data={"type": type(exc).__name__, "message": "The agent run failed safely."},
            )
            return

    async def state(self, *, user_id: str, thread_id: str) -> dict[str, Any]:
        identity = ThreadIdentity(user_id=user_id, thread_id=thread_id)
        async with self._thread_guard(
            user_id=identity.user_id,
            thread_id=identity.thread_id,
        ):
            snapshot = await self.graph.aget_state(
                self.config(user_id=identity.user_id, thread_id=identity.thread_id)
            )
        return {
            "values": self._json_safe_state(snapshot.values),
            "next": list(snapshot.next),
            "checkpoint_created_at": snapshot.created_at,
            "interrupts": self._interrupt_payloads(snapshot),
        }

    def graph_mermaid(self) -> str:
        return self.graph.get_graph().draw_mermaid()

    @staticmethod
    def _json_safe_state(value: dict[str, Any]) -> dict[str, Any]:
        safe = dict(value)
        safe["messages"] = [
            {
                "type": message.type,
                "content": message.content,
                "name": getattr(message, "name", None),
            }
            for message in value.get("messages", [])
        ]
        return safe

    @staticmethod
    def _run_result(
        *,
        value: dict[str, Any],
        interrupts: tuple[Any, ...],
        user_id: str,
        thread_id: str,
    ) -> RunResult:
        interrupt_request: ApprovalRequest | None = None
        if interrupts:
            raw_value = interrupts[0].value
            if isinstance(raw_value, dict):
                interrupt_request = ApprovalRequest.model_validate(
                    {"id": interrupts[0].id, **raw_value}
                )
        plan = [PlanStep.model_validate(step) for step in value.get("plan", [])]
        if interrupt_request is not None:
            run_status = RunStatus.INTERRUPTED
        elif bool(value.get("review", {}).get("complete")):
            run_status = RunStatus.COMPLETED
        else:
            run_status = RunStatus.PARTIAL
        return RunResult(
            user_id=user_id,
            thread_id=thread_id,
            status=run_status,
            answer=str(value.get("final_answer", "")),
            plan=plan,
            sources=list(value.get("sources", [])),
            artifacts=list(value.get("artifacts", [])),
            iterations=int(value.get("agent_iterations", 0)),
            review_cycles=int(value.get("review_cycles", 0)),
            interrupt=interrupt_request,
        )


@asynccontextmanager
async def open_runtime(
    settings: Settings | None = None,
    *,
    brain: AgentBrain | None = None,
    memory: MemoryStore | None = None,
    tool_bundle: ToolBundle | None = None,
) -> AsyncIterator[AtlasRuntime]:
    """Own every persistent resource for exactly as long as the runtime is used."""
    active_settings = settings or get_settings()
    active_settings.ensure_directories()
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    active_tools = tool_bundle or build_tool_bundle(active_settings)
    active_memory = memory
    if active_memory is None and active_settings.memory_enabled:
        active_memory = VectorMemory(
            active_settings.vector_path,
            collection_name=active_settings.memory_collection,
        )
    active_brain = brain or LangChainBrain(active_settings, active_tools.tools)
    async with AsyncSqliteSaver.from_conn_string(str(active_settings.checkpoint_path)) as saver:
        await saver.setup()
        graph = build_graph(
            settings=active_settings,
            brain=active_brain,
            tools=active_tools.tools,
            memory=active_memory,
            checkpointer=saver,
            evidence_extractors=active_tools.evidence_extractors,
        )
        yield AtlasRuntime(
            settings=active_settings,
            graph=graph,
            memory=active_memory,
            tool_bundle=active_tools,
        )
