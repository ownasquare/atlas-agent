# Contributing to Atlas

Thanks for helping make Atlas easier to use, safer to run, and simpler to extend. Participation is
governed by the [Code of Conduct](CODE_OF_CONDUCT.md).

## Before you start

- Use an issue to discuss large features, public API changes, or security-boundary changes first.
- Use the private path in [SECURITY.md](SECURITY.md) for vulnerabilities or sensitive reports.
- Keep pull requests focused. Unrelated cleanup makes safety review harder.

## Development setup

You need Python 3.11–3.13 and [uv](https://docs.astral.sh/uv/).

macOS/Linux:

```bash
cp .env.example .env
uv sync --locked --all-extras --dev
uv run atlas doctor
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
uv sync --locked --all-extras --dev
uv run atlas doctor
```

Credentials are not required for deterministic tests. Code execution is disabled by default; see
[Getting started](docs/getting-started.md) before opting in to Docker.

## Make a change

1. Add or update a focused test for deterministic behavior.
2. Preserve path confinement, approval, redaction, and evidence boundaries.
3. Update the nearest user or extension guide when behavior changes.
4. Run the local quality gates.

```bash
make check
make eval
uv build
```

The equivalent portable commands, including on Windows PowerShell, are:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest --cov=atlas_agent --cov-report=term-missing
uv run python evals/run_evals.py --threshold 1.0
uv build
```

Use Ruff for formatting and linting and keep strict MyPy checks green. New end-to-end browser tests
belong in Playwright; do not add Cypress E2E tests.

## Extension changes

Read [Extending Atlas](docs/extending.md) before adding tools, evidence extractors, providers, or
memory stores. Custom tool output is untrusted by default. A custom source or artifact becomes
confirmed evidence only through an explicitly registered extractor with tests.

## Pull requests

In the pull request, explain:

- the user-visible outcome;
- the reason for the change;
- the files or boundaries affected;
- the exact validation run;
- whether proof is local, live-provider, hosted, or production.

Do not commit `.env`, API keys, local `.atlas` state, generated credentials, or sensitive logs. A
maintainer may ask for a smaller change or additional proof before review.

## Getting help

Check [Troubleshooting](docs/troubleshooting.md) and existing issues before opening a new question.
When sharing diagnostics, include command status and software versions, but never credential values.
