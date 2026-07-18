# Evaluation methodology

Atlas keeps deterministic contracts, rendered behavior, and live-provider quality as separate
proof layers. A green result in one layer must not be presented as proof of another.

## Proof layers

| Layer | Measures | Does not prove |
| --- | --- | --- |
| Unit tests | Schemas, configuration, memory, and guarded tool behavior | End-to-end orchestration |
| Integration tests | Graph routing, durability, approvals, API, and streaming | Live-provider reliability |
| Offline agent evaluations | Recorded plan, trajectory, evidence, memory, and safety contracts | Future model quality |
| Browser validation | Workspace layout and interaction states | Credentialed task quality |
| Live evaluation | Explicitly authorized model and search behavior | Hosted or production readiness |

## Run the local gates

```bash
make check
make eval
uv build
```

`make check` is the source of truth for the current test count and branch coverage. Run it against
the exact commit being reviewed rather than copying an older count into new documentation. The
coverage gate is configured in `pyproject.toml`.

The equivalent focused commands are:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest --cov=atlas_agent --cov-report=term-missing
uv run python evals/run_evals.py
```

## Key-free evaluations

`evals/cases.json` contains deterministic workflow and safety contracts. Workflow cases compare a
recorded run with only the dimensions that matter. Safety cases call Atlas validators in temporary
workspaces without executing generated code or requiring the internet.

The evaluator covers:

- plan structure and ordering;
- expected and forbidden tool trajectories;
- completion status and answer requirements;
- artifact paths and expected content markers;
- confirmed source URLs;
- saved-memory decisions and redaction;
- calculator, filesystem, and Python-source safety policies.

Run it with the default threshold, structured output, or an explicit threshold:

```bash
uv run python evals/run_evals.py
uv run python evals/run_evals.py --json
uv run python evals/run_evals.py --threshold 0.90
```

## Add a regression

1. Reduce the failure to the smallest prompt or input that reproduces it.
2. Add a unit or integration test when the behavior is deterministic code.
3. Add an evaluation case only for agent-level behavior that benefits from a recorded contract.
4. Prefer a real validator probe over a written safety claim.
5. Run `make check` and `make eval`; inspect the per-case result before accepting a changed fixture.

## Continuous integration

The checked-in workflow installs locked dependencies, then runs lint, formatting, strict typing,
tests, offline evaluations, and package builds across the supported Python versions. Remote CI
status is established only after the project is published and the workflow succeeds on that remote;
the presence of the workflow file alone is not remote proof.

## Live evaluation boundary

Live provider tests are opt-in because they require operator credentials and may incur cost. Record
the model and provider version, date, latency, token usage or cost, tool trajectory, sources,
artifacts, human score, and failures. Never print or store credential values.

Live-model success is not authentication, hosted readback, or production proof. Keep those release
layers separately labeled.
