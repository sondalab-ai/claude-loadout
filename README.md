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

If you run `smartctx` (or `--explain` / `rules`) with **`CLAUDE_CONFIG_DIR` unset** and more than
one profile exists (`~/.claude`, `~/.claude-perso`, …), it asks which profile to use rather than
silently falling back to `~/.claude` — where your `always_keep` wouldn't apply. Set the variable
explicitly (as in the aliases above) to skip the prompt. There is no default at the prompt: pick a
number, or press `Ctrl-D` to cancel without launching.

### Installing from source

Working from a clone? Install the CLI from the repo instead:

```sh
make install   # pipx install . --force
```

Re-run it after any code change — pipx keeps the previously built copy until you reinstall, so
edits to the source won't reach the `smartctx` on your `PATH` until you `make install` again.
`make dev` gives you an editable install (`pip install -e ".[dev]"`) if you'd rather skip that
step while hacking, and `make test` runs the suite.

## See what it would do — before it does it

Curious, or tuning things? Add `--explain` and smartctx prints its plan and exits **without
launching anything**:

```sh
smartctx --explain
```

You'll see the goal it detected, which items it would keep, which it would drop (and why), and
the exact command it would run. It's the best way to get a feel for the tool and to calibrate how
aggressively it prunes.

## Check your setup — and what to do next

Just installed, or unsure whether things are wired up? `smartctx doctor` inspects your
environment without launching anything and prints the next steps:

```sh
smartctx doctor
```

It enumerates every Claude profile it finds (`~/.claude`, `~/.claude-perso`, …), marks the one
selected by `CLAUDE_CONFIG_DIR` as active, and for each shows which config files exist and how many
MCP servers / plugins / skills it would inventory. It then reports which embedding model is in use
(bundled, external, or keyword fallback), whether a rule model is configured — and finishes with the
alias + `--explain` + `rules` cheat sheet to get going.

## How much it saves

Every place that shows a plan also shows how much context it trims:

- **`--explain`** and **each scoped launch** report the tools pruned this session and an
  estimated token saving (`≈ 2.4k tokens trimmed`).
- **`smartctx doctor`** shows the *ceiling* per profile — the most a session could prune —
  since the actual amount depends on the goal.
- **`smartctx rules`** closes with the same ceiling once you're done authoring.

> [!NOTE]
> By default these figures are **estimates**. The real cost of an MCP server is the tool
> schemas it injects once connected, which smartctx can't see at plan time — so it uses a
> flat per-kind figure (MCP server ≈ 1200 tokens, plugin ≈ 600) over the items it actually
> removes. Standalone skills are never counted: they're shown but not pruned. Tune the
> constants per kind:

```toml
# .smartctx/config.toml
[token_costs]
mcp = 1500
plugin = 800
```

### Measuring the real cost — `smartctx measure`

For actual numbers instead of estimates, `smartctx measure` connects to each MCP server,
runs the MCP handshake (`initialize` → `tools/list`), and tokenizes the real tool set:

```sh
smartctx measure
```

It's **opt-in** because it makes network/subprocess connections (unlike every other
command, which stays offline). Servers that need credentials you don't hold, fail to
connect, or time out are reported `unmeasured` — never counted as zero. The count uses a
~4-chars/token heuristic, so it's labelled `(measured, heuristic)` rather than exact —
it's not Claude's own tokenizer. Results are cached to `$CLAUDE_CONFIG_DIR/smartctx/costs.json`.

`measure` is also a **diagnostic** — it shows the cost of *every* server Claude Code knows,
including claude.ai connectors and plugin-bundled servers. Once cached, those measured
numbers feed the savings display in two ways:

- for MCP servers declared in your `.claude.json`/`.mcp.json` (matched by bare name), the
  measured cost replaces the per-kind estimate in the ranked-tools total;
- for **claude.ai connectors**, the measured cost is what `--explain`, `doctor`, and each
  launch report as dropped by strict mode (see the pruning table below) — before you run
  `measure` there's no offline way to know they exist, so they only appear afterwards.

---

## How it works

Under the hood, a scoped launch is a normal Claude Code session plus two small, temporary overlay
files:

- **MCP servers** — smartctx writes a curated MCP config listing only the kept servers and starts
  Claude with `--strict-mcp-config`, so only those load. This flag also excludes your claude.ai
  account connectors for that session (see the table below).
- **Plugins** — dropped plugins are switched off via a `--settings` overlay. Anything a plugin
  provides (its skills, agents, MCP servers, hooks) goes with it.

Both overlay files live in your temp directory and are deleted when the session ends. Your real
configuration is never touched — smartctx **never** edits `settings.json` or `.claude.json`, and
it is **not** the nuclear `--bare` mode: your `CLAUDE.md`, hooks, and memory all stay in place.

### What it prunes — and what it doesn't

| | Scoped per session? |
|---|---|
| MCP servers (`.claude.json` / `.mcp.json`) | **Yes** — only the kept set loads |
| Plugins (and everything they provide) | **Yes** — dropped plugins are disabled |
| claude.ai connectors (Gmail, Calendar, …) | **All dropped (all-or-nothing in v1)** — `--strict-mcp-config` loads only the curated overlay, so account connectors don't load at all. A connector that needs account authorization (Gmail, Calendar, …) can't be re-added even if smartctx wanted to: its OAuth lives in your claude.ai account and doesn't transfer to a config smartctx can pass to Claude (verified — the re-injected server reports "not authorized"). Connectors that need no auth *are* technically re-injectable, but v1 keeps none either way. Run `smartctx measure` to see them listed with their token cost in `--explain`. |
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
| `threshold` | Cosine cutoff; an item is kept when its relevance score is `>= threshold`. Higher prunes more; lower keeps more. Calibrate with `--explain`. | `0.20` |
| `model_name` | The embedding model used for ranking. | `minishlab/potion-base-8M` |
| `rule_model_path` | Absolute path to a local GGUF instruct model for compiling natural-language rules. **Not bundled — you supply it.** Unset ⇒ natural-language rule authoring is off (you still get the *keep / drop / skip* prompt). See [Exclusion rules](#exclusion-rules). | *(unset)* |

```toml
# .smartctx/config.toml
# always_keep below is an EXAMPLE — the shipped default is empty.
always_keep = ["superpowers", "remember", "caveman*"]

threshold = 0.20

# Optional: only needed for natural-language rule authoring (see "Exclusion rules").
# This model is NOT shipped with smartctx — download a GGUF yourself and point here.
# Use an absolute path — "~" is not expanded.
rule_model_path = "/Users/you/models/Qwen2.5-0.5B-Instruct.gguf"
```

The default embedding model ships **inside the package** (~29 MB, `minishlab/potion-base-8M`,
MIT-licensed) — a fresh install ranks offline out of the box, with no first-run download. Point
`model_name` at another model2vec model (a Hub id or a local directory) only if you want to
override the default; a Hub id is fetched on demand, and if a model can't be loaded at all
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
target = "design-system-*"
nl = "corporate design-system plugin — only for work sessions"
[rule.predicate]
action = "keep_if"
match = ["design system", "frontend", "work", "ui"]
match_mode = "any"
```

- `keep_if` — keep the tool **only** when the goal matches; drop it otherwise.
- `drop_if` — drop it when the goal matches; otherwise fall through to normal ranking.
- `always_keep` / `always_drop` — unconditional.

`match` terms are compared case-insensitively against the goal and combined with `match_mode`
(`any` or `all`). Rules live at `$CLAUDE_CONFIG_DIR/smartctx/rules.toml` (yours) and/or
`./.smartctx/rules.toml` (this repo, which wins per `target`).

### Writing rules the easy way

You rarely need to hand-write the TOML. Two ways to author rules interactively:

- **`smartctx rules`** walks through your tools and asks, for each one without a rule, how you
  want it scoped. Empty answer = skip.
- **At launch**, if smartctx is about to drop a tool you haven't ruled on (and you're in an
  interactive session), it offers to capture a rule on the spot.

> [!IMPORTANT]
> **Natural-language rules need setup that isn't included by default.** Out of the box these
> prompts offer only a fixed *keep always / drop always / skip* choice — plain-English answers
> like *"only in work sessions"* do **not** work until you add both of the following:
>
> 1. **The optional extra** (adds `llama-cpp-python`):
>    ```sh
>    pipx install "smartctx[rules]"
>    ```
> 2. **A local GGUF instruct model** — download one yourself (e.g. `Qwen2.5-0.5B-Instruct.gguf`;
>    smartctx does **not** ship it) and set [`rule_model_path`](#configuration) to its absolute path.
>
> This is deliberate: a GGUF instruct model is hundreds of MB (vs. the ~29 MB embedding model that
> *is* bundled), and it runs only while you author rules. **Launches themselves never call any
> instruct model** — they stay fully deterministic and offline regardless.

With both in place, your plain-language answer is compiled into a rule by the local model. If a
particular answer can't be translated, that one item falls back to the *keep / drop / skip* choice.

## Design guarantees

- **Session-local.** Scoping affects only the session it launches. Your Claude configuration is
  never modified. (smartctx does write two of its own files under your control: authored rules in
  `smartctx/rules.toml`, and a remembered goal in `./.smartctx/goal`.)
- **Fail-open, always.** A missing config, malformed rules file, unavailable model, or any other
  error degrades to launching the full, unscoped Claude Code — with a warning where it helps. The
  child process's exit code is passed straight back. smartctx can slim a session down; it can
  never stop one from starting.

## Requirements

- Python ≥ 3.11 (runtime deps `model2vec` and `numpy` install automatically)
- Claude Code
- Optional, for natural-language rule authoring: the `smartctx[rules]` extra
  (`llama-cpp-python`) plus a local GGUF instruct model
- The uninstall script is bash (the tool itself is platform-independent)

## Uninstalling

`scripts/uninstall.sh` removes the package (pipx, falling back to pip) and asks whether to
delete the config it wrote (`$CLAUDE_CONFIG_DIR/smartctx/` and this repo's `.smartctx/`). It
never touches your shell rc — alias lines you added are listed for you to remove by hand.
