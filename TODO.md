# TODOs

All four items below are implemented/verified — see
[docs/handoff/2026-08-19-todo-implementation.md](docs/handoff/2026-08-19-todo-implementation.md)
for the commit list and verification evidence.

- [x] Uninstall script (`scripts/uninstall.sh`)
- [x] Better informative guidance once setup is done (how to init) — `smartctx doctor`
- [x] Review readme after having shipped the model inside the repo — verified accurate
- [x] Review the requisites of the readme — verified, one clarification added
- [ ] $ smartctx --help prints out: smartctx: CLAUDE_CONFIG_DIR not set — pick a Claude profile
- [ ] "smartctx: rule for 'session-report@claude-plugins-official' (plugin)? [enter=skip] keep it" on a new line: "couldn't compile; [k]eep always / [d]rop always / [s]kip?" ... this interaction was not clear, it would be better to have a guidance beforehand of the possible rules. The criteria was that the user could use natural language for rules, but apparently that's not the case
