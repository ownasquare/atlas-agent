# Evaluation methodology

Atlas keeps deterministic contracts, rendered behavior, and live-provider quality as separate
proof layers. A green result in one layer must not be presented as proof of another.

## Proof layers

| Layer | Measures | Does not prove |
| --- | --- | --- |
| Unit tests | Schemas, configuration, memory, and guarded tool behavior | End-to-end orchestration |
| Integration tests | Graph routing, durability, approvals, API, and streaming | Live-provider reliability |
| Offline agent evaluations | Recorded plan, trajectory, evidence, memory, and safety contracts | Future model quality |
| Browser validation | Fixture-backed workspace states, keyboard behavior, responsive layout, and automated axe rules | Credentialed task quality or formal accessibility conformance |
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

Install the browser runner once, then run the committed accessibility gate:

```bash
npm ci
npx playwright install chromium
make test-accessibility
```

The Playwright/axe matrix exercises setup-needed, working, completed, approval, rejection, and
recoverable-error states. It also checks keyboard focus, light and dark themes, horizontal reflow at
320, 375, 768, and 1440 pixels, primary target and functional-text sizes, text enlargement, reduced
motion, and forced colors. It uses deterministic local fixtures and does not call a model provider.

Automated rules cannot establish full WCAG conformance. Before making a conformance claim, perform
and record manual screen-reader reading-order and announcement checks, keyboard-only workflows,
400% zoom/reflow, and representative browser/assistive-technology combinations with real content.

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

The checked-in workflow installs locked dependencies and runs tests on Python 3.11, 3.12, and
3.13. Python 3.12 additionally runs lint, formatting, strict typing, offline evaluations, and the
package build; separate jobs run the Playwright/axe accessibility gate, Windows checks, and delivery
smokes. Remote CI status is established only after the workflow succeeds on the remote; the
presence of the workflow file alone is not remote proof.

See [Clean-clone v0.3.0 evidence](clean-clone-v0.3.0.md) for the immutable release baseline and its
explicit provider and accessibility boundaries.

## Live evaluation boundary

Live provider tests are opt-in because they require operator credentials and may incur cost. Record
the model and provider version, date, latency, token usage or cost, tool trajectory, sources,
artifacts, human score, and failures. Never print or store credential values.

Live-model success is not authentication, hosted readback, or production proof. Keep those release
layers separately labeled.
