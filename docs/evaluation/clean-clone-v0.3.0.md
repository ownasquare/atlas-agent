# Clean-clone evidence: v0.3.0

**Evidence date:** 2026-07-18 (America/Los_Angeles)

**Source:** public tag `v0.3.0` at commit `8ec154529ce4968e5519a1ef6c69f73559b35260`
**Environment:** fresh clone, fresh dependency environment, and isolated Atlas data paths

This is an immutable release baseline, not a claim about later commits.

## Verified locally

| Check | Result |
| --- | --- |
| `uv sync --locked` | Passed from the fresh checkout |
| `make check` | Ruff, formatting, strict MyPy, and 209 tests passed; branch coverage was 85.54% |
| `make eval` | 14 of 14 deterministic evaluations passed |
| `uv build` | Wheel and source distribution built successfully |
| CLI smoke | Help, doctor, memory list, and graph commands passed |
| Key-free server smoke | `/api/health` and `/` returned HTTP 200 on loopback |
| Installed-wheel smoke | The built wheel installed in a separate environment; Atlas reported version 0.3.0 and used the isolated data path |

The run did not reuse the maintainer's virtual environment, repository `.env`, or Atlas data. The
doctor and server checks did not reveal credential values.

## Boundaries and discovered limitation

No OpenAI or Anthropic credential was available, so no live-provider task was attempted. This
evidence does not prove live model quality, external search availability, hosted behavior,
authentication, production readiness, or formal accessibility conformance. The v0.3.0 tag also
predates the committed Playwright/axe gate described in [Evaluation methodology](evaluation.md).

The clean-clone run found one adoption issue in the released tag: starting a task without a model
credential exited with a long provider traceback. The post-release adoption work adds a secret-safe
setup preflight so the workspace, CLI, and API explain the missing setup before task execution. That
fix belongs to a later commit and does not change this v0.3.0 record.

## Repeat the baseline

```bash
git clone --branch v0.3.0 https://github.com/ownasquare/atlas-agent.git atlas-agent-clean
cd atlas-agent-clean
cp .env.example .env
uv sync --locked
uv run atlas doctor
make check
make eval
uv build
```

An unconfigured doctor result is expected in a key-free environment. Treat a live-provider smoke,
browser accessibility gate, container smoke, and hosted CI run as separate evidence layers.
