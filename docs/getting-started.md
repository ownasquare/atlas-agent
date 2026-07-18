# Getting started

This guide takes a local checkout from configuration to one successful Atlas task. Atlas is not a
hosted service; all application state stays in directories you configure on the local machine.

## Prerequisites

- Python 3.11, 3.12, or 3.13
- [uv](https://docs.astral.sh/uv/)
- An API key for the model provider you choose
- Docker Desktop or Docker Engine only if you explicitly enable Python analysis

## Four-step setup

From the repository root:

1. Create the local environment file on macOS/Linux.

   ```bash
   cp .env.example .env
   ```

   On Windows PowerShell:

   ```powershell
   Copy-Item .env.example .env
   ```

2. Configure one provider in `.env`.

   OpenAI is the default:

   ```dotenv
   ATLAS_MODEL=openai:gpt-4.1-mini
   OPENAI_API_KEY=replace-with-your-key
   ```

   For Anthropic, use a provider-qualified model ID supplied by Anthropic:

   ```dotenv
   ATLAS_MODEL=anthropic:<model-id>
   ANTHROPIC_API_KEY=replace-with-your-key
   ```

3. Install and check the local application.

   ```bash
   uv sync --locked
   uv run atlas doctor
   ```

   If you configured Anthropic, replace the sync command above with
   `uv sync --locked --extra anthropic` so its optional integration is installed.

   The doctor checks credential presence, the installed model integration, selected local paths,
   memory mode, and the optional Python backend without printing credential values. It does not
   contact the model provider, initialize vector memory, or prove path writability. When Docker is
   selected, it checks the command, daemon, and expected local image. Use
   `uv run atlas doctor --json` for structured local diagnostics.

4. Start Atlas.

   ```bash
   uv run atlas serve
   ```

Open `http://127.0.0.1:8000`.

## First successful task

Enter this in the workspace:

> Research the current LangGraph persistence options using official sources and prepare a concise
> recommendation.

The task should move through a visible plan and activity sequence, then return a reviewed result
with confirmed source links. This task does not require Docker.

For a terminal-only first task:

```bash
uv run atlas run "Compare three current approaches to agent memory and return a cited recommendation."
```

## Search configuration

Atlas uses DDGS when no search key is configured. To use Tavily, set `TAVILY_API_KEY` in `.env` and
restart Atlas. Search-provider output remains untrusted input regardless of provider.

## Enable Python with Docker

Python execution is disabled by default. Pull the expected sandbox image explicitly:

```bash
docker pull python:3.12-alpine
```

Set the backend in `.env`:

```dotenv
ATLAS_CODE_EXECUTION_BACKEND=docker
```

Restart Atlas and run `uv run atlas doctor`. Atlas does not run generated code on the host and does
not silently pull an image during a task. Every Python request still requires a human decision by
default.

## Local data

By default, Atlas uses:

- `.atlas/data` for SQLite checkpoints and vector memory;
- `.atlas/workspace` for task-created files;
- browser local storage for recent-task shortcuts and the theme choice.

Clearing browser storage does not delete checkpoints, files, or vector memory. Do not commit `.env`
or `.atlas` contents.

## Next guides

- [Troubleshooting](troubleshooting.md)
- [Extending Atlas](extending.md)
- [Safety model](safety/safety.md)
- [Evaluation methodology](evaluation/evaluation.md)
