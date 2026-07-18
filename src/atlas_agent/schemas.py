"""Strict public and model-generated schemas used throughout Atlas Agent."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


ToolName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$",
    ),
]


class PlanStep(StrictModel):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    description: str = Field(min_length=3, max_length=500)
    success_criteria: str = Field(min_length=3, max_length=500)
    tool_hint: ToolName | None = None
    depends_on: list[str] = Field(default_factory=list, max_length=10)


class TaskPlan(StrictModel):
    goal: str = Field(min_length=3, max_length=1_000)
    reasoning_summary: str = Field(min_length=3, max_length=1_000)
    steps: list[PlanStep] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def dependencies_reference_prior_unique_steps(self) -> TaskPlan:
        seen: set[str] = set()
        for step in self.steps:
            if step.id in seen:
                raise ValueError(f"duplicate plan step id: {step.id}")
            unknown = set(step.depends_on) - seen
            if unknown:
                raise ValueError(
                    f"step {step.id} depends on unknown or later steps: {sorted(unknown)}"
                )
            seen.add(step.id)
        return self


class ReviewDecision(StrictModel):
    complete: bool
    rationale: str = Field(min_length=3, max_length=1_000)
    feedback: str = Field(default="", max_length=1_000)
    completed_step_ids: list[str] = Field(default_factory=list, max_length=12)
    missing_requirements: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def completion_state_is_consistent(self) -> ReviewDecision:
        if self.complete and self.missing_requirements:
            raise ValueError("a complete review cannot have missing requirements")
        if not self.complete and not self.feedback:
            raise ValueError("an incomplete review requires actionable feedback")
        return self


class MemoryCandidate(StrictModel):
    content: str = Field(min_length=3, max_length=1_000)
    category: Literal["preference", "fact", "project", "constraint"]
    importance: int = Field(default=3, ge=1, le=5)


class MemoryExtraction(StrictModel):
    memories: list[MemoryCandidate] = Field(default_factory=list, max_length=5)


class MemoryRecord(StrictModel):
    id: str
    user_id: str
    content: str
    category: str
    importance: int = Field(ge=1, le=5)
    source_thread: str
    created_at: datetime
    distance: float | None = None


class SearchResult(StrictModel):
    title: str = Field(min_length=1, max_length=500)
    url: HttpUrl
    snippet: str = Field(default="", max_length=2_000)
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ToolEvidence(StrictModel):
    """Verified sources and workspace artifacts extracted from one tool result."""

    sources: list[str] = Field(default_factory=list, max_length=50)
    artifacts: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("sources")
    @classmethod
    def sources_are_bounded_http_urls(cls, values: list[str]) -> list[str]:
        for value in values:
            if len(value) > 2_083 or any(character.isspace() for character in value):
                raise ValueError("evidence sources must be bounded HTTP(S) URLs")
            try:
                _HTTP_URL_ADAPTER.validate_python(value)
            except ValidationError as exc:
                raise ValueError("evidence sources must be bounded HTTP(S) URLs") from exc
        return values

    @field_validator("artifacts")
    @classmethod
    def artifacts_are_safe_relative_paths(cls, values: list[str]) -> list[str]:
        for value in values:
            path = PurePosixPath(value)
            if (
                len(value) > 500
                or "\\" in value
                or any(ord(character) < 32 for character in value)
                or path.is_absolute()
                or not path.parts
                or any(part in {"", ".", ".."} or part.startswith(".") for part in path.parts)
            ):
                raise ValueError("evidence artifacts must be safe workspace-relative paths")
        return values


EvidenceExtractor: TypeAlias = Callable[[Mapping[str, Any]], ToolEvidence]


class ToolTrace(StrictModel):
    tool: str
    status: Literal["started", "succeeded", "failed", "rejected"]
    summary: str = ""
    duration_ms: int = Field(default=0, ge=0)


class RunStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class ApprovalRequest(StrictModel):
    id: str = Field(min_length=1, max_length=200)
    action: str
    question: str
    details: dict[str, Any] = Field(default_factory=dict)


class ApprovalResponse(StrictModel):
    interrupt_id: str = Field(min_length=1, max_length=200)
    action: Literal["approve", "reject"]
    state_token: str | None = Field(default=None, max_length=200)
    edited_arguments: dict[str, Any] | None = None

    @field_validator("edited_arguments")
    @classmethod
    def edited_arguments_are_bounded(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and len(json.dumps(value, ensure_ascii=False)) > 1_050_000:
            raise ValueError("edited arguments exceed the approval payload limit")
        return value


class ThreadIdentity(StrictModel):
    user_id: str = Field(default="local-user", min_length=1, max_length=100)
    thread_id: str = Field(min_length=1, max_length=100)


class ChatRequest(ThreadIdentity):
    message: str = Field(min_length=1, max_length=20_000)


class ResumeRequest(ThreadIdentity):
    response: ApprovalResponse


class RunResult(StrictModel):
    user_id: str
    thread_id: str
    status: RunStatus
    answer: str = ""
    plan: list[PlanStep] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    iterations: int = 0
    review_cycles: int = 0
    interrupt: ApprovalRequest | None = None


class StreamEvent(StrictModel):
    event: Literal["stage", "token", "interrupt", "result", "error"]
    data: dict[str, Any]


class HealthResponse(StrictModel):
    status: Literal["ok"] = "ok"
    version: str
    model: str
    model_configured: bool
    memory_enabled: bool
    code_backend: str
