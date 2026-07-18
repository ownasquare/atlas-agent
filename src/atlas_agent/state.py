"""Typed LangGraph state and state-construction helpers."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage, HumanMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    task: str
    user_id: str
    public_thread_id: str
    recalled_memories: list[str]
    plan: list[dict[str, Any]]
    agent_iterations: int
    review_cycles: int
    review: dict[str, Any]
    final_answer: str
    sources: list[str]
    artifacts: list[str]
    memories_saved: int


def initial_state(*, message: str, user_id: str, thread_id: str) -> AgentState:
    """Create an explicit per-turn state update for a new request."""
    return {
        "messages": [HumanMessage(content=message)],
        "task": message,
        "user_id": user_id,
        "public_thread_id": thread_id,
        "recalled_memories": [],
        "plan": [],
        "agent_iterations": 0,
        "review_cycles": 0,
        "review": {},
        "final_answer": "",
        "sources": [],
        "artifacts": [],
        "memories_saved": 0,
    }
