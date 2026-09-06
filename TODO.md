# TODOs

All items below are implemented/verified. The first six are covered by
[docs/handoff/2026-08-19-todo-implementation.md](docs/handoff/2026-08-19-todo-implementation.md);
the memory item has its own spec and pull request, linked inline.

- [x] Uninstall script (`scripts/uninstall.sh`)
- [x] Better informative guidance once setup is done (how to init) — `loadout doctor`
- [x] Review readme after having shipped the model inside the repo — verified accurate
- [x] Review the requisites of the readme — verified, one clarification added
- [x] $ loadout --help prints out: loadout: CLAUDE_CONFIG_DIR not set — pick a Claude profile — `--help`/`-h`/`--version`/`-V` now short-circuit before profile resolution and scoping
- [x] "loadout: rule for '…' (plugin)? [enter=skip] keep it" / "couldn't compile; [k]eep always / [d]rop always / [s]kip?" interaction unclear — added upfront guidance (`_rules_intro`) with examples; when no rule model is configured the NL prompt is skipped entirely and only keep/drop/skip is offered
- [x] Analyse a way to give the agent a persistent "memory state" that reshapes itself based on its past experience (things that the model overcame, errors encountered and fixed, discussions with the user, important decisions etc...) - this knowledge should be somehow accessible while working nomatter what the session is about - it should not pollute the context while being useful when needed. It should mimic human brain functions around memory, and knowledge handling. Goals: avoid context drift, error propagation, no debt-awareness. — designed in [docs/specs/2026-09-04-session-memory-recall-design.md](docs/specs/2026-09-04-session-memory-recall-design.md) and shipped in https://github.com/sondalab-ai/claude-loadout/pull/5: ranked recall injects only the notes relevant to a session inside a token budget (`[memory]`, off by default), with `cld recall` for the rest, `cld memory audit` to curate, a debt ledger for the shims a session leaves behind, and per-prompt recall behind `prompt_recall`. Entries Claude Code already loads through its own `MEMORY.md` are not repeated.
