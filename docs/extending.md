# Extending Atlas

Atlas keeps extensions explicit: a tool may run without becoming trusted evidence, memory backends must preserve user boundaries, and every public feature keeps one typed contract across the API and browser.

## Extension map

| Change | Primary files | Minimum proof |
| --- | --- | --- |
| Custom tool | Your extension module and runtime entry point | Schema/output unit test and registry test |
| Built-in tool | `src/atlas_agent/tools/<name>.py` and `src/atlas_agent/tools/registry.py` | Tool, registry, graph, and safety tests |
| Evidence | Tool bundle `evidence_extractors`, `src/atlas_agent/graph.py` | Positive extraction and unregistered-output tests |
| Model provider | `pyproject.toml`, `src/atlas_agent/config.py`, `.env.example` | Missing/configured readiness and lazy-client tests |
| Memory backend | `src/atlas_agent/memory.py`, runtime or Studio wiring | Persistence, user isolation, redaction, and CRUD tests |
| API capability | `src/atlas_agent/schemas.py`, `src/atlas_agent/api.py` | Success, validation, authorization boundary, and failure tests |
| Browser feature | `src/atlas_agent/static/index.html`, `src/atlas_agent/static/app.js`, `src/atlas_agent/static/styles.css` | Static safety contract and rendered keyboard/mobile proof |

## Add a tool

1. Define a Pydantic input model with `extra="forbid"` and explicit size/range limits.
2. Return bounded JSON. Never return raw exceptions, credentials, absolute host paths, or unlimited provider content.
3. Pass the `BaseTool` through `build_tool_bundle(extra_tools=[...])`. Extra tools are sorted by name; duplicate built-in or custom names fail during startup.
4. Use a valid planner hint name: 1–64 characters, beginning with a letter and containing only letters, numbers, `_`, or `-`.
5. Pass the bundle to `open_runtime(tool_bundle=bundle)` from your own entry point. Atlas does not dynamically import arbitrary modules from environment variables.
6. Add a unit test for valid output and invalid input. Add a graph test when the tool has side effects, approvals, unusual failures, or new evidence semantics.

Run the complete key-free example:

```bash
uv run python examples/custom_tool.py
```

The example registers `text_stats`, invokes it directly, and prints its typed result. It makes no model, live-provider, persistent workspace-file, or network call; temporary local directories are discarded when it exits.

## Register evidence deliberately

Tool output is untrusted by default. Atlas will not turn a custom tool's URLs or paths into final
citations or artifacts unless its name has an explicit typed extractor in the same tool bundle.
The following is an entry-point excerpt: `settings` is your validated `Settings` instance and
`custom_report` is your already-defined `BaseTool`.

```python
from collections.abc import Mapping
from typing import Any

from atlas_agent.schemas import ToolEvidence
from atlas_agent.tools.registry import build_tool_bundle


def extract_report(payload: Mapping[str, Any]) -> ToolEvidence:
    if payload.get("status") != "succeeded" or not isinstance(payload.get("path"), str):
        return ToolEvidence()
    return ToolEvidence(artifacts=[payload["path"]])


bundle = build_tool_bundle(
    settings,
    extra_tools=[custom_report],
    evidence_extractors={"custom_report": extract_report},
)
```

`ToolEvidence` accepts bounded HTTP(S) source URLs and safe workspace-relative artifact paths. The extractor must validate the tool's success contract before returning either. Ordinary calculations or transformations need no extractor because they create no independent source or artifact. The runnable `text_stats` example registers an empty extractor only to demonstrate that boundary without making a false evidence claim.

## Add a model provider

Atlas uses LangChain's provider-qualified model format. For a custom provider:

1. Install and record the matching LangChain integration package, preferably as an optional dependency extra.
2. Set `ATLAS_MODEL` to `provider:model-name` and configure the provider using its documented secret name.
3. After the package and credential are configured, set `ATLAS_CUSTOM_MODEL_CONFIGURED=true` to opt into readiness. Unknown providers remain blocked by default so the workspace cannot falsely claim it is ready.
4. Add tests for missing credentials, configured readiness, lazy construction, and sanitized failures. Live provider checks remain separate, opt-in proof.

For a non-LangChain model or different models per role, implement the five async methods on
`AgentBrain` in `src/atlas_agent/brain.py` and pass that implementation to
`open_runtime(brain=...)`.

## Replace saved vector memory

Implement the public `MemoryStore` protocol in `src/atlas_agent/memory.py`: `add`, `search`,
`list`, `delete`, and `clear`. Every operation must remain scoped by `user_id`; writes must preserve
redaction and bounded `MemoryCandidate` / `MemoryRecord` contracts.

Pass an instance to `open_runtime(memory=...)`. If LangGraph Studio should use it too, replace the
memory construction in `src/atlas_agent/studio.py`. Prove restart persistence, duplicate handling,
cross-user isolation, secret redaction/drop behavior, and API CRUD before enabling the backend by
default.

Conversation memory is separate: replacing saved vector memory does not replace the LangGraph checkpointer or its `(user_id, thread_id)` namespace.

## Add an API or browser feature

Start with a strict request/response model in `src/atlas_agent/schemas.py`. Add the route in
`create_app()` in `src/atlas_agent/api.py` and test success plus bounded validation and failure
responses with a deterministic runtime override. Keep server-derived authorization as a
prerequisite before public hosting.

The dependency-free browser uses `textContent`/DOM nodes rather than raw-model `innerHTML`. Add the smallest markup and state needed, keep technical detail behind progressive disclosure, provide keyboard focus and readable error/empty/loading states, and update the deterministic UI fixture. Validate desktop and mobile behavior in the browser in addition to static source tests.

## Validation

Run focused tests while developing, then the full local contract before opening a pull request:

```bash
uv run pytest tests/unit/tools tests/integration/test_graph.py tests/unit/test_memory.py -q
make check
make eval
uv build
```

Tests and fixture-backed browser proof do not establish live-provider or hosted-production reliability; label those layers separately.
