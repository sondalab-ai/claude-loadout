# Agent instructions — claude-loadout (`ccloadout`)

Guidance for AI coding agents working in this repo. Human contributors: the release rule applies to you too.

## Release / version bump (MANDATORY)

Publishing to PyPI is automated: `.github/workflows/publish.yml` runs on every push to `master` and publishes **only when the `version` in `pyproject.toml` is not already on PyPI**. A merge that does not change the version is a silent no-op — nothing gets published.

**Therefore: any PR that changes shipped code MUST bump `version` in `pyproject.toml`, in the same PR.** Never split the code change and its version bump across separate PRs — if the code merges first without the bump, the release is skipped and users cannot install the change.

Pick the bump with semver:

- **patch** (`0.2.0` → `0.2.1`) — bug fix, internal refactor, or a change with no public API/CLI difference.
- **minor** (`0.2.0` → `0.3.0`) — new backward-compatible feature, flag, or command.
- **major** (`0.2.0` → `1.0.0`) — breaking change to the CLI, config, or public behavior.

"Shipped code" = anything inside `src/ccloadout/**` or packaging (`pyproject.toml` deps). Docs-only, CI-only, or test-only changes do **not** require a bump.

### Checklist before opening a PR with code changes

1. Bump `version` in `pyproject.toml` (semver as above).
2. Include the bump in the same commit range as the code change.
3. Confirm the new version is not already on PyPI: `curl -fsS https://pypi.org/pypi/ccloadout/<version>/json` should 404.
4. On merge to `master`, watch the **Publish to PyPI** workflow go green, then verify: `pipx install ccloadout==<version>` (or `pipx upgrade ccloadout`).

## Tests

Run before every PR:

```sh
python -m pytest -q
```

All tests must pass. Bug fixes get a regression test. New behavior gets a test.

## Publishing mechanics (reference)

- PyPI publish uses **Trusted Publishing (OIDC)** — no API token is stored in the repo.
- The default branch is `master` (not `main`).
- The terminal color palette comes from the shared `sondalab-palette` package (repo `sondalab-ai/sondalab-ui`); the named-ANSI floor must stay byte-identical to legacy codes so non-truecolor terminals see no change. Do not hand-edit color roles here — change them upstream in `sondalab-ui`.
