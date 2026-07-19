# Phase 2 Workspace Product Contract

**Status:** implemented local product contract
**Scope:** local Atlas web workspace
**Proof boundary:** local source, automated tests, and local browser validation only

## Product experience

Phase 2 turns the control-room demo into a familiar task workspace. The default view must prioritize what a person wants to do, what is happening, and what was produced. Framework names, graph internals, model identifiers, thread IDs, SQLite details, and raw tool payloads belong behind explicit **Technical details** or **How it works** disclosures.

The workspace has four primary surfaces:

1. **Recent tasks:** a compact navigation list with New task, title, updated time, and plain-language status.
2. **Task composer:** the first meaningful action, labelled “What would you like to accomplish?” and visible in the initial viewport.
3. **Task activity and result:** a concise progress summary followed by a readable result, sources, and artifacts.
4. **Decision request:** a focused approval surface that explains the action, consequence, and safe choices in plain language.

Use solid, conventional application surfaces, readable type, familiar icons, and one restrained accent. Decorative gradients, glass effects, tiny uppercase labels, anthropomorphic “mission/control room” language, and implementation metrics must not dominate the default experience.

## Local recent-task contract

- Store the recent-task index locally in the browser; do not imply account sync or cross-device history.
- Keep the internal thread and namespace values out of the default UI while preserving them for resume requests.
- Each entry records only the data needed to resume and identify the task: thread ID, local namespace, display title or excerpt, updated time, and status.
- De-duplicate by thread ID, order by most recently updated, cap retention to a documented finite count, and tolerate missing, malformed, or unavailable browser storage.
- New task creates a fresh thread and focuses the composer. Selecting a recent task restores its known result or paused decision state. Users can remove one entry or clear the local list with confirmation.
- Local history is convenience state, not authentication or an authorization boundary.

## Artifact browsing contract

- Artifact browsing is read-only: list, inspect metadata, preview supported text, and optionally download. It cannot edit, rename, delete, execute, or upload files.
- Resolve only relative paths within the configured Atlas workspace. Reject absolute paths, parent traversal, symlink escapes, invalid encodings, and paths outside that root.
- Apply bounded directory counts, preview sizes, and output lengths. Render untrusted content as text; never execute artifact HTML, JavaScript, Markdown HTML, or code.
- Show a friendly empty state, unsupported-type state, oversized-file state, and recoverable load error.
- The local namespace may scope the visible list for product consistency, but it is not security until authenticated server identity exists.

## Status and setup language

The default status vocabulary is: **Setup needed**, **Ready**, **Working**, **Waiting for your decision**, **Complete**, **Needs review**, and **Could not finish**. Graph nodes, review cycles, tool-call names, and provider responses may appear only in technical details.

When no model is configured, show a persistent Setup needed state with a clear local setup path. People may draft a task, but Run stays unavailable with an explanation; submission must not fail mysteriously. Never expose a secret value in the page, browser storage, URL, logs, or error copy.

## Progressive disclosure and themes

- Default content answers “What can I do?”, “What is happening?”, and “What did I get?”
- Put runtime health, model name, thread identifiers, raw approval data, memory internals, architecture flow, and API documentation under Settings, Technical details, or How it works.
- Support **System**, **Light**, and **Dark** themes. Use the system preference initially and persist an explicit local choice.
- Both themes must use shared semantic tokens and meet WCAG 2.2 AA text, non-text contrast, focus, disabled, error, success, warning, hover, active, and selected-state requirements. Forced-colors mode must remain usable.

## Accessibility acceptance criteria

- A skip link reaches the task workspace. Headings and landmarks describe the visual hierarchy, and sequential plans, sources, artifacts, memories, and recent tasks use semantic lists.
- Every action is operable by keyboard with a logical tab order and a visible `:focus-visible` indicator of at least 3:1 contrast.
- All actionable targets are at least 44 by 44 CSS pixels, including icon buttons, recent-task rows, example prompts, Copy, theme controls, and decision actions.
- Use exactly one concise, visually hidden status live region. Token streaming, results, timelines, and task lists are not live regions. Major state changes are announced once, with `aria-busy` reflecting active work.
- A decision request uses labelled dialog or alert-dialog semantics, moves focus to the safest action, contains keyboard focus while active, and restores focus when resolved. Reject remains at least as reachable and clear as Approve.
- Persistent errors use `role="alert"`, identify the failed action, and offer recovery. Timed toast messages are reserved for non-critical confirmations.
- Decorative glyphs are hidden from assistive technology. External links identify that they open a new tab. Reduced-motion preferences disable decorative animation and smooth scrolling.
- Functional text is normally at least 14px; subdued and placeholder text meets 4.5:1 contrast. Control boundaries and focus indicators meet 3:1.

## Responsive acceptance matrix

Validate the complete composer → activity → decision → result → artifact flow at these minimum viewports:

| Viewport | Required behavior |
| --- | --- |
| 320px wide | Composer and primary action are visible without horizontal scrolling; recent tasks remain reachable after the primary workspace in a compact horizontal list; no clipped labels or controls. |
| 375px wide | One-column task flow, 44px targets, readable result and artifact previews, and decision controls visible without overlap. |
| 768px wide | Navigation and workspace use available space without moving activity or decisions out of context. |
| 1440px wide | Recent tasks, primary work, and supporting activity form a balanced application shell; the composer remains the dominant entry action. |

The committed local matrix covers light and dark themes; setup, working, success, approval, and
recoverable-error states; no horizontal overflow; focus containment and return; enlarged text;
reduced motion; and forced colors. Before a formal accessibility conformance claim or public
hosting, add recorded manual proof for representative screen readers, full keyboard workflows,
400% zoom reflow, and supported browser/assistive-technology combinations.

## Validation and proof boundaries

Phase 2 local acceptance combines focused unit/API tests, static accessibility and injection-safety
contracts, a committed Playwright/axe CI matrix, and inspected desktop/mobile behavior. The
deterministic browser fixture exercises setup-needed, working, completion, approval and focus
return, a truncated-stream error, responsive themes, and evidence rendering without a provider
credential. Manual checks remain a separate proof layer, so this work does not claim formal WCAG
conformance.

This evidence proves the local application only. It does not prove hosted availability, production deployment, public authentication, multi-tenant isolation, cross-device history, live provider reliability, or multi-host persistence. Any fixture, mocked stream, deterministic vectorizer, or local-only provider path must be labelled as such; none may be summarized as production proof.
