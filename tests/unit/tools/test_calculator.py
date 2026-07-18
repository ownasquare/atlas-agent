"""Security and correctness tests for the AST calculator."""

from __future__ import annotations

import json

import pytest

from atlas_agent.tools.calculator import calculator, evaluate_expression


def test_evaluate_expression_honors_precedence_and_approved_math() -> None:
    assert evaluate_expression("2 + 3 * 4") == 14
    assert evaluate_expression("sqrt(81) + round(pi, 2)") == pytest.approx(12.14)
    assert evaluate_expression("max(2, 5, -1) - min(2, 5, -1)") == 6


def test_calculator_tool_returns_structured_json() -> None:
    payload = json.loads(calculator.invoke({"expression": "(7 - 2) ** 2"}))

    assert payload == {"expression": "(7 - 2) ** 2", "result": 25}


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os')",
        "(1).__class__",
        "[value for value in [1]]",
        "lambda: 1",
        "1 < 2",
        "sqrt(value=4)",
        "True",
    ],
)
def test_evaluate_expression_rejects_non_numeric_syntax(expression: str) -> None:
    with pytest.raises(ValueError):
        evaluate_expression(expression)


@pytest.mark.parametrize(
    "expression",
    [
        "2 ** 101",
        "9" * 101,
        "1e309",
    ],
)
def test_evaluate_expression_rejects_unbounded_values(expression: str) -> None:
    with pytest.raises(ValueError):
        evaluate_expression(expression)


def test_evaluate_expression_rejects_excessive_ast_complexity() -> None:
    expression = " + ".join(["1"] * 50)

    with pytest.raises(ValueError, match="too complex"):
        evaluate_expression(expression)


@pytest.mark.parametrize("expression", ["1 / 0", "sqrt(-1)", "log(0)"])
def test_evaluate_expression_normalizes_math_failures(expression: str) -> None:
    with pytest.raises(ValueError):
        evaluate_expression(expression)
