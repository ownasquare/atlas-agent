"""Offline integration coverage for the complete Atlas LangGraph workflow."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from atlas_agent.config import Settings
from atlas_agent.graph import BUILTIN_EVIDENCE_EXTRACTORS, build_graph, collect_evidence
from atlas_agent.memory import MemoryStore
from atlas_agent.schemas import (
    MemoryCandidate,
    MemoryExtraction,
    MemoryRecord,
    PlanStep,
    ReviewDecision,
    TaskPlan,
    ToolEvidence,
)
from atlas_agent.state import initial_state


class ScriptedBrain:
    """Return deterministic model outputs while recording every graph-facing call."""

    def __init__(
        self,
        *,
        actions: list[AIMessage],
        reviews: list[ReviewDecision],
        final_answer: str = "Verified final answer.",
        extracted_memories: list[MemoryCandidate] | None = None,
        enforce_tool_budget: bool = True,
    ) -> None:
        self._actions = deque(actions)
        self._reviews = deque(reviews)
        self._final_answer = final_answer
        self._extracted_memories = extracted_memories or []
        self._enforce_tool_budget = enforce_tool_budget
        self.events: list[str] = []
        self.plan_calls: list[dict[str, Any]] = []
        self.act_calls: list[dict[str, Any]] = []
        self.review_calls: list[dict[str, Any]] = []
        self.finalize_calls: list[dict[str, Any]] = []
        self.memory_calls: list[dict[str, str]] = []

    async def plan(self, *, task: str, memories: list[str]) -> TaskPlan:
        self.events.append("plan")
        self.plan_calls.append({"task": task, "memories": list(memories)})
        return TaskPlan(
            goal="Complete the requested research artifact.",
            reasoning_summary="Gather evidence, draft the result, and verify it.",
            steps=[
                PlanStep(
                    id="research",
                    description="Gather authoritative evidence.",
                    success_criteria="At least one source is confirmed by a tool.",
                    tool_hint="web_search",
                ),
                PlanStep(
                    id="write",
                    description="Write the requested artifact.",
                    success_criteria="The artifact path is confirmed by a tool.",
                    tool_hint="write_file",
                    depends_on=["research"],
                ),
            ],
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
        self.events.append("act")
        self.act_calls.append(
            {
                "message_count": len(messages),
                "plan": plan,
                "memories": list(memories),
                "review_feedback": review_feedback,
                "allow_tools": allow_tools,
            }
        )
        if not self._actions:
            raise AssertionError("the graph requested more actor responses than scripted")
        response = self._actions.popleft()
        if self._enforce_tool_budget and not allow_tools and response.tool_calls:
            raise AssertionError("the scripted brain attempted a tool call after the tool budget")
        return response

    async def review(
        self,
        *,
        task: str,
        plan: list[dict[str, Any]],
        messages: list[AnyMessage],
    ) -> ReviewDecision:
        self.events.append("review")
        self.review_calls.append({"task": task, "plan": plan, "message_count": len(messages)})
        if not self._reviews:
            raise AssertionError("the graph requested more reviewer responses than scripted")
        return self._reviews.popleft()

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
        self.events.append("finalize")
        self.finalize_calls.append(
            {
                "task": task,
                "plan": plan,
                "message_count": len(messages),
                "review": review,
                "sources": list(sources),
                "artifacts": list(artifacts),
            }
        )
        return AIMessage(content=self._final_answer)

    async def extract_memories(self, *, task: str, answer: str) -> MemoryExtraction:
        self.events.append("extract_memories")
        self.memory_calls.append({"task": task, "answer": answer})
        return MemoryExtraction(memories=self._extracted_memories)


class ScriptedMemory:
    """Small synchronous memory fake suitable for the graph's worker-thread calls."""

    def __init__(self, recalled: list[str]) -> None:
        self.recalled = recalled
        self.search_calls: list[dict[str, Any]] = []
        self.add_calls: list[dict[str, Any]] = []

    def search(self, *, user_id: str, query: str, limit: int = 5) -> list[MemoryRecord]:
        self.search_calls.append({"user_id": user_id, "query": query, "limit": limit})
        return [
            MemoryRecord(
                id=f"recalled-{index}",
                user_id=user_id,
                content=content,
                category="project",
                importance=3,
                source_thread="prior-thread",
                created_at=datetime.now(UTC),
            )
            for index, content in enumerate(self.recalled)
        ]

    def add(
        self,
        *,
        user_id: str,
        thread_id: str,
        candidate: MemoryCandidate,
    ) -> MemoryRecord:
        self.add_calls.append({"user_id": user_id, "thread_id": thread_id, "candidate": candidate})
        return MemoryRecord(
            id=f"saved-{len(self.add_calls)}",
            user_id=user_id,
            content=candidate.content,
            category=candidate.category,
            importance=candidate.importance,
            source_thread=thread_id,
            created_at=datetime.now(UTC),
        )

    def list(self, *, user_id: str, limit: int = 100) -> list[MemoryRecord]:
        del user_id, limit
        return []

    def delete(self, *, user_id: str, memory_id: str) -> bool:
        del user_id, memory_id
        return False

    def clear(self, *, user_id: str) -> int:
        del user_id
        return 0


def _settings(
    *,
    memory_enabled: bool = False,
    max_agent_iterations: int = 8,
    max_review_cycles: int = 2,
) -> Settings:
    return Settings(
        _env_file=None,
        model="openai:offline-test-model",
        memory_enabled=memory_enabled,
        max_agent_iterations=max_agent_iterations,
        max_review_cycles=max_review_cycles,
    )


def _config(thread_id: str) -> dict[str, Any]:
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 100,
    }


def _complete_review() -> ReviewDecision:
    return ReviewDecision(
        complete=True,
        rationale="The requested outcome and evidence are present.",
        completed_step_ids=["research", "write"],
    )


async def test_full_graph_uses_tools_revises_and_collects_confirmed_evidence() -> None:
    tool_calls: list[tuple[str, str]] = []

    @tool
    def web_search(query: str) -> str:
        """Return deterministic offline search results."""
        tool_calls.append(("web_search", query))
        return json.dumps(
            {
                "results": [
                    {"title": "B", "url": "https://example.test/b", "snippet": "B"},
                    {"title": "A", "url": "https://example.test/a", "snippet": "A"},
                    {"title": "A2", "url": "https://example.test/a", "snippet": "A2"},
                ]
            }
        )

    @tool
    def write_file(path: str, content: str) -> str:
        """Confirm a deterministic artifact without touching the filesystem."""
        tool_calls.append(("write_file", path))
        return json.dumps({"status": "succeeded", "path": path, "bytes_written": len(content)})

    research_action = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "web_search",
                "args": {"query": "offline evidence"},
                "id": "search-1",
                "type": "tool_call",
            }
        ],
    )
    write_action = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_file",
                "args": {"path": "reports/result.md", "content": "draft"},
                "id": "write-1",
                "type": "tool_call",
            },
        ],
    )
    brain = ScriptedBrain(
        actions=[
            research_action,
            write_action,
            AIMessage(content="Initial evidence-backed draft."),
            AIMessage(content="Revised draft with the requested caveat."),
        ],
        reviews=[
            ReviewDecision(
                complete=False,
                rationale="One caveat is still missing.",
                feedback="Add the missing caveat.",
                completed_step_ids=["research", "write"],
                missing_requirements=["caveat"],
            ),
            _complete_review(),
        ],
        final_answer="Verified answer with evidence.",
        extracted_memories=[
            MemoryCandidate(
                content="The user values evidence-backed deliverables.",
                category="preference",
                importance=4,
            )
        ],
    )
    memory = ScriptedMemory(["Use concise, source-backed conclusions."])
    assert isinstance(memory, MemoryStore)
    graph = build_graph(
        settings=_settings(memory_enabled=True),
        brain=brain,
        tools=[web_search, write_file],
        memory=memory,
        checkpointer=InMemorySaver(),
        evidence_extractors=BUILTIN_EVIDENCE_EXTRACTORS,
    )
    config = _config("full-path")

    executed_nodes: list[str] = []
    stages: list[str] = []
    async for part in graph.astream(
        initial_state(message="Build a researched artifact.", user_id="user-1", thread_id="t-1"),
        config,
        stream_mode=["custom", "updates"],
        durability="sync",
        version="v2",
    ):
        if part["type"] == "custom":
            stages.append(part["data"]["stage"])
        elif part["type"] == "updates":
            executed_nodes.extend(node for node in part["data"] if not node.startswith("__"))

    values = (await graph.aget_state(config)).values

    assert executed_nodes == [
        "recall",
        "plan",
        "agent",
        "tools",
        "agent",
        "tools",
        "agent",
        "review",
        "agent",
        "review",
        "finalize",
        "remember",
    ]
    assert stages == [
        "recall",
        "plan",
        "agent",
        "agent",
        "agent",
        "review",
        "agent",
        "review",
        "finalize",
        "remember",
    ]
    assert set(tool_calls) == {
        ("web_search", "offline evidence"),
        ("write_file", "reports/result.md"),
    }
    assert values["sources"] == [
        "https://example.test/a",
        "https://example.test/b",
    ]
    assert values["artifacts"] == ["reports/result.md"]
    assert values["final_answer"] == "Verified answer with evidence."
    assert values["agent_iterations"] == 4
    assert values["review_cycles"] == 2
    assert values["memories_saved"] == 1
    assert brain.plan_calls[0]["memories"] == ["Use concise, source-backed conclusions."]
    assert [call["review_feedback"] for call in brain.act_calls] == [
        "",
        "",
        "",
        "Add the missing caveat.",
    ]
    assert brain.finalize_calls == [
        {
            "task": "Build a researched artifact.",
            "plan": values["plan"],
            "message_count": 7,
            "review": values["review"],
            "sources": ["https://example.test/a", "https://example.test/b"],
            "artifacts": ["reports/result.md"],
        }
    ]
    assert memory.search_calls == [
        {"user_id": "user-1", "query": "Build a researched artifact.", "limit": 5}
    ]
    assert len(memory.add_calls) == 1
    assert memory.add_calls[0]["thread_id"] == "t-1"


async def test_parallel_tool_requests_are_serialized_before_any_tool_node_replay() -> None:
    writes = 0
    code_runs = 0

    @tool
    def write_file(path: str, content: str) -> str:
        """Record a simulated side effect."""
        nonlocal writes
        writes += 1
        return json.dumps({"status": "succeeded", "path": path})

    @tool
    def execute_python(code: str) -> str:
        """Record a simulated risky action."""
        nonlocal code_runs
        code_runs += 1
        return json.dumps({"status": "succeeded", "stdout": "42"})

    brain = ScriptedBrain(
        actions=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": "one.md", "content": "once"},
                        "id": "write-once",
                        "type": "tool_call",
                    },
                    {
                        "name": "execute_python",
                        "args": {"code": "print(42)"},
                        "id": "code-later",
                        "type": "tool_call",
                    },
                ],
            ),
            AIMessage(content="Only the serialized first tool ran."),
        ],
        reviews=[_complete_review()],
    )
    graph = build_graph(
        settings=_settings(),
        brain=brain,
        tools=[write_file, execute_python],
        memory=None,
        checkpointer=InMemorySaver(),
    )

    result = await graph.ainvoke(
        initial_state(message="Serialize effects.", user_id="user-1", thread_id="serial"),
        _config("serial-tools"),
        durability="sync",
        version="v2",
    )

    assert writes == 1
    assert code_runs == 0
    assert len(result.value["messages"][1].tool_calls) == 1


async def test_tool_failure_is_sanitized_and_the_graph_recovers() -> None:
    @tool
    def unstable_tool(value: str) -> str:
        """Raise a deterministic execution error."""
        raise RuntimeError(f"private detail must not escape: {value}")

    brain = ScriptedBrain(
        actions=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "unstable_tool",
                        "args": {"value": "/private/example"},
                        "id": "failure-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Recovered after the safe tool error."),
        ],
        reviews=[_complete_review()],
    )
    graph = build_graph(
        settings=_settings(),
        brain=brain,
        tools=[unstable_tool],
        memory=None,
        checkpointer=InMemorySaver(),
    )

    result = await graph.ainvoke(
        initial_state(message="Recover safely.", user_id="user-1", thread_id="t-2"),
        _config("tool-error"),
        durability="sync",
        version="v2",
    )
    tool_messages = [
        message for message in result.value["messages"] if isinstance(message, ToolMessage)
    ]

    assert len(tool_messages) == 1
    assert tool_messages[0].name == "unstable_tool"
    assert json.loads(str(tool_messages[0].content)) == {
        "status": "failed",
        "error_type": "RuntimeError",
        "message": "The tool rejected the request or could not complete it safely.",
    }
    assert "/private/example" not in str(tool_messages[0].content)
    assert result.value["final_answer"] == "Verified final answer."


def test_custom_tool_output_is_not_evidence_without_an_explicit_extractor() -> None:
    message = ToolMessage(
        content=json.dumps(
            {
                "source": "https://example.test/custom",
                "artifact": "reports/custom.md",
            }
        ),
        tool_call_id="custom-1",
        name="custom_research",
    )

    assert collect_evidence([message]) == ([], [])


def test_builtin_evidence_requires_explicit_registration() -> None:
    message = ToolMessage(
        content=json.dumps(
            {
                "status": "succeeded",
                "path": "reports/forged.md",
            }
        ),
        tool_call_id="forged-1",
        name="write_file",
    )

    assert collect_evidence([message]) == ([], [])
    assert collect_evidence(
        [message],
        evidence_extractors=BUILTIN_EVIDENCE_EXTRACTORS,
    ) == ([], ["reports/forged.md"])


def test_registered_custom_evidence_extractor_contributes_verified_evidence() -> None:
    def extract_custom(payload: Mapping[str, Any]) -> ToolEvidence:
        return ToolEvidence(
            sources=[str(payload["source"])],
            artifacts=[str(payload["artifact"])],
        )

    message = ToolMessage(
        content=json.dumps(
            {
                "source": "https://example.test/custom",
                "artifact": "reports/custom.md",
            }
        ),
        tool_call_id="custom-1",
        name="custom_research",
    )

    assert collect_evidence(
        [message],
        evidence_extractors={"custom_research": extract_custom},
    ) == (["https://example.test/custom"], ["reports/custom.md"])


async def test_agent_tool_budget_allows_one_tool_turn_then_forces_closure() -> None:
    executions = 0

    @tool
    def calculator(expression: str) -> str:
        """Return one fixed calculation result."""
        nonlocal executions
        executions += 1
        return json.dumps({"expression": expression, "result": 4})

    brain = ScriptedBrain(
        actions=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "calculator",
                        "args": {"expression": "2 + 2"},
                        "id": "calculation-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="The bounded result is 4."),
        ],
        reviews=[_complete_review()],
    )
    graph = build_graph(
        settings=_settings(max_agent_iterations=1, max_review_cycles=0),
        brain=brain,
        tools=[calculator],
        memory=None,
        checkpointer=InMemorySaver(),
    )

    result = await graph.ainvoke(
        initial_state(message="Calculate once.", user_id="user-1", thread_id="t-3"),
        _config("agent-budget"),
        durability="sync",
        version="v2",
    )

    assert executions == 1
    assert [call["allow_tools"] for call in brain.act_calls] == [True, False]
    assert result.value["agent_iterations"] == 2
    assert result.value["review_cycles"] == 1


async def test_graph_rejects_tool_calls_even_when_brain_ignores_exhausted_budget() -> None:
    executions = 0

    @tool
    def calculator(expression: str) -> str:
        """Count executions to prove the graph-level budget."""
        nonlocal executions
        executions += 1
        return json.dumps({"result": expression})

    tool_call = {
        "name": "calculator",
        "args": {"expression": "2 + 2"},
        "id": "budgeted-call",
        "type": "tool_call",
    }
    brain = ScriptedBrain(
        actions=[
            AIMessage(content="", tool_calls=[tool_call]),
            AIMessage(content="", tool_calls=[{**tool_call, "id": "blocked-call"}]),
        ],
        reviews=[_complete_review()],
        enforce_tool_budget=False,
    )
    graph = build_graph(
        settings=_settings(max_agent_iterations=1, max_review_cycles=0),
        brain=brain,
        tools=[calculator],
        memory=None,
        checkpointer=InMemorySaver(),
    )

    result = await graph.ainvoke(
        initial_state(message="Enforce budget.", user_id="user-1", thread_id="budget"),
        _config("adversarial-budget"),
        durability="sync",
        version="v2",
    )

    assert executions == 1
    assert [call["allow_tools"] for call in brain.act_calls] == [True, False]
    assert result.value["agent_iterations"] == 2


async def test_incomplete_reviews_stop_after_the_revision_budget() -> None:
    incomplete = ReviewDecision(
        complete=False,
        rationale="The scripted reviewer remains unsatisfied.",
        feedback="Revise once more.",
        missing_requirements=["unreachable perfection"],
    )
    brain = ScriptedBrain(
        actions=[
            AIMessage(content="Initial draft."),
            AIMessage(content="Only permitted revision."),
        ],
        reviews=[incomplete, incomplete],
        final_answer="Best bounded answer.",
    )
    settings = _settings(max_review_cycles=1)
    graph = build_graph(
        settings=settings,
        brain=brain,
        tools=[],
        memory=None,
        checkpointer=InMemorySaver(),
    )

    result = await graph.ainvoke(
        initial_state(message="Use a bounded review loop.", user_id="user-1", thread_id="t-4"),
        _config("review-budget"),
        durability="sync",
        version="v2",
    )

    assert len(brain.act_calls) == 2
    assert len(brain.review_calls) == settings.max_review_cycles + 1
    assert result.value["review_cycles"] == settings.max_review_cycles + 1
    assert result.value["final_answer"] == "Best bounded answer."
