# Safety model

## Scope

Atlas is a local-first portfolio application. Its controls demonstrate safe agent engineering, but they do not turn an unauthenticated local service into a public multi-tenant platform. Authentication, authorization, network rate limiting, abuse controls, managed secrets, and production observability are deployment prerequisites.

## Threat model

Atlas assumes that all of the following may be hostile or misleading:

- user prompts;
- web search snippets and URLs;
- file contents;
- recalled vector-memory text;
- model-proposed Python;
- model-proposed file paths;
- tool error text and oversized output.

The design protects the host workspace and durable user boundaries while preserving explicit human control over high-impact local actions.

## Controls

| Risk | Control | Validation |
| --- | --- | --- |
| Path traversal | Resolve against one workspace root and verify ancestry | Traversal and absolute-path tests |
| Symlink escape | Reject symlinks in every resolved path component | Symlink escape tests |
| Hidden or secret files | Reject dot-prefixed components and keep `.env` outside tool root | File policy tests |
| Accidental overwrite | Nonce-bound interrupt plus explicit absent/existing file-state token before any overwrite intent | Changed/created/deleted-while-paused integration tests |
| Concurrent file mutation | NFKC/casefold canonical per-path lock encloses both fingerprint checks and atomic replace | Same-spelling and case-alias multi-writer tests |
| Arbitrary Python | Docker isolation, AST import/name/introspection/pattern policy, and explicit approval | Source-policy and interrupt tests |
| Host/network access | Docker uses no network, read-only root, dropped capabilities, non-root UID | Command-construction tests; local image required |
| Runaway computation | Wall/CPU/memory/process/file/output limits | Timeout and truncation tests |
| Calculator code injection | Purpose-built AST interpreter, never Python `eval` | Injection and complexity tests |
| Cross-user memory | Mandatory user filters on search/list/delete/clear | Isolation tests |
| Credential persistence | Pattern redaction before vectorization and SQLite upsert | Redaction/drop tests |
| Prompt injection | External text is labeled untrusted; evidence comes only from tool messages | Prompt and evidence tests |
| Infinite loops/cost | Action, review, search, file, and output budgets | Budget integration tests |
| Information leakage | Sanitized tool errors and credential-presence-only health response | API/tool tests |

## Python execution backend

The hardened local backend runs `python:3.12-alpine` with:

- `--network none`;
- read-only filesystem;
- all Linux capabilities dropped;
- `no-new-privileges`;
- process, memory, CPU, and temporary-filesystem limits;
- an unprivileged numeric user;
- isolated Python mode and a clean environment.

Atlas does not automatically pull images during a task. Pull the version-tagged runtime image as an explicit operator action. The tag is convenient for this local demo but is not an immutable digest; production operators should pin an approved multi-architecture digest. This also avoids a model-triggered network mutation. Timeout cleanup force-removes the named sandbox, tolerates an already-exited or permission-inaccessible client process group, and bounds the final client wait.

The workspace service defends against model-controlled paths, concurrent create-new races, and multiple Atlas processes spending the same approved fingerprint. It assumes other local processes with write access to the workspace root are trusted; a hostile process that can swap directory entries requires an OS-level sandbox or directory-descriptor-based filesystem broker.

There is no automatic host-process fallback. Use `ATLAS_CODE_EXECUTION_BACKEND=disabled` on a public or shared host unless a dedicated sandbox service is available. Never mount the host Docker socket into the provided application container.

## Human approval semantics

Approvals are fail-closed. The default response in the CLI is rejection. Code execution and overwriting existing files pause through a durable LangGraph interrupt; a server restart or disconnected browser does not silently approve the action. Rejection becomes a structured tool result so the graph can adapt without executing the proposed effect.

Each decision must name the exact pending interrupt ID. Every `overwrite=true` decision also echoes the reviewed file-state token, including an explicit token when the target was absent. A target created, changed, or deleted while approval is pending makes the decision stale; the tool neither replaces nor recreates it. Per-thread async and filesystem locks prevent concurrent local runtimes sharing one data directory from racing the same checkpoint.

An approval authorizes exactly the paused tool call. `edited_arguments` may replace the proposed code or file content through the public schema, enabling a reviewer to narrow the action before it runs.

## Production hardening checklist

- Authenticate every request and derive tenant identity server-side.
- Authorize thread and memory access against that tenant identity.
- Add per-tenant rate, token, tool, storage, and concurrency limits.
- Use a managed checkpoint database and encrypted vector store.
- Replace local execution with an ephemeral sandbox service.
- Add outbound-domain policy and search-provider allowlists where required.
- Store API keys in a managed secret service and rotate them.
- Emit tamper-resistant audit events for prompts, tool decisions, approvals, and mutations.
- Add content/data-retention policy, deletion workflows, and incident response.
- Run live red-team, dependency, container, and infrastructure scans before release.
