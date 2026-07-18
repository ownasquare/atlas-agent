"""A small AST-interpreted calculator that never calls `eval`."""

from __future__ import annotations

import ast
import json
import math
import operator
from collections.abc import Callable
from typing import Any, cast

from langchain.tools import tool
from pydantic import BaseModel, ConfigDict, Field


class CalculatorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expression: str = Field(min_length=1, max_length=500)


_BINARY_OPERATORS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCTIONS: dict[str, Callable[..., float]] = {
    "abs": abs,
    "ceil": math.ceil,
    "cos": math.cos,
    "floor": math.floor,
    "log": math.log,
    "log10": math.log10,
    "max": max,
    "min": min,
    "round": round,
    "sin": math.sin,
    "sqrt": math.sqrt,
    "tan": math.tan,
}
_CONSTANTS = {"e": math.e, "pi": math.pi, "tau": math.tau}


class SafeCalculator(ast.NodeVisitor):
    """Interpret only numeric syntax from an explicit allowlist."""

    max_nodes = 80
    max_abs_value = 1e100
    max_exponent = 100

    def evaluate(self, expression: str) -> int | float:
        try:
            tree = ast.parse(expression, mode="eval")
        except (SyntaxError, RecursionError) as exc:
            raise ValueError("invalid mathematical expression") from exc
        if sum(1 for _ in ast.walk(tree)) > self.max_nodes:
            raise ValueError("expression is too complex")
        result = self.visit(tree)
        if isinstance(result, bool) or not isinstance(result, (int, float)):
            raise ValueError("expression did not produce a number")
        if isinstance(result, int) and len(str(abs(result))) > 101:
            raise ValueError("result is too large")
        if isinstance(result, float) and (
            not math.isfinite(result) or abs(result) > self.max_abs_value
        ):
            raise ValueError("result is not finite or is too large")
        return cast(int | float, result)

    def visit_Expression(self, node: ast.Expression) -> int | float:
        return cast(int | float, self.visit(node.body))

    def visit_Constant(self, node: ast.Constant) -> int | float:
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("only numeric literals are allowed")
        if isinstance(node.value, int) and len(str(abs(node.value))) > 100:
            raise ValueError("integer literal is too large")
        return node.value

    def visit_Name(self, node: ast.Name) -> float:
        try:
            return _CONSTANTS[node.id]
        except KeyError as exc:
            raise ValueError(f"unknown constant: {node.id}") from exc

    def visit_BinOp(self, node: ast.BinOp) -> int | float:
        operation = _BINARY_OPERATORS.get(type(node.op))
        if operation is None:
            raise ValueError("operator is not allowed")
        left = self.visit(node.left)
        right = self.visit(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > self.max_exponent:
            raise ValueError("exponent is too large")
        try:
            result = operation(left, right)
        except (ArithmeticError, OverflowError, ValueError) as exc:
            raise ValueError(f"calculation failed: {type(exc).__name__}") from exc
        return self._bounded(result)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> int | float:
        operation = _UNARY_OPERATORS.get(type(node.op))
        if operation is None:
            raise ValueError("unary operator is not allowed")
        return self._bounded(operation(self.visit(node.operand)))

    def visit_Call(self, node: ast.Call) -> int | float:
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise ValueError("function is not allowed")
        if node.keywords:
            raise ValueError("keyword arguments are not allowed")
        if not 1 <= len(node.args) <= 8:
            raise ValueError("function requires between one and eight arguments")
        arguments = [self.visit(argument) for argument in node.args]
        try:
            return self._bounded(_FUNCTIONS[node.func.id](*arguments))
        except (ArithmeticError, OverflowError, TypeError, ValueError) as exc:
            raise ValueError(f"function failed: {type(exc).__name__}") from exc

    def generic_visit(self, node: ast.AST) -> Any:
        raise ValueError(f"syntax is not allowed: {type(node).__name__}")

    def _bounded(self, value: Any) -> int | float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("operation did not produce a number")
        if isinstance(value, int) and len(str(abs(value))) > 101:
            raise ValueError("intermediate result is too large")
        if isinstance(value, float) and (
            not math.isfinite(value) or abs(value) > self.max_abs_value
        ):
            raise ValueError("intermediate result is too large")
        return cast(int | float, value)


def evaluate_expression(expression: str) -> int | float:
    return SafeCalculator().evaluate(expression)


@tool(args_schema=CalculatorInput)
def calculator(expression: str) -> str:
    """Calculate a numeric expression using safe arithmetic and approved math functions."""
    result = evaluate_expression(expression)
    return json.dumps({"expression": expression, "result": result}, ensure_ascii=False)
