# Publishing Atlas

This is a maintainer checklist, not an extra installation guide. The canonical repository is
[ownasquare/atlas-agent](https://github.com/ownasquare/atlas-agent). Do not publish placeholder
links or claim services that do not exist.

## Repository setup

- Confirm the `Homepage`, `Repository`, `Issues`, `Documentation`, and `Changelog` entries
  under `[project.urls]` still resolve.
- Keep the repository description, topics, license detection, and social preview current. Confirm
  every README badge or link resolves to this repository.
- Keep GitHub private vulnerability reporting enabled and verify the links in `SECURITY.md` and
  `CODE_OF_CONDUCT.md` open the private form.

## Clean-clone proof

Test the instructions from a fresh clone outside the working repository:

```bash
git clone <repository-url> atlas-agent-clean
cd atlas-agent-clean
cp .env.example .env
uv sync --locked --all-extras --group dev
uv run atlas doctor
make check
make eval
uv build
```

The key-free checks must pass without using a maintainer's existing virtual environment or local
Atlas data. A doctor report without a provider key may correctly request configuration; it must not
print credentials. Also repeat the four-step [getting-started flow](getting-started.md) with a test
provider account before announcing the release.

## Remote proof

- Push a release candidate and require the repository's CI workflow on the default branch and pull
  requests. Confirm lint, types, tests, evaluation, package build, and wheel smoke checks are green
  in the hosted runner.
- Open one sample bug and pull request from the supplied templates, then confirm their links and
  instructions work for a contributor without maintainer access.
- Tag a release only after `CHANGELOG.md` matches the shipped version and the built artifacts come
  from the validated commit.

## Optional follow-ups

Treat these as separate releases, not current Atlas capabilities:

- Publish to PyPI only after package-name ownership, trusted publishing, artifact inspection, and a
  clean-environment install are proven.
- Record a live-provider smoke test only when a maintainer intentionally supplies a test credential;
  local doctor and CI checks do not contact or validate a model provider.
- Add hosting, authentication, multi-user isolation, or managed persistence only after those
  features are implemented, threat-modeled, documented, and tested. The current project is a local
  application.
