# Hand-off — TODO.md implementation (2026-08-19)

> **Purpose:** status + review entry points for the four TODOs in `TODO.md`, aimed at a fresh
> model (or human) reviewing or continuing this work. **Audience:** future agents, reviewers.
> **Owner:** repo maintainer. **Relationship to companion files:** this doc is a pointer layer —
> the actual diffs live in the three commits listed below; `docs/specs/2026-08-18-smartctx-launcher-design.md`
> and `docs/plans/2026-08-18-smartctx-launcher.md` define the product and are unchanged by this work.

## Status summary

| TODO | Status | Commit |
|---|---|---|
| Uninstall script | **Implemented (delivered)** | `2e775dd` |
| Better guidance once setup is done (how to init) | **Implemented (delivered)** | `6595415` |
| Review README after shipping the model in-repo | **Verified — README already accurate; two lines added as review outcome** | `a92cf1b` |
| Review README requirements | **Verified — one clarification line added** | `a92cf1b` |

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

## Follow-ups (explicitly NOT in scope)

- The empty-inventory fail-open path launches `claude` even for `--explain` (exit 1 with no TTY);
  a dedicated "nothing to prune" message would be nicer UX but changes launcher semantics.
- `doctor` probes the embedding model (loads ~30 MB); fine for a diagnostic, don't call it per launch.

## Suggested skills for the next session

- `superpowers:verification-before-completion` before claiming anything green (evidence above
  shows the pattern used here).
- `superpowers:requesting-code-review` / `caveman-review` for a review pass over the three commits.
- `superpowers:systematic-debugging` if the bash 3.2 or PEP 668 edge cases resurface.