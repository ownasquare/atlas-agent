.PHONY: install test test-unit lint typecheck check serve demo eval graph

install:
	uv sync

test:
	uv run pytest --cov=atlas_agent --cov-report=term-missing

test-unit:
	uv run pytest tests/unit -q

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy src

check: lint typecheck test

serve:
	uv run atlas serve --reload

demo:
	uv run atlas run "Research LangGraph memory from official sources and save a cited brief to langgraph-memory.md"

eval:
	uv run python evals/run_evals.py --threshold 1.0

graph:
	uv run atlas graph
