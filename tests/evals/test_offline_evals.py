from __future__ import annotations

import sys
from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

CASES_PATH = Path(__file__).parents[2] / "evals" / "cases.json"
RUNNER_PATH = CASES_PATH.with_name("run_evals.py")
SPEC = spec_from_file_location("atlas_offline_evals", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
EVALS = module_from_spec(SPEC)
sys.modules[SPEC.name] = EVALS
SPEC.loader.exec_module(EVALS)

evaluate_cases = EVALS.evaluate_cases
load_cases = EVALS.load_cases
main = EVALS.main
render_report = EVALS.render_report
run_safety_probe = EVALS.run_safety_probe
score_artifacts = EVALS.score_artifacts
score_case = EVALS.score_case
score_citations = EVALS.score_citations
score_memory = EVALS.score_memory


def _case(cases: list[dict[str, Any]], case_id: str) -> dict[str, Any]:
    return next(case for case in cases if case["id"] == case_id)


def test_fixture_is_curated_and_has_unique_ids() -> None:
    cases = load_cases(CASES_PATH)

    ids = [case["id"] for case in cases]
    categories = {case["category"] for case in cases}

    assert len(cases) >= 10
    assert len(ids) == len(set(ids))
    assert {"research", "code_execution", "memory", "safety"} <= categories
    assert sum(case["category"] == "safety" for case in cases) >= 4


def test_curated_offline_baseline_scores_perfectly_without_model_calls() -> None:
    result = evaluate_cases(load_cases(CASES_PATH), threshold=1.0)

    assert result.passed
    assert result.passed_count == len(result.cases)
    assert result.score == 1.0
    assert all(score == 1.0 for score in result.dimension_averages().values())


def test_missing_tool_breaks_required_trajectory() -> None:
    cases = load_cases(CASES_PATH)
    candidate = deepcopy(_case(cases, "research_framework_comparison"))
    candidate["recorded_run"]["tool_calls"] = [
        call for call in candidate["recorded_run"]["tool_calls"] if call["name"] != "calculator"
    ]

    result = score_case(candidate, threshold=0.85)
    trajectory = next(item for item in result.dimensions if item.name == "trajectory")

    assert not result.passed
    assert trajectory.score < 0.85
    assert any(not check.passed for check in trajectory.checks)


def test_artifact_and_citation_scorers_detect_contract_regressions() -> None:
    cases = load_cases(CASES_PATH)
    candidate = deepcopy(_case(cases, "research_framework_comparison"))
    candidate["recorded_run"]["artifacts"][0]["content"] = "# Framework Comparison"
    candidate["recorded_run"]["citations"] = [
        "http://docs.langchain.com/oss/python/langgraph/overview",
        "http://docs.langchain.com/oss/python/langgraph/overview",
    ]

    artifact_result = score_artifacts(candidate["expected"]["artifacts"], candidate["recorded_run"])
    citation_result = score_citations(candidate["expected"]["citations"], candidate["recorded_run"])

    assert artifact_result.score < 1.0
    assert citation_result.score < 1.0
    assert any(
        "recommendation" in check.label for check in artifact_result.checks if not check.passed
    )
    assert any("HTTPS" in check.label for check in citation_result.checks if not check.passed)


def test_memory_scorer_rejects_cross_user_recall() -> None:
    cases = load_cases(CASES_PATH)
    candidate = deepcopy(_case(cases, "memory_user_isolation"))
    candidate["recorded_run"]["memory_hits"].append(
        {"user_id": "alice", "content": "The preferred reporting currency is CAD."}
    )

    result = score_memory(candidate["expected"]["memory"], candidate["recorded_run"])

    assert result.score < 1.0
    assert any("belong to bob" in check.label for check in result.checks if not check.passed)
    assert any("excludes: cad" in check.label for check in result.checks if not check.passed)


def test_safety_cases_probe_real_validators_without_executing_code() -> None:
    cases = load_cases(CASES_PATH)
    safety_cases = [case for case in cases if "safety" in case["expected"]]

    results = [score_case(case, threshold=1.0) for case in safety_cases]
    safe_python = _case(cases, "approve_safe_python_before_execution")
    decision, reason = run_safety_probe(safe_python["safety_probe"])

    assert all(result.passed for result in results)
    assert decision == "approval_required"
    assert "human approval" in reason


def test_cli_report_is_readable_and_returns_success(capsys: Any) -> None:
    exit_code = main(["--cases", str(CASES_PATH), "--threshold", "1.0"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Atlas Agent Offline Evaluation" in output
    assert "research_framework_comparison" in output
    assert "Dimension averages:" in output
    assert "14/14 cases passed" in output


def test_report_lists_actionable_failed_checks() -> None:
    cases = load_cases(CASES_PATH)
    candidate = deepcopy(_case(cases, "budget_calculation"))
    candidate["recorded_run"]["status"] = "failed"
    result = evaluate_cases([candidate], threshold=1.0)

    report = render_report(result)

    assert "FAIL" in report
    assert "Failed checks:" in report
    assert "run status is completed" in report
