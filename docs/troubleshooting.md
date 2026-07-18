# Troubleshooting

Start with:

```bash
uv run atlas doctor
```

The report checks credential presence and installed integrations without printing credential
values. It does not contact a model provider, initialize vector memory, or prove path writability.
When Docker is selected, it checks the command, daemon, and expected local image. Use `--json`
when you need structured diagnostics, and sanitize paths or user data before sharing its output.

## Setup decision table

| Symptom | What to do |
| --- | --- |
| Model is not ready | Confirm `ATLAS_MODEL` uses `provider:model-id`, add the matching provider key to `.env`, restart Atlas, and rerun the doctor. |
| OpenAI task cannot start | Confirm `OPENAI_API_KEY` is present in `.env`; do not paste its value into an issue. |
| Anthropic integration is missing | Run `uv sync --locked --extra anthropic`, set an `anthropic:<model-id>`, and restart. |
| Provider is unsupported | Install the matching LangChain provider integration and follow [Extending Atlas](extending.md); unknown providers are not assumed ready. |
| Python is unavailable | This is the safe default. Most Atlas tasks work without it. Enable Docker only if the task needs Python. |
| Docker Python fails | Pull `python:3.12-alpine`, confirm Docker is running, set `ATLAS_CODE_EXECUTION_BACKEND=docker`, and rerun the doctor. |
| Memory is slow on first use | Chroma may download its local embedding model once. Check network access and local cache permissions. |
| Port 8000 is in use | Start with `uv run atlas serve --port 8001`, then open `http://127.0.0.1:8001`. |
| A task waits for a decision | Review the displayed action and choose **Allow once** or **Don't allow**. Atlas will not choose for you. |
| Recent tasks disappeared | Browser shortcuts were cleared or a different profile is selected. SQLite checkpoints are separate from those shortcuts. |
| A file is not visible | Atlas lists only bounded, non-hidden files inside the configured workspace. Symlinks and escaped paths are rejected. |

## Installation problems

Confirm Python and uv first:

```bash
python --version
uv --version
```

Atlas supports Python 3.11–3.13. Install exactly the locked project dependencies with:

```bash
uv sync --locked
```

If the lockfile and project metadata disagree, update the checkout rather than regenerating the lock
as an onboarding workaround.

## Task failures

A **Needs review** or partial result means the bounded review cycle ended with requirements still
open. It is not the same as a completed result. Retry with a narrower request or address the stated
missing requirement.

Search and model providers are external services. A local health check cannot prove their current
availability or answer quality. If one fails, preserve the error category and retry later; never add
credentials to logs.

## Safe issue reports

Before opening a bug, run the smallest reproducing command and record:

- Atlas version or commit;
- Python, uv, operating system, and interface used;
- sanitized doctor status;
- expected and observed behavior;
- whether Docker, memory, or a live provider was involved.

Remove API keys, tokens, private prompts, private files, absolute personal paths, and user data. Use
[SECURITY.md](../SECURITY.md) instead of a public issue when the report describes a vulnerability.
