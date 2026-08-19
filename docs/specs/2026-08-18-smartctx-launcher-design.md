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
   inventory → goal → **apply exclusion rules** → rank the undecided → compose →
   `exec claude …`. Also exposes `smartctx rules` for bulk rule authoring. Unrecognized args
   pass through to `claude` untouched.
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
   **always-keep set**. Threshold is an **absolute cosine cutoff, default `0.20`** (not top-k —
   top-k is fragile as inventory size varies), overridable via the config chain (§6) and
   calibrated during build via `--explain`. Model cached under `~/.cache`.
5. **Launch composer** — writes a curated `mcp.json` (kept MCP servers) and an ephemeral
   `settings.json` (`enabledPlugins` with dropped plugins set to `false`), then
   `exec claude --strict-mcp-config --mcp-config <tmp> --settings <tmp> <passthrough>`,
   preserving `CLAUDE_CONFIG_DIR`.
6. **`Harness` protocol** — `inventory() -> list[Item]` and `compose(kept) -> (argv, env)`.
   Claude Code implementation ships in v1. The core (goal detection + ranking) is
   harness-independent — the agnosticism lives in the design, not yet in shipped adapters.
7. **Rules store + evaluator** — persists per-item exclusion rules (id/glob → NL text +
   compiled predicate) in a config-chain TOML, and deterministically evaluates each predicate
   against the session context to force-keep or force-drop a candidate, ahead of similarity
   ranking. Offline, no model. See §12.
8. **Rule compiler (local instruct model)** — translates an NL rule into a structured predicate
   at authoring time only (setup command or launch-time elicitation), via a small local instruct
   LLM (llama.cpp + small GGUF). Never runs at plain launch. See §12.

## 5. Data flow

```
inventory()        -> items
detect_goal()      -> context (goal string + confidence; prompt if low)
apply_rules(items, rules, context) -> (forced_keep, forced_drop, undecided)
    # rule-less drop candidates may trigger elicitation -> compile -> persist
rank(context, undecided) -> ranked_kept  (score >= threshold ∪ always-keep)
kept = forced_keep ∪ ranked_kept  (minus forced_drop)
adapter.compose(kept) -> (argv, env)
exec(argv, env)   # runs claude as a child; overlays cleaned up after exit
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
- Instruct model missing at authoring time → skip NL compilation; prompt the user for a simple
  `always keep` / `always drop` / `skip` choice for that item instead + warn. Never blocks launch.
- Non-interactive launch → no elicitation; only already-compiled rules apply.

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
- **Model provisioning** — the model2vec embedding model is fetched on first run to the standard
  cache (`~/.cache`), source/name overridable via config; offline machines fall back to the
  keyword heuristic (§7). The instruct model for rule compilation (§12) is a **separate, optional**
  GGUF fetched (or pointed at via `rule_model_path`) only when rule authoring is used; absent →
  the degrade path in §7 applies. Neither model is vendored into the package.
- **Optional dependency** — `llama-cpp-python` is an **extra** (`smartctx[rules]`), not a base
  dependency; core scoping installs and runs without it.
- **Portability** — POSIX shells + Python ≥ 3.11 (stdlib `tomllib`); no dependency on the author's dotfiles.

## 10. Out of scope (YAGNI for v1)

- Harness adapters other than Claude Code (interface exists; no second implementation shipped).
- Remote / API-based relevance models.
- Per-tool pruning within a single MCP server (only whole-server granularity).
- Selective standalone-skill pruning (all-or-nothing via `--disable-slash-commands`).
- Any modification of global/persisted config.

## 11. Resolved defaults (confirmed 2026-08-18)

1. **Threshold** — absolute cosine cutoff, default `0.20` (calibrated against the
   `potion-base-8M` score distribution on real inventories); calibrate further via `--explain` (§4).
2. **Wrap scope** — v1 wraps profile aliases only; bare `claude` stays full unless the user
   opts in by aliasing it to `smartctx`.
3. **Config format** — TOML.
4. **Model** — fetch on first run to `~/.cache`; no vendored binary; offline → keyword fallback (§9).
5. **Exclusion rules** (added 2026-08-18) — elicited both via a `smartctx rules` setup command
   and as a launch-time fallback for rule-less drop candidates; NL compiled to a deterministic
   predicate by a small **local instruct** model at authoring time; launch-time evaluation is
   deterministic and offline. See §12.

## 12. Exclusion rules layer

Beyond similarity, the user attaches **exclusion rules** to items so context-sensitive items
(e.g. a corporate plugin/skill that must appear only in work-related sessions) are kept or
dropped by an explicit, deterministic rule rather than by cosine score alone.

### Rule and predicate

A rule binds an item id/glob to NL text and a compiled predicate:

```toml
# $CLAUDE_CONFIG_DIR/smartctx/rules.toml  (user)  and/or  ./.smartctx/rules.toml (repo)
[[rule]]
target = "camunda-*"
nl = "corporate design-system plugin — only for camunda / bpmn / work sessions"
[rule.predicate]
action = "keep_if"          # keep_if | drop_if | always_keep | always_drop
match = ["camunda", "bpmn", "work", "orchestration"]
match_mode = "any"          # any | all
```

**Predicate semantics** (evaluated against `context` = the goal string, case-insensitive
substring match of each `match` term; `match_mode` combines them):

- `always_keep` → force keep.
- `always_drop` → force drop.
- `keep_if` → context matches ⇒ force keep; no match ⇒ **force drop** (item is scoped *only* to
  those contexts).
- `drop_if` → context matches ⇒ force drop; no match ⇒ **undecided** (falls through to ranking).

**Precedence:** `always_keep` config set (§6) > rules > similarity threshold (§4). Within rules,
an exact-id rule wins over a glob rule; if still tied, `always_*` beats conditional.

### Store & precedence

Rules load from the config chain (repo `./.smartctx/rules.toml` overrides
`$CLAUDE_CONFIG_DIR/smartctx/rules.toml` per `target`). Repo rules are git-ignorable or shareable
at the user's choice. Unknown targets are ignored silently (distributed-tool reality).

### Elicitation (both modes)

- **Setup — `smartctx rules`**: walks the inventory; for each item without a rule, shows its
  id/kind/description and prompts for an NL rule (empty = skip, leaves it to pure ranking).
  Compiles and saves each.
- **Launch fallback**: when scoping would drop one or more items that have **no** rule, and the
  session is interactive (TTY, not `-p/--print`), smartctx prompts **once** for all such
  candidates as a batch (each skippable), compiles, saves, then applies. Non-interactive → no
  prompt; only pre-compiled rules apply.

### Compiler (local instruct model, authoring-time only)

`compile_rule(nl, item) -> Predicate` calls a small local instruct LLM (llama.cpp + small GGUF,
e.g. `Qwen2.5-0.5B-Instruct`) with a constrained prompt that must return the predicate JSON above;
the result is schema-validated (unknown `action` / non-list `match` → rejected, re-prompt once,
then fall back to the §7 simple-choice degrade). The model is invoked **only** during
elicitation, never at plain launch, so launches stay deterministic and offline. `rule_model_path`
in config points at the GGUF; absent → §7 degrade. The compiler takes the model as an injected
callable so tests never load a real model.
