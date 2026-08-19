# smartctx

**Start each Claude Code session with only the tools it needs.**

`smartctx` is a small launcher that sits in front of Claude Code. It looks at what you're
working on, figures out which of your installed MCP servers and plugins are actually relevant,
and starts the session with just those — leaving everything else out of the way. When you're
done, nothing about your setup has changed.

---

## Why

Every Claude Code session loads *all* of your installed MCP servers, plugins, and skills — a
calendar integration, a browser driver, three documentation servers, a design system — whether
or not today's task has anything to do with them. Each one spends part of the model's context
window describing itself before you've typed a word. The more you install, the more crowded
every session starts.

`smartctx` fixes that per session, without you having to toggle anything by hand. You keep
everything installed; smartctx just decides, each time you launch, what's worth bringing in.

## What it does

1. **Figures out the goal.** It reads signals from your working directory — the folder name,
   marker files like `package.json` or `pyproject.toml`, the git branch. If it can't tell, it
   asks once (and remembers your answer).
2. **Ranks your tools against that goal** using a small, fast, local model — no network call, no
   data leaving your machine.
3. **Applies your rules.** You can pin tools to always keep, and write plain-language rules like
   *"this corporate plugin only in work sessions."*
4. **Launches Claude Code with the relevant subset.** The off-topic servers and plugins simply
   aren't loaded for that session.

If anything goes wrong at any step, smartctx quietly launches the full, normal session instead —
**it can never leave you unable to start Claude.**

## Quick start

```sh
pipx install smartctx
```

Point your usual launch command at it. `smartctx` figures out which Claude profile you're using
from the `CLAUDE_CONFIG_DIR` environment variable (default `~/.claude`) and passes it straight
through, so one install wraps any alias — use whatever names you already have:

```sh
alias claude-work="CLAUDE_CONFIG_DIR=~/.claude-work smartctx"
alias claude-perso="CLAUDE_CONFIG_DIR=~/.claude-perso smartctx"

# Optional — wrap plain `claude` too:
# alias claude="smartctx"
```

That's it. Run `claude-work` (or whatever you aliased) as you always have — every Claude Code
argument you pass is forwarded untouched, e.g. `claude-work -p "summarize this repo"`.

## See what it would do — before it does it

Curious, or tuning things? Add `--explain` and smartctx prints its plan and exits **without
launching anything**:

```sh
smartctx --explain
```

You'll see the goal it detected, which items it would keep, which it would drop (and why), and
the exact command it would run. It's the best way to get a feel for the tool and to calibrate how
aggressively it prunes.

---

## How it works

Under the hood, a scoped launch is a normal Claude Code session plus two small, temporary overlay
files:

- **MCP servers** — smartctx writes a curated MCP config listing only the kept servers and starts
  Claude with `--strict-mcp-config`, so only those load.
- **Plugins** — dropped plugins are switched off via a `--settings` overlay. Anything a plugin
  provides (its skills, agents, MCP servers, hooks) goes with it.

Both overlay files live in your temp directory and are deleted when the session ends. Your real
configuration is never touched — smartctx **never** edits `settings.json` or `.claude.json`, and
it is **not** the nuclear `--bare` mode: your `CLAUDE.md`, hooks, and memory all stay in place.

### What it prunes — and what it doesn't

| | Scoped per session? |
|---|---|
| MCP servers | **Yes** — only the kept set loads |
| Plugins (and everything they provide) | **Yes** — dropped plugins are disabled |
| Standalone skills (`$CLAUDE_CONFIG_DIR/skills`) | **No** — inventoried and shown in `--explain`, but not removed (Claude Code offers only an all-or-nothing switch, which v1 leaves alone) |
| `CLAUDE.md`, hooks, memory | **No** — always preserved |

---

## Configuration

Everything is optional — smartctx works with zero configuration. When you do want to tune it,
settings are TOML and resolved through a chain, where **a later layer replaces an earlier one for
each key** (layers don't merge; a list value is overwritten wholesale):

1. Built-in defaults
2. User / profile — `$CLAUDE_CONFIG_DIR/smartctx/config.toml`
3. Repo-local — `./.smartctx/config.toml`
4. Environment — `SMARTCTX_ALWAYS_KEEP`, `SMARTCTX_THRESHOLD`, `SMARTCTX_RULE_MODEL`

| Key | Meaning | Default |
|---|---|---|
| `always_keep` | Item ids or glob patterns to never prune. Unknown ids are ignored. | *(empty)* |
| `threshold` | Cosine cutoff; an item is kept when its relevance score is `>= threshold`. Higher prunes more; lower keeps more. | `0.35` |
| `model_name` | The embedding model used for ranking. | `minishlab/potion-base-8M` |
| `rule_model_path` | Absolute path to a local instruct model for compiling natural-language rules (see below). | *(unset)* |

```toml
# .smartctx/config.toml
# always_keep below is an EXAMPLE — the shipped default is empty.
always_keep = ["superpowers", "remember", "caveman*"]

threshold = 0.35

# Use an absolute path — "~" is not expanded.
rule_model_path = "/Users/you/models/Qwen2.5-0.5B-Instruct.gguf"
```

On first run the embedding model is downloaded once to your standard cache. On an offline machine
smartctx falls back to a keyword-matching heuristic and warns — it still runs.

## Exclusion rules

Relevance ranking is good at "is this about the same topic," but it can't express intent like
*"this design-system plugin belongs only in work sessions, never personal ones."* Exclusion
rules do exactly that: they bind a tool (by id or glob) to a small deterministic rule that's
evaluated offline every launch.

**Precedence:** `always_keep` (kept no matter what) → your rules → similarity score.

A rule reads naturally in TOML:

```toml
[[rule]]
target = "camunda-*"
nl = "corporate design-system plugin — only for camunda / bpmn / work sessions"
[rule.predicate]
action = "keep_if"
match = ["camunda", "bpmn", "work", "orchestration"]
match_mode = "any"
```

- `keep_if` — keep the tool **only** when the goal matches; drop it otherwise.
- `drop_if` — drop it when the goal matches; otherwise fall through to normal ranking.
- `always_keep` / `always_drop` — unconditional.

`match` terms are compared case-insensitively against the goal and combined with `match_mode`
(`any` or `all`). Rules live at `$CLAUDE_CONFIG_DIR/smartctx/rules.toml` (yours) and/or
`./.smartctx/rules.toml` (this repo, which wins per `target`).

### Writing rules the easy way

You rarely need to hand-write the TOML. Two ways to author rules in plain language:

- **`smartctx rules`** walks through your tools and asks, for each one without a rule, how you
  want it scoped. Empty answer = skip.
- **At launch**, if smartctx is about to drop a tool you haven't ruled on (and you're in an
  interactive session), it offers to capture a rule on the spot.

Your plain-language answer is turned into a rule by a small **local instruct model**, enabled by
installing the optional extra:

```sh
pipx install "smartctx[rules]"
```

and pointing `rule_model_path` at a local model file. Without it, rule authoring falls back to a
simple *keep / drop / skip* prompt — and either way, **launches themselves never call a model;
they stay fully deterministic and offline.**

## Design guarantees

- **Session-local.** Scoping affects only the session it launches. Your Claude configuration is
  never modified. (smartctx does write two of its own files under your control: authored rules in
  `smartctx/rules.toml`, and a remembered goal in `./.smartctx/goal`.)
- **Fail-open, always.** A missing config, malformed rules file, unavailable model, or any other
  error degrades to launching the full, unscoped Claude Code — with a warning where it helps. The
  child process's exit code is passed straight back. smartctx can slim a session down; it can
  never stop one from starting.

## Requirements

- Python ≥ 3.11
- Claude Code
- Optional, for natural-language rule authoring: the `smartctx[rules]` extra
  (`llama-cpp-python`) plus a local GGUF instruct model
