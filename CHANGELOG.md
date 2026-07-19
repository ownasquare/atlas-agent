# Changelog

Notable user-facing changes are recorded here. Atlas follows semantic versioning while its public
interfaces continue to mature.

## [Unreleased]

## [0.3.1] - 2026-07-18

### Added

- A committed Playwright/axe browser gate for setup, task, approval, error, keyboard, theme, and
  responsive workspace behavior.
- Provider-aware setup preflight responses for the CLI and task API.

### Changed

- Keyless task starts now stop with concise setup guidance before model runtime construction.
- The workspace preserves keyboard focus through working, success, error, and approval states and
  improves control readability, contrast, narrow-screen reflow, and result heading structure.
- Installation guidance now warns that the similarly named PyPI distribution is unrelated and
  directs users to the supported tagged source checkout.
- Runtime installation no longer includes the contributor toolchain by default; contributor setup
  remains an explicit locked install.

## [0.3.0] - 2026-07-18

### Added

- Secret-safe `atlas doctor` readiness checks.
- Public extension seams and a runnable custom-tool example.
- Getting-started, troubleshooting, contribution, security, and community guidance.
- Accessible compact information controls and safer structured result rendering.

### Changed

- Code execution now defaults to disabled and requires explicit Docker opt-in.
- Saved context now uses Atlas's network-free SQLite vector index instead of Chroma, removing the
  first-use embedding download and heavyweight cache volume. Pre-release Chroma records are left
  untouched but are not imported automatically; re-save any context Atlas should continue to recall.
- The workspace centers the task, progress, and reviewed result while disclosing secondary details
  only when useful.
- Continuous integration uses locked dependency installation and current supported Python versions.

### Security

- Removed ChromaDB 1.5.9 and its unresolved critical `GHSA-f4j7-r4q5-qw2c` advisory from the
  runtime dependency graph.
- Raised the development graph's cryptography floor to 48.0.1, which contains the fix for
  `GHSA-537c-gmf6-5ccf`.

## [0.2.0] - 2026-07-17

- Reworked the browser into a responsive local task workspace.
- Added recent tasks, plain-language approvals, saved context, and confined file preview/download.
- Added focused workspace API and browser contracts.

## [0.1.0] - 2026-07-17

- Added the initial LangGraph planner, tool loop, reviewer, and memory workflow.
- Added guarded search, calculator, file, and Docker Python tools.
- Added CLI, FastAPI/SSE, deterministic evaluations, packaging, and local delivery configuration.
