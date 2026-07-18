"""Deterministic, key-free regression evaluations for Atlas Agent.

The workflow cases contain recorded run contracts so planning and tool-routing
quality can be checked without calling a model. Safety cases execute Atlas's
real parsers and validators, but never execute generated code or access the web.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

DEFAULT_CASES_PATH = Path(__file__).with_name("cases.json")
DIMENSION_ORDER = (
    "plan",
    "trajectory",
    "completion",
    "artifacts",
    "citations",
    "memory",
    "safety",
)


@dataclass(frozen=True)
class CheckResult:
    """One deterministic assertion contributing to a dimension score."""

    label: str
    passed: bool


@dataclass(frozen=True)
class DimensionResult:
    """A collection of related checks with a normalized score."""

    name: str
    checks: tuple[CheckResult, ...]

    @property
    def earned(self) -> int:
        return sum(check.passed for check in self.checks)

    @property
    def possible(self) -> int:
        return len(self.checks)

    @property
    def score(self) -> float:
        return self.earned / self.possible if self.possible else 0.0


@dataclass(frozen=True)
class CaseResult:
    """Evaluation result for one curated case."""

    case_id: str
    category: str
    dimensions: tuple[DimensionResult, ...]
    score: float
    passed: bool


@dataclass(frozen=True)
class SuiteResult:
    """Aggregate result for a complete fixture run."""

    cases: tuple[CaseResult, ...]
    threshold: float

    @property
    def passed_count(self) -> int:
        return sum(case.passed for case in self.cases)

    @property
    def passed(self) -> bool:
        return self.passed_count == len(self.cases)

    @property
    def score(self) -> float:
        earned = sum(dimension.earned for case in self.cases for dimension in case.dimensions)
        possible = sum(dimension.possible for case in self.cases for dimension in case.dimensions)
        return earned / possible if possible else 0.0

    def dimension_averages(self) -> dict[str, float]:
        averages: dict[str, float] = {}
        for name in DIMENSION_ORDER:
            scores = [
                dimension.score
                for case in self.cases
                for dimension in case.dimensions
                if dimension.name == name
            ]
            if scores:
                averages[name] = sum(scores) / len(scores)
        return averages


def _normalise(value: Any) -> str:
    return " ".join(str(value).casefold().split())


def _list(container: Mapping[str, Any], key: str) -> list[Any]:
    value = container.get(key, [])
    return value if isinstance(value, list) else []


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _dimension(name: str, checks: list[CheckResult]) -> DimensionResult:
    if not checks:
        raise ValueError(f"evaluation dimension '{name}' has no configured checks")
    return DimensionResult(name=name, checks=tuple(checks))


def _is_subsequence(expected: Sequence[Any], actual: Sequence[Any]) -> bool:
    cursor = 0
    for item in actual:
        if cursor < len(expected) and item == expected[cursor]:
            cursor += 1
    return cursor == len(expected)


def _plan_step_text(step: Any) -> str:
    if isinstance(step, Mapping):
        fields = (
            step.get("description", ""),
            step.get("success_criteria", ""),
            step.get("tool_hint", ""),
        )
        return _normalise(" ".join(str(field) for field in fields))
    return _normalise(step)


def score_plan(expectation: Mapping[str, Any], recorded_run: Mapping[str, Any]) -> DimensionResult:
    """Score plan size, required concepts, and their ordering."""

    steps = [_plan_step_text(step) for step in _list(recorded_run, "plan")]
    checks: list[CheckResult] = []
    if "min_steps" in expectation:
        minimum = int(expectation["min_steps"])
        checks.append(CheckResult(f"plan has at least {minimum} steps", len(steps) >= minimum))
    if "max_steps" in expectation:
        maximum = int(expectation["max_steps"])
        checks.append(CheckResult(f"plan has at most {maximum} steps", len(steps) <= maximum))

    groups = _list(expectation, "ordered_step_terms")
    normalized_groups: list[list[str]] = []
    for raw_group in groups:
        if not isinstance(raw_group, list) or not raw_group:
            continue
        terms = [_normalise(term) for term in raw_group]
        normalized_groups.append(terms)
        checks.append(
            CheckResult(
                "plan step contains " + " + ".join(terms),
                any(all(term in step for term in terms) for step in steps),
            )
        )

    cursor = -1
    ordered = True
    for terms in normalized_groups:
        next_index = next(
            (
                index
                for index in range(cursor + 1, len(steps))
                if all(term in steps[index] for term in terms)
            ),
            None,
        )
        if next_index is None:
            ordered = False
            break
        cursor = next_index
    if normalized_groups:
        checks.append(CheckResult("required plan steps appear in order", ordered))
    return _dimension("plan", checks)


def score_trajectory(
    expectation: Mapping[str, Any], recorded_run: Mapping[str, Any]
) -> DimensionResult:
    """Score ordered tool calls, statuses, forbidden calls, and approvals."""

    raw_calls = _list(recorded_run, "tool_calls")
    calls = [_mapping(call) for call in raw_calls]
    names = [str(call.get("name", "")) for call in calls]
    statuses = [(str(call.get("name", "")), str(call.get("status", ""))) for call in calls]
    checks: list[CheckResult] = []

    required_order = [str(name) for name in _list(expectation, "required_order")]
    if required_order:
        checks.append(
            CheckResult(
                "tool order: " + " -> ".join(required_order),
                _is_subsequence(required_order, names),
            )
        )

    raw_statuses = _list(expectation, "required_status_sequence")
    required_statuses = [
        (str(_mapping(item).get("name", "")), str(_mapping(item).get("status", "")))
        for item in raw_statuses
    ]
    if required_statuses:
        label = " -> ".join(f"{name}:{status}" for name, status in required_statuses)
        checks.append(
            CheckResult(
                "tool status sequence: " + label,
                _is_subsequence(required_statuses, statuses),
            )
        )

    for forbidden in _list(expectation, "forbidden_tools"):
        name = str(forbidden)
        checks.append(CheckResult(f"tool is not called: {name}", name not in names))

    approvals = {str(value) for value in _list(recorded_run, "approvals")}
    for required in _list(expectation, "required_approvals"):
        approval = str(required)
        checks.append(CheckResult(f"approval is requested: {approval}", approval in approvals))

    if "max_calls" in expectation:
        maximum = int(expectation["max_calls"])
        checks.append(CheckResult(f"uses at most {maximum} tools", len(calls) <= maximum))
    return _dimension("trajectory", checks)


def score_completion(
    expectation: Mapping[str, Any], recorded_run: Mapping[str, Any]
) -> DimensionResult:
    """Score terminal status and explicit answer requirements."""

    checks: list[CheckResult] = []
    if "status" in expectation:
        status = str(expectation["status"])
        checks.append(
            CheckResult(f"run status is {status}", str(recorded_run.get("status", "")) == status)
        )
    answer = _normalise(recorded_run.get("answer", ""))
    for required in _list(expectation, "required_answer_terms"):
        term = _normalise(required)
        checks.append(CheckResult(f"answer contains: {term}", term in answer))
    for forbidden in _list(expectation, "forbidden_answer_terms"):
        term = _normalise(forbidden)
        checks.append(CheckResult(f"answer excludes: {term}", term not in answer))
    return _dimension("completion", checks)


def _safe_workspace_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    return bool(normalized) and not path.is_absolute() and ".." not in path.parts


def score_artifacts(
    expectation: Mapping[str, Any], recorded_run: Mapping[str, Any]
) -> DimensionResult:
    """Score artifact count, paths, confinement, and required content."""

    artifacts = [_mapping(item) for item in _list(recorded_run, "artifacts")]
    checks: list[CheckResult] = []
    if "min_count" in expectation:
        minimum = int(expectation["min_count"])
        checks.append(
            CheckResult(f"at least {minimum} artifacts are present", len(artifacts) >= minimum)
        )
    if expectation.get("workspace_relative") is True:
        paths = [str(artifact.get("path", "")) for artifact in artifacts]
        checks.append(
            CheckResult(
                "all artifact paths remain workspace-relative",
                bool(paths) and all(_safe_workspace_path(path) for path in paths),
            )
        )

    by_path = {str(artifact.get("path", "")): artifact for artifact in artifacts}
    for raw_requirement in _list(expectation, "required"):
        requirement = _mapping(raw_requirement)
        path = str(requirement.get("path", ""))
        artifact = by_path.get(path)
        checks.append(CheckResult(f"artifact exists: {path}", artifact is not None))
        content = _normalise(artifact.get("content", "") if artifact else "")
        for required in _list(requirement, "contains"):
            term = _normalise(required)
            checks.append(CheckResult(f"artifact {path} contains: {term}", term in content))

    for forbidden in _list(expectation, "forbidden_paths"):
        path = str(forbidden)
        checks.append(CheckResult(f"artifact is not created: {path}", path not in by_path))
    return _dimension("artifacts", checks)


def _domain_matches(hostname: str, expected: str) -> bool:
    hostname = hostname.casefold().rstrip(".")
    expected = expected.casefold().rstrip(".")
    return hostname == expected or hostname.endswith("." + expected)


def score_citations(
    expectation: Mapping[str, Any], recorded_run: Mapping[str, Any]
) -> DimensionResult:
    """Score citation count, URL hygiene, uniqueness, and source domains."""

    citations = [str(value) for value in _list(recorded_run, "citations")]
    parsed = [urlsplit(value) for value in citations]
    checks: list[CheckResult] = []
    if "min_count" in expectation:
        minimum = int(expectation["min_count"])
        checks.append(
            CheckResult(f"at least {minimum} citations are present", len(citations) >= minimum)
        )
    if expectation.get("require_https") is True:
        checks.append(
            CheckResult(
                "all citations use HTTPS and include a host",
                bool(parsed) and all(item.scheme == "https" and item.hostname for item in parsed),
            )
        )
    if expectation.get("require_unique") is True:
        checks.append(CheckResult("citations are unique", len(citations) == len(set(citations))))
    for required in _list(expectation, "required_domains"):
        domain = str(required)
        checks.append(
            CheckResult(
                f"citation includes domain: {domain}",
                any(
                    item.hostname is not None and _domain_matches(item.hostname, domain)
                    for item in parsed
                ),
            )
        )
    return _dimension("citations", checks)


def score_memory(
    expectation: Mapping[str, Any], recorded_run: Mapping[str, Any]
) -> DimensionResult:
    """Score recalled content and per-user namespace isolation."""

    raw_hits = _list(recorded_run, "memory_hits")
    hits = [_mapping(hit) for hit in raw_hits]
    checks: list[CheckResult] = []
    if "min_hits" in expectation:
        minimum = int(expectation["min_hits"])
        checks.append(
            CheckResult(f"at least {minimum} memories are recalled", len(hits) >= minimum)
        )
    if "user_id" in expectation:
        user_id = str(expectation["user_id"])
        checks.append(
            CheckResult(
                f"all recalled memories belong to {user_id}",
                bool(hits) and all(str(hit.get("user_id", "")) == user_id for hit in hits),
            )
        )
    content = _normalise(" ".join(str(hit.get("content", "")) for hit in hits))
    for required in _list(expectation, "required_terms"):
        term = _normalise(required)
        checks.append(CheckResult(f"memory contains: {term}", term in content))
    for forbidden in _list(expectation, "forbidden_terms"):
        term = _normalise(forbidden)
        checks.append(CheckResult(f"memory excludes: {term}", term not in content))
    return _dimension("memory", checks)


def run_safety_probe(probe: Mapping[str, Any]) -> tuple[str, str]:
    """Exercise a safe parser/validator and return its policy decision.

    Python probes stop after source validation. They never execute the supplied
    source. File probes resolve a path only inside a temporary workspace.
    """

    probe_type = str(probe.get("type", ""))
    value = str(probe.get("input", ""))
    try:
        if probe_type == "calculator":
            from atlas_agent.tools.calculator import evaluate_expression

            evaluate_expression(value)
            return "allowed", "calculator expression passed validation"
        if probe_type == "file_path":
            from atlas_agent.tools.files import WorkspaceFiles

            with tempfile.TemporaryDirectory(prefix="atlas-eval-") as directory:
                WorkspaceFiles(Path(directory)).resolve(value)
            return "allowed", "file path passed workspace validation"
        if probe_type == "python_source":
            from atlas_agent.tools.python_exec import validate_python_source

            validate_python_source(value)
            if probe.get("requires_approval") is True:
                return (
                    "approval_required",
                    "source passed validation; execution requires human approval",
                )
            return "allowed", "Python source passed validation"
    except ValueError as exc:
        return "blocked", str(exc)
    raise ValueError(f"unsupported safety probe type: {probe_type}")


def score_safety(expectation: Mapping[str, Any], probe: Mapping[str, Any]) -> DimensionResult:
    """Score the decision and sanitized reason from a deterministic safety probe."""

    decision, reason = run_safety_probe(probe)
    expected_decision = str(expectation.get("decision", ""))
    checks = [CheckResult(f"safety decision is {expected_decision}", decision == expected_decision)]
    normalized_reason = _normalise(reason)
    for required in _list(expectation, "reason_contains"):
        term = _normalise(required)
        checks.append(CheckResult(f"safety reason contains: {term}", term in normalized_reason))
    return _dimension("safety", checks)


def score_case(case: Mapping[str, Any], *, threshold: float = 0.85) -> CaseResult:
    """Apply every configured scorer to one case."""

    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")
    expected = _mapping(case.get("expected"))
    recorded = _mapping(case.get("recorded_run"))
    dimensions: list[DimensionResult] = []
    scorers = {
        "plan": score_plan,
        "trajectory": score_trajectory,
        "completion": score_completion,
        "artifacts": score_artifacts,
        "citations": score_citations,
        "memory": score_memory,
    }
    for name in DIMENSION_ORDER:
        if name == "safety" and name in expected:
            dimensions.append(
                score_safety(_mapping(expected[name]), _mapping(case.get("safety_probe")))
            )
        elif name in expected:
            dimensions.append(scorers[name](_mapping(expected[name]), recorded))
    if not dimensions:
        raise ValueError(f"case {case.get('id', '<unknown>')} has no evaluation dimensions")

    earned = sum(dimension.earned for dimension in dimensions)
    possible = sum(dimension.possible for dimension in dimensions)
    score = earned / possible
    passed = score >= threshold and all(dimension.score >= threshold for dimension in dimensions)
    return CaseResult(
        case_id=str(case["id"]),
        category=str(case.get("category", "uncategorized")),
        dimensions=tuple(dimensions),
        score=score,
        passed=passed,
    )


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[dict[str, Any]]:
    """Load and minimally validate a JSON evaluation fixture."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("evaluation fixture must be a non-empty JSON array")
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_case in enumerate(payload):
        if not isinstance(raw_case, dict):
            raise ValueError(f"case at index {index} must be an object")
        case_id = raw_case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"case at index {index} must have a non-empty id")
        if case_id in seen:
            raise ValueError(f"duplicate evaluation case id: {case_id}")
        seen.add(case_id)
        if not isinstance(raw_case.get("prompt"), str):
            raise ValueError(f"case {case_id} must include a prompt")
        expected = raw_case.get("expected")
        if not isinstance(expected, dict) or not expected:
            raise ValueError(f"case {case_id} must include expectations")
        if "safety" in expected and not isinstance(raw_case.get("safety_probe"), dict):
            raise ValueError(f"safety case {case_id} must include a safety_probe")
        if "safety" not in expected and not isinstance(raw_case.get("recorded_run"), dict):
            raise ValueError(f"workflow case {case_id} must include a recorded_run")
        cases.append(raw_case)
    return cases


def evaluate_cases(cases: Sequence[Mapping[str, Any]], *, threshold: float = 0.85) -> SuiteResult:
    """Evaluate a sequence of cases with deterministic scorers."""

    if not cases:
        raise ValueError("at least one evaluation case is required")
    results = tuple(score_case(case, threshold=threshold) for case in cases)
    return SuiteResult(cases=results, threshold=threshold)


def _score_cell(case: CaseResult, dimension_name: str) -> str:
    dimension = next((item for item in case.dimensions if item.name == dimension_name), None)
    return "-" if dimension is None else f"{dimension.score:.0%}"


def render_report(result: SuiteResult) -> str:
    """Render a compact terminal report with actionable failed checks."""

    labels = {
        "plan": "Plan",
        "trajectory": "Tools",
        "completion": "Done",
        "artifacts": "Files",
        "citations": "Cites",
        "memory": "Memory",
        "safety": "Safety",
    }
    lines = ["Atlas Agent Offline Evaluation", "=" * 122]
    header = f"{'Case':<34}" + "".join(f"{labels[name]:>9}" for name in DIMENSION_ORDER)
    header += f"{'Total':>9}{'Result':>9}"
    lines.extend((header, "-" * 122))
    for case in result.cases:
        row = f"{case.case_id[:33]:<34}" + "".join(
            f"{_score_cell(case, name):>9}" for name in DIMENSION_ORDER
        )
        row += f"{case.score:>8.0%}{('PASS' if case.passed else 'FAIL'):>9}"
        lines.append(row)

    failed_checks = [
        (case.case_id, dimension.name, check.label)
        for case in result.cases
        for dimension in case.dimensions
        for check in dimension.checks
        if not check.passed
    ]
    if failed_checks:
        lines.extend(("", "Failed checks:"))
        lines.extend(
            f"  - {case_id} [{dimension}]: {label}" for case_id, dimension, label in failed_checks
        )

    averages = ", ".join(
        f"{labels[name]} {score:.0%}" for name, score in result.dimension_averages().items()
    )
    lines.extend(
        (
            "-" * 122,
            (
                f"Summary: {result.passed_count}/{len(result.cases)} cases passed | "
                f"overall {result.score:.1%} | threshold {result.threshold:.0%}"
            ),
            "Dimension averages: " + averages,
        )
    )
    return "\n".join(lines)


def result_as_dict(result: SuiteResult) -> dict[str, Any]:
    """Convert a suite result to a stable machine-readable structure."""

    return {
        "passed": result.passed,
        "passed_cases": result.passed_count,
        "total_cases": len(result.cases),
        "score": result.score,
        "threshold": result.threshold,
        "dimension_averages": result.dimension_averages(),
        "cases": [
            {
                "id": case.case_id,
                "category": case.category,
                "passed": case.passed,
                "score": case.score,
                "dimensions": {
                    dimension.name: {
                        "score": dimension.score,
                        "earned": dimension.earned,
                        "possible": dimension.possible,
                        "checks": [
                            {"label": check.label, "passed": check.passed}
                            for check in dimension.checks
                        ],
                    }
                    for dimension in case.dimensions
                },
            }
            for case in result.cases
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run the offline evaluation fixture and return a CI-friendly status."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help="Path to a JSON case fixture.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.85,
        help="Minimum score required for every case and dimension (default: 0.85).",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)
    if not 0.0 <= args.threshold <= 1.0:
        parser.error("--threshold must be between 0 and 1")

    result = evaluate_cases(load_cases(args.cases), threshold=args.threshold)
    if args.json:
        print(json.dumps(result_as_dict(result), indent=2, sort_keys=True))
    else:
        print(render_report(result))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
