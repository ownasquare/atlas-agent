# Atlas Agent

**Atlas is a local task workspace that plans complex work, safely uses tools, reviews the outcome, and remembers useful context.**

[![CI](https://github.com/ownasquare/atlas-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/ownasquare/atlas-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Atlas combines a LangGraph workflow with web research, calculations, confined file access,
optional Docker-isolated Python, durable conversations, and user-scoped memory. Use the focused
web workspace, the CLI, or the FastAPI interface without learning graph terminology first.

> Atlas 0.3.0 is a local-first open-source project. It is not a hosted service and does not include
> authentication. Keep it on a trusted machine or loopback network.

## What you can do

- Break an open-ended request into a short, reviewable plan.
- Research current information and preserve the source links used.
- Calculate results and create files inside one confined workspace.
- Pause before approved high-impact actions, including Python execution and file replacement.
- Resume durable tasks and recall curated context across tasks.
- Inspect results through a calm web workspace, CLI, or API.

## Local quickstart

You need Python 3.11–3.13, [uv](https://docs.astral.sh/uv/), and an OpenAI API key.

```bash
git clone https://github.com/ownasquare/atlas-agent.git
cd atlas-agent
```

Then complete these four steps.

1. Create your local configuration on macOS/Linux:

   ```bash
   cp .env.example .env
   ```

   On Windows PowerShell:

   ```powershell
   Copy-Item .env.example .env
   ```

2. Add `OPENAI_API_KEY` to `.env`, then install the locked dependencies:

   ```bash
   uv sync --locked
   ```

3. Check readiness without revealing credential values:

   ```bash
   uv run atlas doctor
   ```

4. Start the workspace:

   ```bash
   uv run atlas serve
   ```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). For Anthropic, alternative setup, and
first-run explanations, see [Getting started](docs/getting-started.md).

## Complete your first task

In the workspace, choose **Research a topic** or enter:

> Research the current LangGraph persistence options using official sources and prepare a concise
> recommendation.

Atlas will show the plan, activity, reviewed result, and confirmed sources in one task view. You
can also run a one-shot task from another terminal:

```bash
uv run atlas run "Compare three current approaches to agent memory and return a cited recommendation."
```

## Optional Python analysis

Code execution is disabled by default. Atlas never falls back to running generated Python directly
on the host. To opt in to the local Docker sandbox:

```bash
docker pull python:3.12-alpine
```

Then set `ATLAS_CODE_EXECUTION_BACKEND=docker` in `.env`, restart Atlas, and run
`uv run atlas doctor` again. Docker is not required for research, calculations, memory, or file
work that does not need Python.

## How Atlas works

```text
request → recall → plan → work with tools → review → result → remember
```

The graph has bounded action and review loops. Separate SQLite files store exact task state and a
small vector index of curated cross-task context. The index uses deterministic feature hashing, so
it needs no network call or embedding-model download. Successful tool messages—not model prose—
establish confirmed sources and created files.

| Capability | Default behavior |
| --- | --- |
| Web research | DDGS without a key; Tavily when configured |
| Calculator | Restricted arithmetic parser; no Python `eval` |
| Files | Confined to `.atlas/workspace`; hidden paths and traversal rejected |
| Python | Disabled until Docker is explicitly enabled |
| Conversation state | Durable local SQLite checkpoints |
| Saved context | User-scoped SQLite vector memory |

## Useful commands

```bash
uv run atlas doctor            # Explain local readiness
uv run atlas chat              # Start a durable terminal conversation
uv run atlas memory list       # Inspect saved context
uv run atlas graph             # Print the compiled Mermaid graph
make check                     # Lint, type-check, and test
make eval                      # Run key-free agent evaluations
uv build                       # Build the wheel and source archive
```

The OpenAPI reference is available at `http://127.0.0.1:8000/docs` while Atlas is running.
`make` is a macOS/Linux convenience; Windows contributors can run the portable `uv run`
commands listed in [Contributing](CONTRIBUTING.md).

## Documentation

| Guide | Use it for |
| --- | --- |
| [Getting started](docs/getting-started.md) | Provider setup, first task, and Docker opt-in |
| [Troubleshooting](docs/troubleshooting.md) | Readiness and common local failures |
| [Extending Atlas](docs/extending.md) | Custom tools, evidence, memory, providers, and tests |
| [Architecture](docs/architecture/architecture.md) | Graph, state, memory, and interface design |
| [Safety model](docs/safety/safety.md) | Trust boundaries and tool controls |
| [Evaluation](docs/evaluation/evaluation.md) | Tests, offline evals, and proof boundaries |
| [Contributing](CONTRIBUTING.md) | Development workflow and pull requests |
| [Support](SUPPORT.md) | Self-help order and issue guidance |
| [Security](SECURITY.md) | Private vulnerability reporting guidance |

## Safety and limits

- Atlas is local-first and unauthenticated. Do not expose it to untrusted networks or users.
- Search results, files, memories, and tool output are treated as untrusted input.
- File access is bounded to the configured workspace; browser file access is read-only.
- Python requires explicit approval and the Docker backend; keep it disabled when unnecessary.
- Recent-task shortcuts are browser-local, not account history or an authorization boundary.
- Local tests do not prove live-provider quality, hosted behavior, or production readiness.
- Saved context stays in a separate local SQLite vector index and is scoped by local profile.

See [Troubleshooting](docs/troubleshooting.md) before opening an issue. Please use the private path
described in [Security](SECURITY.md) for vulnerabilities or sensitive reports.

Atlas is available under the [MIT License](LICENSE).
