"""The explicit LangGraph planner, tool loop, verifier, and memory workflow."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Literal

from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from atlas_agent.brain import AgentBrain
from atlas_agent.config import Settings
from atlas_agent.memory import MemoryStore
from atlas_agent.schemas import EvidenceExtractor, ReviewDecision, ToolEvidence
from atlas_agent.state import AgentState

logger = logging.getLogger(__name__)


def _content_text(message: AnyMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return json.dumps(message.content, ensure_ascii=False)


def extract_web_search_evidence(payload: Mapping[str, Any]) -> ToolEvidence:
    """Extract normalized URLs from the built-in search result contract."""
    sources: set[str] = set()
    for result in payload.get("results", []):
        if isinstance(result, dict) and isinstance(result.get("url"), str):
            sources.add(result["url"])
    return ToolEvidence(sources=sorted(sources))


def extract_write_file_evidence(payload: Mapping[str, Any]) -> ToolEvidence:
    """Trust a workspace artifact only after the built-in writer confirms success."""
    if payload.get("status") == "succeeded" and isinstance(payload.get("path"), str):
        return ToolEvidence(artifacts=[payload["path"]])
    return ToolEvidence()


BUILTIN_EVIDENCE_EXTRACTORS: Mapping[str, EvidenceExtractor] = MappingProxyType(
    {
        "web_search": extract_web_search_evidence,
        "write_file": extract_write_file_evidence,
    }
)
EMPTY_EVIDENCE_EXTRACTORS: Mapping[str, EvidenceExtractor] = MappingProxyType({})


def collect_evidence(
    messages: list[AnyMessage],
    *,
    evidence_extractors: Mapping[str, EvidenceExtractor] | None = None,
) -> tuple[list[str], list[str]]:
    """Extract evidence only through explicitly registered, typed tool policies."""
    sources: set[str] = set()
    artifacts: set[str] = set()
    active_extractors = (
        EMPTY_EVIDENCE_EXTRACTORS if evidence_extractors is None else evidence_extractors
    )
    for message in messages:
        if not isinstance(message, ToolMessage) or not message.name:
            continue
        extractor = active_extractors.get(message.name)
        if extractor is None:
            continue
        try:
            payload = json.loads(_content_text(message))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        try:
            extracted = extractor(payload)
            evidence = (
                extracted
                if isinstance(extracted, ToolEvidence)
                else ToolEvidence.model_validate(extracted)
            )
        except Exception:
            logger.warning("Evidence extraction failed for tool '%s'", message.name)
            continue
        sources.update(evidence.sources)
        artifacts.update(evidence.artifacts)
    return sorted(sources), sorted(artifacts)


def _tool_error(error: Exception) -> str:
    """Give the model a useful category without leaking local paths or exception text."""
    return json.dumps(
        {
            "status": "failed",
            "error_type": type(error).__name__,
            "message": "The tool rejected the request or could not complete it safely.",
        }
    )


def build_graph(
    *,
    settings: Settings,
    brain: AgentBrain,
    tools: list[BaseTool],
    memory: MemoryStore | None,
    checkpointer: BaseCheckpointSaver[Any],
    evidence_extractors: Mapping[str, EvidenceExtractor] | None = None,
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    """Compile Atlas's typed workflow around injected infrastructure."""

    registered_evidence_extractors = MappingProxyType(dict(evidence_extractors or {}))

    async def recall_node(state: AgentState) -> dict[str, Any]:
        get_stream_writer()({"stage": "recall", "message": "Searching long-term memory"})
        if memory is None or not settings.memory_enabled:
            return {"recalled_memories": []}
        try:
            records = await asyncio.to_thread(
                memory.search,
                user_id=state["user_id"],
                query=state["task"],
                limit=settings.memory_recall_limit,
            )
        except Exception:
            logger.exception("Long-term memory recall failed")
            return {"recalled_memories": []}
        return {"recalled_memories": [record.content for record in records]}

    async def plan_node(state: AgentState) -> dict[str, Any]:
        get_stream_writer()({"stage": "plan", "message": "Breaking the task into steps"})
        plan = await brain.plan(
            task=state["task"],
            memories=state.get("recalled_memories", []),
        )
        return {"plan": [step.model_dump(mode="json") for step in plan.steps]}

    async def agent_node(state: AgentState) -> dict[str, Any]:
        iteration = state.get("agent_iterations", 0)
        allow_tools = iteration < settings.max_agent_iterations
        get_stream_writer()(
            {
                "stage": "agent",
                "message": "Selecting the next action"
                if allow_tools
                else "Closing within the action budget",
                "iteration": iteration + 1,
            }
        )
        response = await brain.act(
            messages=state["messages"],
            plan=state.get("plan", []),
            memories=state.get("recalled_memories", []),
            review_feedback=str(state.get("review", {}).get("feedback", "")),
            allow_tools=allow_tools,
        )
        if len(response.tool_calls) > 1:
            logger.warning("Actor requested parallel tools; serializing to the first call")
            response = response.model_copy(update={"tool_calls": response.tool_calls[:1]})
        return {"messages": [response], "agent_iterations": iteration + 1}

    async def review_node(state: AgentState) -> dict[str, Any]:
        get_stream_writer()({"stage": "review", "message": "Verifying the requested outcome"})
        sources, artifacts = collect_evidence(
            state["messages"],
            evidence_extractors=registered_evidence_extractors,
        )
        review = await brain.review(
            task=state["task"],
            plan=state.get("plan", []),
            messages=state["messages"],
        )
        plan_ids = {str(step.get("id", "")) for step in state.get("plan", [])}
        completed_ids = set(review.completed_step_ids)
        unknown_ids = completed_ids - plan_ids
        incomplete_ids = plan_ids - completed_ids
        if unknown_ids or (review.complete and incomplete_ids):
            logger.warning("Reviewer returned plan-inconsistent completion state")
            review = ReviewDecision(
                complete=False,
                rationale="The review did not account for the validated execution plan.",
                feedback="Re-evaluate every provided plan step using only confirmed evidence.",
                completed_step_ids=sorted(completed_ids & plan_ids),
                missing_requirements=[
                    "Confirm every validated plan step before marking the task complete."
                ],
            )
        return {
            "review": review.model_dump(mode="json"),
            "review_cycles": state.get("review_cycles", 0) + 1,
            "sources": sources,
            "artifacts": artifacts,
        }

    async def finalize_node(state: AgentState) -> dict[str, Any]:
        get_stream_writer()({"stage": "finalize", "message": "Synthesizing the verified result"})
        sources, artifacts = collect_evidence(
            state["messages"],
            evidence_extractors=registered_evidence_extractors,
        )
        response = await brain.finalize(
            task=state["task"],
            plan=state.get("plan", []),
            messages=state["messages"],
            review=state.get("review", {}),
            sources=sources,
            artifacts=artifacts,
        )
        return {
            "messages": [response],
            "final_answer": _content_text(response),
            "sources": sources,
            "artifacts": artifacts,
        }

    async def remember_node(state: AgentState) -> dict[str, Any]:
        get_stream_writer()({"stage": "remember", "message": "Curating durable context"})
        if (
            memory is None
            or not settings.memory_enabled
            or not state.get("review", {}).get("complete")
        ):
            return {"memories_saved": 0}
        try:
            extraction = await brain.extract_memories(
                task=state["task"],
                answer=state.get("final_answer", ""),
            )
            saved = 0
            for candidate in extraction.memories:
                record = await asyncio.to_thread(
                    memory.add,
                    user_id=state["user_id"],
                    thread_id=state["public_thread_id"],
                    candidate=candidate,
                )
                saved += int(record is not None)
            return {"memories_saved": saved}
        except Exception:
            logger.exception("Long-term memory write failed")
            return {"memories_saved": 0}

    def route_after_agent(state: AgentState) -> Literal["tools", "review"]:
        last_message = state["messages"][-1]
        if (
            isinstance(last_message, AIMessage)
            and last_message.tool_calls
            and state.get("agent_iterations", 0) <= settings.max_agent_iterations
        ):
            return "tools"
        return "review"

    def route_after_review(state: AgentState) -> Literal["agent", "finalize"]:
        review = state.get("review", {})
        if review.get("complete"):
            return "finalize"
        if state.get("review_cycles", 0) > settings.max_review_cycles:
            return "finalize"
        return "agent"

    builder = StateGraph(AgentState)
    builder.add_node("recall", recall_node)
    builder.add_node("plan", plan_node)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools, handle_tool_errors=_tool_error))
    builder.add_node("review", review_node)
    builder.add_node("finalize", finalize_node)
    builder.add_node("remember", remember_node)

    builder.add_edge(START, "recall")
    builder.add_edge("recall", "plan")
    builder.add_edge("plan", "agent")
    builder.add_conditional_edges(
        "agent",
        route_after_agent,
        {"tools": "tools", "review": "review"},
    )
    builder.add_edge("tools", "agent")
    builder.add_conditional_edges(
        "review",
        route_after_review,
        {"agent": "agent", "finalize": "finalize"},
    )
    builder.add_edge("finalize", "remember")
    builder.add_edge("remember", END)
    return builder.compile(checkpointer=checkpointer)
