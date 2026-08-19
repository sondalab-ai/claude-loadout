# Hand-off — TODO.md implementation (2026-08-19)

> **Purpose:** status + review entry points for the six TODOs in `TODO.md`, aimed at a fresh
> model (or human) reviewing or continuing this work. **Audience:** future agents, reviewers.
> **Owner:** repo maintainer. **Relationship to companion files:** this doc is a pointer layer —
> the actual diffs live in the commits listed below; `docs/specs/2026-08-18-smartctx-launcher-design.md`
> and `docs/plans/2026-08-18-smartctx-launcher.md` define the product and are unchanged by this work.

## Status summary

| TODO | Status | Commit |
|---|---|---|
| Uninstall script | **Implemented (delivered)** | `2e775dd` |
| Better guidance once setup is done (how to init) | **Implemented (delivered)** | `6595415` |
| Review README after shipping the model in-repo | **Verified — README already accurate; two lines added as review outcome** | `a92cf1b` |
| Review README requirements | **Verified — one clarification line added** | `a92cf1b` |
| `smartctx --help` triggered the profile picker instead of showing help | **Implemented (delivered)** | `37516aa` |
| Rule elicitation UX unclear (NL rules "didn't work") | **Implemented (delivered)** | `37516aa` |

Legend: **Implemented (delivered)** = merged to the production codebase; **Verified** = checked
against the build/code, no change needed beyond what the commit shows.

## Verification evidence (all run on 2026-08-19)

- `pytest tests/ -q` → **36 passed** (34 pre-existing + 2 new for `doctor`).
- `scripts/uninstall.sh` exercised in a sandbox with a fake `pipx` (pipx branch), with a fake
  HOME + no install (skip branch + config cleanup `y/y`), and under `/bin/bash` 3.2.57
  (macOS system bash — `bash -n` clean). Fix applied along the way: `${answer,,}` is bash≥4 only,
  replaced with a `=~ ^[yY]([eE][sS])?$` regex. The pip fallback is non-fatal (PEP 668
  externally-managed environments would otherwise abort the whole script).
- Wheel build (`python -m build --wheel`) contains `smartctx/models/potion-base-8M/*` incl.
  `model.safetensors` (30 MB) → README "ships inside the package" claim verified.
- Fresh venv install of the built wheel + `smartctx --explain` in an empty dir **without
  network** → real model scores printed, no fallback warning → "ranks offline out of the box"
  verified. (Note: with an empty inventory the tool fail-opens into launching `claude`, by design.)

## Design decisions (review these first if you disagree)

1. **`scripts/uninstall.sh`** — bash, prompts for every destructive step, never touches shell rc
   (prints what to remove by hand). Detection order: pipx → pip (non-fatal) → skip. `CLAUDE_CONFIG_DIR`
   honored, `~` expanded manually (`${VAR/#\~/$HOME}` — tilde is not expanded inside variables).
2. **`smartctx doctor`** — new subcommand (sibling of `rules`), prints config dir, config-file
   presence, inventory counts, embedding-model state (bundled/external/keyword fallback — identity
   check against the `keyword_embed` import), rule-model config, then a "Next steps" cheat sheet
   (alias examples, `--explain`, `rules`). Never launches Claude Code, never writes files, always
   exits 0 (fail-open, same spirit as the launcher).
3. **README review TODOs** — merged into one commit: the model-vendoring review found the README
   already correct (it was rewritten in `dbe98f6` after the vendoring commit `c0b2ee4`); the
   requirements review added "(runtime deps `model2vec` and `numpy` install automatically)" and
   "(the uninstall script is bash; the tool itself is platform-independent)".

4. **`--help`/`--version` short-circuit** — `_run` now handles `--help`/`-h` (prints smartctx's own
   usage) and `--version`/`-V` (prints the package version) *before* profile resolution and scoping.
   Root cause: the profile picker (`_resolve_config_root`) and `_scoped_plan` ran for every invocation,
   so `smartctx --help` hit "CLAUDE_CONFIG_DIR not set — pick a Claude profile" instead of showing help.
5. **Rule elicitation guidance** — added `_rules_intro(compile_fn)`, printed once before any elicitation
   run (launch-time drops and the `rules` subcommand). Root cause of the confusion: `_elicit` always
   asked for a natural-language rule first, but when no rule model is configured (the common case)
   the NL text can never compile, so every entry silently fell through to keep/drop/skip. Now: with a
   rule model, the intro shows NL examples and the compile-failure path says "couldn't translate …,
   choose manually"; with no rule model, the NL prompt is skipped entirely and only keep/drop/skip is
   offered, prefaced by an explicit "no rule model configured" line.

## Verification evidence (2026-08-19, TODOs 5–6)

- `pytest tests/ -q` → **58 passed** (55 pre-existing + 3 new: help bypasses prompt/launch,
  `--version` bypasses prompt, elicitation with no rule model skips the NL prompt and re-keeps on `k`).
- `python -m smartctx --help` and `--version` run with two profiles present and `CLAUDE_CONFIG_DIR`
  unset → both exit 0 and print without any profile prompt.

## Follow-ups (explicitly NOT in scope)

- The empty-inventory fail-open path launches `claude` even for `--explain` (exit 1 with no TTY);
  a dedicated "nothing to prune" message would be nicer UX but changes launcher semantics.
- `doctor` probes the embedding model (loads ~30 MB); fine for a diagnostic, don't call it per launch.

## Suggested skills for the next session

- `superpowers:verification-before-completion` before claiming anything green (evidence above
  shows the pattern used here).
- `superpowers:requesting-code-review` / `caveman-review` for a review pass over the commits.
- `superpowers:systematic-debugging` if the bash 3.2 or PEP 668 edge cases resurface.