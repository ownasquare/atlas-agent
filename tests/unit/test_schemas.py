import pytest
from pydantic import ValidationError

from atlas_agent.schemas import ApprovalResponse, PlanStep, ReviewDecision, TaskPlan, ToolEvidence


def step(identifier: str, *, depends_on: list[str] | None = None) -> PlanStep:
    return PlanStep(
        id=identifier,
        description=f"Complete {identifier}",
        success_criteria=f"Evidence exists for {identifier}",
        depends_on=depends_on or [],
    )


def test_plan_accepts_ordered_prior_dependencies() -> None:
    plan = TaskPlan(
        goal="Create a verified report",
        reasoning_summary="Research before synthesis.",
        steps=[step("research"), step("write", depends_on=["research"])],
    )

    assert [item.id for item in plan.steps] == ["research", "write"]


def test_plan_rejects_duplicate_step_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        TaskPlan(
            goal="Create a verified report",
            reasoning_summary="Two accidental duplicates.",
            steps=[step("research"), step("research")],
        )


def test_plan_rejects_later_or_unknown_dependency() -> None:
    with pytest.raises(ValidationError, match="unknown or later"):
        TaskPlan(
            goal="Create a verified report",
            reasoning_summary="Invalid order.",
            steps=[step("write", depends_on=["research"]), step("research")],
        )


def test_strict_api_models_reject_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        ApprovalResponse.model_validate(
            {"interrupt_id": "interrupt-1", "action": "approve", "unsafe": True}
        )


def test_tool_hint_accepts_builtins_and_valid_custom_names() -> None:
    assert step("research").model_copy(update={"tool_hint": "web_search"}).tool_hint == "web_search"
    custom = PlanStep(
        id="analyze",
        description="Analyze supplied text",
        success_criteria="Text statistics are returned",
        tool_hint="text_stats",
    )
    assert custom.tool_hint == "text_stats"


@pytest.mark.parametrize("tool_name", ["has spaces", "bad:name", "x" * 65])
def test_tool_hint_rejects_invalid_custom_names(tool_name: str) -> None:
    with pytest.raises(ValidationError):
        PlanStep(
            id="run",
            description="Run a custom tool",
            success_criteria="The custom tool finished",
            tool_hint=tool_name,
        )


@pytest.mark.parametrize(
    "evidence",
    [
        {"sources": ["file:///private/report.md"]},
        {"sources": ["https://example.com/a b"]},
        {"sources": ["https://example.com:99999/report"]},
        {"artifacts": ["../outside.md"]},
        {"artifacts": [".hidden/report.md"]},
        {"artifacts": ["..\\outside.md"]},
        {"artifacts": ["reports/bad\u0000name.md"]},
    ],
)
def test_tool_evidence_rejects_non_http_sources_and_unsafe_paths(
    evidence: dict[str, list[str]],
) -> None:
    with pytest.raises(ValidationError):
        ToolEvidence.model_validate(evidence)


def test_tool_evidence_preserves_the_validated_source_string() -> None:
    source = "https://Example.com/Report?view=Full#Summary"

    evidence = ToolEvidence(sources=[source])

    assert evidence.sources == [source]


def test_review_completion_state_must_be_self_consistent() -> None:
    with pytest.raises(ValidationError, match="complete review"):
        ReviewDecision(
            complete=True,
            rationale="Contradictory output.",
            completed_step_ids=["research"],
            missing_requirements=["citation"],
        )
    with pytest.raises(ValidationError, match="actionable feedback"):
        ReviewDecision(
            complete=False,
            rationale="Something is missing.",
            missing_requirements=["citation"],
        )


def test_approval_edits_have_a_hard_payload_limit() -> None:
    with pytest.raises(ValidationError, match="payload limit"):
        ApprovalResponse(
            interrupt_id="interrupt-1",
            action="approve",
            edited_arguments={"content": "x" * 1_050_001},
        )
