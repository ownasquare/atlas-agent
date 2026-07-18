"""Provider-neutral model gateway for all graph reasoning roles."""

from __future__ import annotations

import json
from typing import Any, Protocol, TypeVar

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from atlas_agent.config import Settings
from atlas_agent.prompts import (
    ACTOR_SYSTEM_PROMPT,
    FINALIZER_SYSTEM_PROMPT,
    MEMORY_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    REVIEWER_SYSTEM_PROMPT,
)
from atlas_agent.schemas import MemoryExtraction, ReviewDecision, TaskPlan


class AgentBrain(Protocol):
    async def plan(self, *, task: str, memories: list[str]) -> TaskPlan: ...

    async def act(
        self,
        *,
        messages: list[AnyMessage],
        plan: list[dict[str, Any]],
        memories: list[str],
        review_feedback: str,
        allow_tools: bool,
    ) -> AIMessage: ...

    async def review(
        self,
        *,
        task: str,
        plan: list[dict[str, Any]],
        messages: list[AnyMessage],
    ) -> ReviewDecision: ...

    async def finalize(
        self,
        *,
        task: str,
        plan: list[dict[str, Any]],
        messages: list[AnyMessage],
        review: dict[str, Any],
        sources: list[str],
        artifacts: list[str],
    ) -> AIMessage: ...

    async def extract_memories(self, *, task: str, answer: str) -> MemoryExtraction: ...


SchemaT = TypeVar("SchemaT", bound=BaseModel)


def _coerce_schema(value: Any, schema: type[SchemaT]) -> SchemaT:
    if isinstance(value, schema):
        return value
    return schema.model_validate(value)


def _render_memories(memories: list[str]) -> str:
    if not memories:
        return "No relevant long-term memories were recalled."
    return (
        "<untrusted_recalled_memory>\n"
        + "\n".join(f"- {item}" for item in memories)
        + "\n</untrusted_recalled_memory>"
    )


def _render_message(message: AnyMessage) -> str:
    content = message.content
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    line = f"[{message.type}] {text}"
    if isinstance(message, AIMessage) and message.tool_calls:
        line += f"\n[requested_tools] {json.dumps(message.tool_calls, ensure_ascii=False)}"
    return line


def _message_blocks(messages: list[AnyMessage]) -> list[list[AnyMessage]]:
    """Keep each tool request adjacent to all of its contiguous tool results."""
    blocks: list[list[AnyMessage]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        block = [message]
        index += 1
        if isinstance(message, AIMessage) and message.tool_calls:
            while index < len(messages) and isinstance(messages[index], ToolMessage):
                block.append(messages[index])
                index += 1
        blocks.append(block)
    return blocks


def _conversation_turns(messages: list[AnyMessage]) -> list[list[AnyMessage]]:
    """Group prior history by user turn so an old assistant reply is never orphaned."""
    turns: list[list[AnyMessage]] = []
    current: list[AnyMessage] = []
    for message in messages:
        if isinstance(message, HumanMessage) and current:
            turns.append(current)
            current = []
        current.append(message)
    if current:
        turns.append(current)
    return turns


def _context_excerpt(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    marker = f"\n...[{len(value) - limit} chars omitted from model context]...\n"
    available = max(0, limit - len(marker))
    head = available * 2 // 3
    tail = available - head
    return value[:head] + marker + (value[-tail:] if tail else "")


def _compact_value(value: Any, *, string_limit: int) -> Any:
    if isinstance(value, str):
        return _context_excerpt(value, string_limit)
    if isinstance(value, dict):
        return {
            str(key): _compact_value(item, string_limit=string_limit) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_compact_value(item, string_limit=string_limit) for item in value[:20]]
    return value


def _compact_block(block: list[AnyMessage], *, max_chars: int) -> list[AnyMessage]:
    """Compact a newest/current block without breaking tool-call/result identity."""
    if max_chars < 500:
        return []
    content_limit = max(100, (max_chars - 500) // max(1, len(block)))

    def build(limit: int) -> list[AnyMessage]:
        compacted: list[AnyMessage] = []
        for message in block:
            if isinstance(message, AIMessage) and message.tool_calls:
                calls = [
                    {
                        **call,
                        "args": _compact_value(call.get("args", {}), string_limit=limit),
                    }
                    for call in message.tool_calls
                ]
                content = message.content
                if isinstance(content, str):
                    content = _context_excerpt(content, min(limit, 1_000))
                compacted.append(
                    message.model_copy(
                        update={
                            "content": content,
                            "tool_calls": calls,
                            "additional_kwargs": {},
                        }
                    )
                )
            else:
                content = message.content
                text = (
                    content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                )
                compacted.append(
                    message.model_copy(update={"content": _context_excerpt(text, limit)})
                )
        return compacted

    while content_limit >= 100:
        compacted = build(content_limit)
        if sum(len(_render_message(message)) for message in compacted) <= max_chars:
            return compacted
        content_limit //= 2
    return []


def bounded_model_messages(
    messages: list[AnyMessage], *, max_chars: int = 30_000
) -> list[AnyMessage]:
    """Keep the current request and newest complete message blocks within a hard budget."""
    if not messages:
        return []
    human_indexes = [
        index for index, message in enumerate(messages) if isinstance(message, HumanMessage)
    ]
    anchor_index = human_indexes[-1] if human_indexes else len(messages)
    anchor = [messages[anchor_index]] if anchor_index < len(messages) else []
    if anchor and len(_render_message(anchor[0])) > max_chars // 2:
        anchor = _compact_block(anchor, max_chars=max_chars // 2)
    used = sum(len(_render_message(message)) for message in anchor)

    def newest_fitting(
        blocks: list[list[AnyMessage]], budget: int, *, compact_oversized: bool
    ) -> list[AnyMessage]:
        selected: list[list[AnyMessage]] = []
        consumed = 0
        for block in reversed(blocks):
            size = sum(len(_render_message(message)) for message in block)
            if consumed + size > budget:
                if not compact_oversized:
                    break
                block = _compact_block(block, max_chars=budget - consumed)
                size = sum(len(_render_message(message)) for message in block)
                if not block or consumed + size > budget:
                    break
            selected.append(block)
            consumed += size
        return [message for block in reversed(selected) for message in block]

    after = messages[anchor_index + 1 :] if anchor else messages
    selected_after = newest_fitting(
        _message_blocks(after),
        max(0, max_chars - used),
        compact_oversized=True,
    )
    used += sum(len(_render_message(message)) for message in selected_after)
    before = messages[:anchor_index] if anchor else []
    selected_before = newest_fitting(
        _conversation_turns(before),
        max(0, max_chars - used),
        compact_oversized=False,
    )
    return [*selected_before, *anchor, *selected_after]


def render_transcript(messages: list[AnyMessage], *, max_chars: int = 30_000) -> str:
    """Create a bounded transcript that always favors the newest/current evidence."""
    rendered: list[str] = []
    used = 0
    for message in bounded_model_messages(messages, max_chars=max_chars):
        line = _render_message(message)
        remaining = max_chars - used
        if remaining <= 0:
            break
        rendered.append(line[:remaining])
        used += min(len(line), remaining)
    return "\n".join(rendered)


class LangChainBrain:
    """Use one chat model through specialized structured and tool-bound views."""

    def __init__(self, settings: Settings, tools: list[BaseTool]) -> None:
        self._settings = settings
        self._tools = tools
        self._model: BaseChatModel | None = None
        self._actor_with_tools: Any = None
        self._planner: Any = None
        self._reviewer: Any = None
        self._memory_curator: Any = None

    def _ensure_initialized(self) -> BaseChatModel:
        """Construct provider clients only when a task actually needs the model."""
        if self._model is not None:
            return self._model
        model_options: dict[str, Any] = {}
        if self._settings.model_api_key is not None:
            model_options["api_key"] = self._settings.model_api_key.get_secret_value()
        model = init_chat_model(
            self._settings.model,
            temperature=self._settings.model_temperature,
            **model_options,
        )
        self._model = model
        self._actor_with_tools = self._model.bind_tools(self._tools)
        self._planner = self._model.with_structured_output(TaskPlan)
        self._reviewer = self._model.with_structured_output(ReviewDecision)
        self._memory_curator = self._model.with_structured_output(MemoryExtraction)
        return self._model

    async def plan(self, *, task: str, memories: list[str]) -> TaskPlan:
        self._ensure_initialized()
        response = await self._planner.ainvoke(
            [
                SystemMessage(content=PLANNER_SYSTEM_PROMPT),
                ("human", f"Task:\n{task}\n\n{_render_memories(memories)}"),
            ]
        )
        return _coerce_schema(response, TaskPlan)

    async def act(
        self,
        *,
        messages: list[AnyMessage],
        plan: list[dict[str, Any]],
        memories: list[str],
        review_feedback: str,
        allow_tools: bool,
    ) -> AIMessage:
        model = self._ensure_initialized()
        execution_context = {
            "plan": plan,
            "review_feedback": review_feedback,
            "tools_allowed_this_iteration": allow_tools,
        }
        system = SystemMessage(
            content=(
                f"{ACTOR_SYSTEM_PROMPT}\n\n"
                f"Execution context:\n{json.dumps(execution_context, ensure_ascii=False)}\n\n"
                f"{_render_memories(memories)}"
            )
        )
        selected_model = self._actor_with_tools if allow_tools else model
        response = await selected_model.ainvoke([system, *bounded_model_messages(messages)])
        if not isinstance(response, AIMessage):
            raise TypeError("chat model did not return an AIMessage")
        return response

    async def review(
        self,
        *,
        task: str,
        plan: list[dict[str, Any]],
        messages: list[AnyMessage],
    ) -> ReviewDecision:
        self._ensure_initialized()
        response = await self._reviewer.ainvoke(
            [
                SystemMessage(content=REVIEWER_SYSTEM_PROMPT),
                (
                    "human",
                    "Original task:\n"
                    f"{task}\n\nPlan:\n{json.dumps(plan, ensure_ascii=False)}\n\n"
                    f"<untrusted_execution_transcript>\n{render_transcript(messages)}\n"
                    "</untrusted_execution_transcript>",
                ),
            ]
        )
        return _coerce_schema(response, ReviewDecision)

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
        model = self._ensure_initialized()
        evidence = {
            "review": review,
            "confirmed_source_urls": sources,
            "confirmed_workspace_artifacts": artifacts,
        }
        response = await model.ainvoke(
            [
                SystemMessage(content=FINALIZER_SYSTEM_PROMPT),
                (
                    "human",
                    f"Original task:\n{task}\n\nPlan:\n{json.dumps(plan, ensure_ascii=False)}\n\n"
                    f"Verified evidence:\n{json.dumps(evidence, ensure_ascii=False)}\n\n"
                    f"<untrusted_execution_transcript>\n{render_transcript(messages)}\n"
                    "</untrusted_execution_transcript>",
                ),
            ]
        )
        if not isinstance(response, AIMessage):
            raise TypeError("chat model did not return an AIMessage")
        return response

    async def extract_memories(self, *, task: str, answer: str) -> MemoryExtraction:
        self._ensure_initialized()
        response = await self._memory_curator.ainvoke(
            [
                SystemMessage(content=MEMORY_SYSTEM_PROMPT),
                ("human", f"User task:\n{task}\n\nAtlas answer:\n{answer[:8_000]}"),
            ]
        )
        return _coerce_schema(response, MemoryExtraction)
