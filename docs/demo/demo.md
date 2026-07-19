# Recruiter demo

This walkthrough demonstrates the local Atlas application. It does not represent a hosted service, public authentication boundary, production deployment, or live-provider reliability proof.

## Two-minute setup

```bash
cp .env.example .env
# Add OPENAI_API_KEY to .env
uv sync --locked
uv run atlas serve
```

File-replacement approval works with this default setup. To include Python in the demo, first pull
`python:3.12-alpine`, set `ATLAS_CODE_EXECUTION_BACKEND=docker` in `.env`, and restart Atlas.

Open `http://127.0.0.1:8000`. Keep a terminal available with `uv run atlas graph` for the technical follow-up rather than leading with the implementation.

## Ninety-second product walkthrough

1. **Start with the work.** Point out the conventional New task action, readable setup/readiness state, recent-task navigation, and System/Light/Dark theme control. The primary UI does not require graph, model, namespace, or thread terminology.
2. **Submit a useful task.** Choose **Research a topic**, then start the task. Atlas uses the public SSE route to update the plan and plain-language Activity panel as the durable graph runs.
3. **Review the outcome.** Show the plan, readable result, source links, and honest Complete or Needs review status. Confirm that source and artifact evidence is extracted from successful tool results rather than trusted from model prose.
4. **Open the deliverable.** Select the generated file in **Files**, inspect its bounded text preview, copy it, and download it. The file browser delegates to the same confined workspace service as the tools and is read-only.
5. **Demonstrate control.** Ask Atlas to replace an existing file. The task pauses durably and presents a plain-language decision. Open action details if the reviewer wants the raw scope, reject once, then rerun and choose **Allow once**. If the optional Docker setup above is enabled, a Python task demonstrates the same approval flow.
6. **Resume work naturally.** Start another task, then select the first item under Recent tasks. Explain that the browser stores at most eight local shortcuts per profile while SQLite—not `localStorage`—holds the durable conversation state.
7. **Show saved context.** Ask Atlas to remember a reporting preference, inspect **Saved context**, and reuse the same local profile in a new task. Change the profile under Workspace settings only when demonstrating isolation.
8. **Close on engineering proof.** Expand **Engineering details** or show `uv run atlas graph`. Discuss the explicit planner/tool/reviewer nodes, deterministic evaluations, branch-coverage gate, guarded tools, durable approvals, and non-root read-only application container.

## Suggested tasks

### Cited research artifact

> Research the current LangGraph persistence and interrupt patterns using official sources. Compare them in a concise table and save a cited brief to `langgraph-runtime.md`.

Expected trajectory: `web_search` → optional calculator/file discovery → `write_file` → reviewer → cited result. The resulting file should appear in the local Files panel after refresh.

### Tool composition after Docker opt-in

> Read `quarterly-data.csv`, calculate the average and percent change with Python, and save an executive summary to `quarterly-summary.md`.

Place a safe sample CSV in `.atlas/workspace` first; the Phase 2 browser is intentionally read-only and does not upload files. Expected trajectory: `read_file` → approval → `execute_python` → `write_file`.

### Durable preference

> For future reports, remember that I prefer Canadian dollars and a three-bullet executive summary.

Then choose New task with the same local profile and ask for a report format. The user-scoped local vector index should supply the preference.

## What the browser stores

The browser-local recent-task record contains only the information needed to present and reopen up to eight shortcuts for one profile: thread reference, title or excerpt, status, and updated time. It is a convenience index:

- it does not contain the durable message transcript;
- it does not synchronize across browsers or devices;
- it is not authentication or authorization;
- clearing it does not delete SQLite checkpoints, workspace files, or saved vector memories.

The explicit theme preference is also browser-local. System theme remains the default when no preference has been saved.

## Artifact boundary

The local UI exposes three read-only operations over the configured workspace: bounded listing, bounded UTF-8 preview, and bounded attachment download. Each request uses a relative confined path and inherits hidden-file, traversal, symlink, scan, and size controls. Previewed content is rendered as text, not executable HTML or Markdown. The browser cannot edit, rename, delete, execute, or upload artifacts.

These controls reduce local accidents; they are not a substitute for authentication. The workspace endpoints must not be exposed to untrusted users until identity and authorization are derived and enforced by the server.

## Credential-free fallback

If live provider access is unavailable, start the local shell with model-dependent execution unavailable and show the honest Setup needed notice, theme/responsive behavior, Engineering details, OpenAPI schema, deterministic test suite, and offline evaluator:

```bash
ATLAS_MEMORY_ENABLED=false ATLAS_CODE_EXECUTION_BACKEND=disabled uv run atlas serve
uv run pytest -q
uv run python evals/run_evals.py
```

Do not present a credential-free shell or mocked/deterministic result as a live model completion. It proves local rendering and deterministic contracts only.

## Talking points

- “The primary experience is a task workspace; orchestration details remain one disclosure away for technical reviewers.”
- “SQLite keeps each task durable, while the eight-item browser index only makes recent work easy to find.”
- “A small local SQLite vector index keeps curated cross-task context separate from the exact conversation checkpoint.”
- “Generated files use the same confined workspace boundary for tools, safe preview, and attachment download.”
- “Interrupts persist before a risky effect, and the person sees a clear allow-once or reject decision.”
- “The Docker backend has no network, a read-only root, no capabilities, and a non-root user.”
- “External content is untrusted, and only successful tool messages become confirmed evidence.”
- “The same runtime powers the CLI, API, workspace, tests, and Studio graph.”
