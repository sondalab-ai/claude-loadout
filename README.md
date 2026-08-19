# smartctx

`smartctx` is a pre-launch wrapper for Claude Code that scopes a single session to the
goal-relevant subset of your installed MCP servers and plugins. It infers the session goal from
the working directory, ranks each installed item against that goal, applies your exclusion rules,
then launches a **normal** Claude Code session (never `--bare`) with two ephemeral overlays that
prune the off-topic items — so their tool schemas and descriptions no longer inflate the context
window. The scoping is strictly session-local: it writes no Claude config, never mutates
`settings.json` or `.claude.json`, and it is **fail-open** — any error at all degrades to
launching the full, unscoped `claude`.

## What it prunes (and what it does not)

Pruning is done for the current session only, via CLI overlays:

- **MCP servers** — only the curated, kept set is loaded (`--strict-mcp-config --mcp-config`).
  Because of `--strict-mcp-config`, only the servers in the generated file load, regardless of
  what is in `.claude.json`.
- **Plugins** — dropped plugins are disabled through a `--settings` overlay
  (`enabledPlugins.<id> = false`). Skills, agents, MCP servers, and hooks provided *by* a plugin
  go away transitively when their plugin is disabled.

Not pruned in v1:

- **Standalone skills** (under `$CLAUDE_CONFIG_DIR/skills`) are inventoried, scored, and shown in
  the `--explain` dropped list, but they are **not removed** — Claude Code only exposes an
  all-or-nothing switch for them, which v1 does not use.
- `CLAUDE.md`, hooks, and memory are preserved (this is a normal session, not `--bare`).

## Install

```sh
pipx install smartctx
```

For the optional natural-language rule compiler (adds `llama-cpp-python`):

```sh
pipx install "smartctx[rules]"
```

The core scoping installs and runs without the extra. On first run the embedding model
(`minishlab/potion-base-8M` by default) is fetched to the standard cache; offline machines fall
back to a keyword-overlap heuristic with a warning.

## Alias integration

`smartctx` resolves its config root from `$CLAUDE_CONFIG_DIR` (default `~/.claude`) and preserves
that variable when it launches `claude`. Nothing about a profile is hardcoded, so a single binary
wraps any alias. These are **documentation examples**, not shipped configuration — pick whatever
alias names you use:

```sh
alias claude-perso="CLAUDE_CONFIG_DIR=~/.claude-perso smartctx"
alias claude-work="CLAUDE_CONFIG_DIR=~/.claude-work smartctx"
# opt-in: wrap bare `claude` too
# alias claude="smartctx"
```

v1 wraps profile aliases only; bare `claude` stays full unless you opt in with the last line.
Any Claude Code arguments you pass are forwarded untouched, e.g. `claude-perso -p "..."`.

## Configuration

Config is TOML, resolved through a chain where **a later layer replaces an earlier one** for each
key (layers do not merge, and a list-valued key is overwritten wholesale, not extended):

1. built-in defaults — `always_keep` is **empty**, `threshold = 0.35`,
   `model_name = "minishlab/potion-base-8M"`, `rule_model_path` unset.
2. user/profile — `$CLAUDE_CONFIG_DIR/smartctx/config.toml`.
3. repo-local — `./.smartctx/config.toml` in the working directory.
4. environment — `SMARTCTX_ALWAYS_KEEP="id1,id2"`, `SMARTCTX_THRESHOLD`, `SMARTCTX_RULE_MODEL`.

Keys:

| Key | Meaning | Default |
|---|---|---|
| `always_keep` | item ids or globs never pruned (see precedence below) | *(empty)* |
| `threshold` | cosine cutoff; an item is kept when its score is `>= threshold`. Raise it to prune more aggressively, lower it to keep more. | `0.35` |
| `model_name` | model2vec embedding model fetched for ranking | `minishlab/potion-base-8M` |
| `rule_model_path` | absolute path to the local GGUF instruct model for rule compilation | *(unset)* |

Sample `.smartctx/config.toml`:

```toml
# The always_keep list below is an EXAMPLE — the shipped default is empty.
# Entries are item ids or glob patterns; unknown ids are simply ignored.
always_keep = ["superpowers", "remember", "caveman*"]

threshold = 0.35

# rule_model_path is NOT tilde-expanded — use an absolute path, not "~/...".
rule_model_path = "/Users/you/models/Qwen2.5-0.5B-Instruct.gguf"
```

Note: `rule_model_path` (and `SMARTCTX_RULE_MODEL`) must be an absolute path — `~` is not
expanded, and a tilde path silently degrades to "rule authoring disabled" at runtime.

## Dry run: `--explain`

`smartctx --explain [claude args...]` prints the detected goal, the kept item ids, the dropped
items with their scores, and the exact `argv` that *would* be launched — then exits **without**
launching. The `--explain` flag is consumed by smartctx and stripped from the passthrough. This is
the primary surface for calibrating `threshold` and debugging what gets scoped out.

## Exclusion rules

Similarity alone cannot express "this corporate plugin belongs only in work sessions." Exclusion
rules bind an item id/glob to a deterministic predicate that is evaluated offline at launch,
against the goal string (case-insensitive substring match).

**Precedence:** `always_keep` config (kept regardless) > rules > similarity threshold. Within
rules, an exact-id rule wins over a glob rule; if still tied, `always_*` beats a conditional.

Rules live in the config chain: `$CLAUDE_CONFIG_DIR/smartctx/rules.toml` (user) and/or
`./.smartctx/rules.toml` (repo); the repo file overrides the user file per `target`. Sample
`rules.toml`:

```toml
[[rule]]
target = "camunda-*"
nl = "corporate design-system plugin — only for camunda / bpmn / work sessions"
[rule.predicate]
action = "keep_if"
match = ["camunda", "bpmn", "work", "orchestration"]
match_mode = "any"
```

Predicate `action` is one of `keep_if`, `drop_if`, `always_keep`, `always_drop`; `match` is a list
of terms combined by `match_mode` (`any` or `all`). `keep_if` forces keep on a match and force
drop otherwise (item scoped *only* to those goals); `drop_if` forces drop on a match and otherwise
falls through to ranking.

### Authoring rules

- **Bulk — `smartctx rules`**: walks the inventory and, for each item without a rule, prompts for a
  natural-language rule (empty input skips, leaving it to pure ranking). It resolves the session
  goal first, so it may ask you to confirm the goal before it starts walking items.
- **Launch-time elicitation**: when scoping would drop a rule-less item and the session is
  interactive (a TTY, no `-p`/`--print`), smartctx warns how many such items there are, then asks
  per item for a rule (press enter to skip). Non-interactive launches skip this entirely; only
  already-compiled rules apply.

Natural-language rules are compiled to a predicate by a small **local instruct model**, configured
via `rule_model_path` / `SMARTCTX_RULE_MODEL` and installed through the `smartctx[rules]` extra.
Without that model, authoring degrades to a simple prompt — keep always / drop always / skip — for
each item; launches themselves stay deterministic and offline regardless.

## Fail-open guarantee

`smartctx` must never make `claude` unlaunchable. Any exception while building the scoped plan, an
empty inventory, or an unreadable config all degrade to launching the full unscoped `claude` (with
a stderr warning where relevant); the child's exit code is propagated. Related degradations: a
missing embedding model falls back to the keyword heuristic, and a missing rule model disables NL
compilation — neither blocks a launch.

The "no config mutation" guarantee is about Claude Code's own configuration. smartctx itself does
write two files under your control: it appends authored rules to
`$CLAUDE_CONFIG_DIR/smartctx/rules.toml`, and caches a confirmed session goal at `./.smartctx/goal`
in the working directory.
