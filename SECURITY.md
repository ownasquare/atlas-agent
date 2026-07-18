# Security policy

Atlas handles untrusted prompts, web content, files, model output, and optional generated Python.
Please report security problems privately so maintainers have time to investigate before details are
public.

## Supported code

Security fixes target the latest release and the current default branch. Older versions may receive
a fix when the change can be applied safely, but support is not guaranteed.

## Report a vulnerability

Use [GitHub's private vulnerability report](https://github.com/ownasquare/atlas-agent/security/advisories/new).
Do not open a public issue containing an exploit, secret, private file, or identifying user data.

Include only what is needed to reproduce the problem:

- affected version or commit;
- affected interface and configuration;
- minimal reproduction steps;
- impact and expected boundary;
- sanitized logs or proof;
- any proposed mitigation.

Never send working credentials. Replace secrets, tokens, absolute personal paths, and private data
with clearly marked placeholders.

## In scope

- workspace traversal, symlink, hidden-file, or download escapes;
- approval bypasses or stale-decision replay;
- unintended host or network access from Python execution;
- cross-user checkpoint or memory access;
- credential exposure or unsafe persistence;
- prompt or tool output becoming trusted evidence without confirmation;
- denial-of-service paths that bypass configured limits.

General feature requests, setup questions, and model-quality disagreements are not vulnerabilities.
Use the normal issue templates for those reports.

## Disclosure

Maintainers will acknowledge a usable private report when practical, investigate it, and coordinate
disclosure after a fix or mitigation exists. Timing depends on severity, reproducibility, and release
scope. Please do not publish details before coordination is complete.

Atlas is local-first and unauthenticated. Binding it to an untrusted network is outside the current
security boundary; see [the safety model](docs/safety/safety.md).
