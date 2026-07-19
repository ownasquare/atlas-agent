.PHONY: install install-dev test test-unit test-e2e test-accessibility lint typecheck check serve demo eval graph

install:
	uv sync --locked

install-dev:
	uv sync --locked --all-extras --dev

test:
	uv run pytest --cov=atlas_agent --cov-report=term-missing

test-unit:
	uv run pytest tests/unit -q

test-e2e:
	npm run test:e2e

test-accessibility:
	npm run test:e2e:accessibility

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
