# smartctx — Context-Scoping Launcher (Design Spec)

> **What is this file.** Implementation contract for `smartctx`, a launcher wrapper that
> composes a context-scoped Claude Code session by pruning off-topic MCP servers, plugins,
> and skills for the current session only. **Audience:** the implementing engineer.
> **Owner:** marcello.barile. **Companion files:** none yet; the implementation plan will
> live under `docs/plans/`. This spec is the contract; trade-offs and rejected alternatives
> are recorded inline in the "Alternatives considered" sections.

Date: 2026-08-18 · Status: **Design approved, pre-implementation** · Target harness (v1): Claude Code

---

## 1. Problem

A Claude Code session loads every installed MCP server, plugin, and skill regardless of the
task. Their tool schemas, skill descriptions, and agent lists inflate the context window even
when irrelevant to the session's goal. There is no runtime way to remove them: the system
prompt is assembled at session start, and hooks can only *inject* or *block*, never retract
already-loaded content.

**Goal:** decide the relevant subset *before* the session starts, and launch Claude Code with
only that subset — scoped to the current session, with zero mutation of global config.

## 2. Feasibility — verified CLI levers

Confirmed against `claude --help` and `~/.claude/settings.json` on 2026-08-18.

| Category | Lever | Granularity |
|---|---|---|
| MCP servers | `--strict-mcp-config --mcp-config <tmp.json>` — use ONLY the listed servers | fine |
| Plugins | `--settings <tmp.json>` overlay flipping `enabledPlugins.<id> = false` | fine |
| Plugin-provided skills / agents / MCP / hooks | transitive: removed when their plugin is disabled | fine |
| Standalone skills (`$CLAUDE_CONFIG_DIR/skills`, non-plugin) | `--disable-slash-commands` = all-off only | coarse (accepted) |

**Base strategy:** a *normal* session (keeps `CLAUDE.md`, hooks, memory) plus two ephemeral
overlays (curated MCP config + settings with pruned `enabledPlugins`). **No `--bare`** — that
flag is a nuclear clean-slate that also drops hooks, `CLAUDE.md` auto-discovery, and
auto-memory, which the user wants to keep.

Overlays are written to `$TMPDIR`, deleted on exit via a shell/`atexit` trap. No global config
is modified — the scoping is strictly session-local.

## 3. Profiles / aliases

Claude Code aliases select a config root via `CLAUDE_CONFIG_DIR`:

- `claude` → `CLAUDE_CONFIG_DIR` unset → defaults to `~/.claude`
- `claude-perso` → `alias claude-perso="CLAUDE_CONFIG_DIR=~/.claude-perso claude"`
- `claude-work` → not yet defined; same pattern expected

`smartctx` **resolves the config root from `$CLAUDE_CONFIG_DIR`**, falling back to Claude
Code's own default root when unset. **Nothing is hardcoded** — no machine-specific paths, no
baked-in alias names, no assumed plugin ids. All inventory reads (`settings.json`, `mcpServers`,
skills dir) come from the resolved root, and the composed `exec` preserves `CLAUDE_CONFIG_DIR`.
Pruning is therefore per-profile by construction, on any machine.

**Recommended integration** — these are *documentation examples*, not shipped logic; a user
picks whatever alias names they use:

```sh
alias claude-perso="CLAUDE_CONFIG_DIR=~/.claude-perso smartctx"
alias claude-work="CLAUDE_CONFIG_DIR=~/.claude-work smartctx"
# optionally: alias claude="smartctx"
```

**Alternatives considered:** argv[0] multi-call (symlink `smartctx-perso` → `smartctx`, map
name → dir) and an explicit `--profile` flag. Both require a name→dir map that must be updated
for each new alias; inheriting `CLAUDE_CONFIG_DIR` needs no map and covers future aliases
automatically. Rejected.

## 4. Components

1. **`smartctx` (entrypoint)** — `smartctx [claude args passthrough…]`. Orchestrates:
   inventory → goal → rank → compose → `exec claude …`. Unrecognized args pass through to
   `claude` untouched.
2. **Inventory adapter (Claude Code)** — reads the resolved config root:
   `enabledPlugins` + plugin manifests (name + description), `mcpServers`
   (`$CLAUDE_CONFIG_DIR/.claude.json` + project `.mcp.json`), standalone skill frontmatter.
   Emits items `{id, kind, name, description, footprint}` where `kind ∈ {mcp, plugin, skill}`.
3. **Goal detector** — signals: directory basename, marker files
   (`package.json` / `pyproject.toml` / `*.tsx` / `README` / `docs/`), git remote & branch.
   Produces a goal string + a confidence score. Below a threshold → ONE interactive prompt
   (with a suggested default). Result cached in `.smartctx/goal` per directory (add to
   `.gitignore`) so subsequent launches do not re-ask.
4. **Relevance ranker (local model)** — model2vec static embeddings (`potion-base-8M`, ~30MB,
   pure-numpy, no torch, CPU, offline, deterministic). Embeds the goal and each item
   description; ranks by cosine similarity; keeps items with score ≥ threshold, unioned with an
   **always-keep set**. Threshold is an **absolute cosine cutoff, default `0.35`** (not top-k —
   top-k is fragile as inventory size varies), overridable via the config chain (§6) and
   calibrated during build via `--explain`. Model cached under `~/.cache`.
5. **Launch composer** — writes a curated `mcp.json` (kept MCP servers) and an ephemeral
   `settings.json` (`enabledPlugins` with dropped plugins set to `false`), then
   `exec claude --strict-mcp-config --mcp-config <tmp> --settings <tmp> <passthrough>`,
   preserving `CLAUDE_CONFIG_DIR`.
6. **`Harness` protocol** — `inventory() -> list[Item]` and `compose(kept) -> (argv, env)`.
   Claude Code implementation ships in v1. The core (goal detection + ranking) is
   harness-independent — the agnosticism lives in the design, not yet in shipped adapters.

## 5. Data flow

```
inventory()  -> items
detect_goal()-> goal (+ confidence; prompt if low)
rank(goal, items) -> kept  (score >= threshold ∪ always-keep)
adapter.compose(kept) -> (argv, env)
exec(argv, env)   # replaces the smartctx process
```

## 6. Always-keep set (configurable, never shipped machine-specific)

A list of item ids/globs never pruned — the core workflow to keep regardless of goal. This is a
**first-class configurable concept**, resolved at runtime from a precedence chain; the
distributed package ships an **empty** default so nothing is bound to one user's setup.

Precedence (later overrides earlier), all optional:

1. built-in default — **empty** (rank everything; keep only what the model selects).
2. user/profile config — `$CLAUDE_CONFIG_DIR/smartctx/config.toml` → `always_keep = [...]`.
3. repo-local config — `.smartctx/config.toml` in the working dir.
4. env override — `SMARTCTX_ALWAYS_KEEP="id1,id2"`.

Entries are item ids or glob patterns (e.g. `superpowers`, `remember`, `caveman*`). A user's own
list (e.g. `superpowers`, `remember`, caveman hook stack) lives in *their* config file, which is
**not part of the distributed artifact** — it appears only as an example in the README.

Unknown ids in `always_keep` are ignored with a warning (a config may reference plugins absent
on this machine — expected for a distributed tool).

## 7. Error handling — fail-open always

The wrapper must **never** make `claude` unlaunchable. On any failure it degrades to launching
the full, unscoped session with a warning to stderr.

- Model missing → offer one-time download; if declined/offline, fall back to keyword-overlap
  heuristic (degraded ranking) + warn.
- Goal undetectable AND non-interactive (`-p`/`--print`, no TTY) → keep all + warn.
- Config unreadable / parse error → keep all + warn.

## 8. Testing

- **Unit** — ranker (fixed embedding fixtures → deterministic kept set), goal detector (fixture
  directories → expected goal + confidence), composer (golden `argv` + golden overlay JSON).
- **`--explain` (dry run)** — prints the detected goal, the kept/dropped items with scores, and
  the exact `argv` that *would* be exec'd, without launching. Primary manual-verification and
  debugging surface.

## 9. Distribution & packaging

The tool is built to be **distributed**, not tied to one machine.

- **Packaging** — a Python package with a single console entrypoint `smartctx`, installable via
  `pipx install smartctx` (isolated) or runnable as a `uv`/PEP 723 single-file script. No
  absolute user paths in code; all roots resolved at runtime (§3).
- **Zero-assumption inventory** — parse whatever exists under the resolved config root; missing
  files/keys are skipped, not errors. No assumption about which plugins/MCP/skills are present.
- **Config, not code, is user-specific** — the distributed artifact carries no user's plugin
  ids, paths, or alias names. Per-user/per-repo settings live in the config chain (§6):
  `SMARTCTX_ALWAYS_KEEP` env, `.smartctx/config.toml` (repo), `$CLAUDE_CONFIG_DIR/smartctx/config.toml`
  (user). Threshold and model source are overridable in the same chain.
- **Model provisioning** — the model2vec model is fetched on first run to the standard cache
  (`~/.cache`), with its source/name overridable via config; offline machines fall back to the
  keyword heuristic (§7). No model binary is vendored into the package unless a later decision
  requires fully offline installs.
- **Portability** — POSIX shells + Python ≥ 3.11 (stdlib `tomllib`); no dependency on the author's dotfiles.

## 10. Out of scope (YAGNI for v1)

- Harness adapters other than Claude Code (interface exists; no second implementation shipped).
- Remote / API-based relevance models.
- Per-tool pruning within a single MCP server (only whole-server granularity).
- Selective standalone-skill pruning (all-or-nothing via `--disable-slash-commands`).
- Any modification of global/persisted config.

## 11. Resolved defaults (confirmed 2026-08-18)

1. **Threshold** — absolute cosine cutoff, default `0.35`; calibrate via `--explain` (§4).
2. **Wrap scope** — v1 wraps profile aliases only; bare `claude` stays full unless the user
   opts in by aliasing it to `smartctx`.
3. **Config format** — TOML.
4. **Model** — fetch on first run to `~/.cache`; no vendored binary; offline → keyword fallback (§9).
