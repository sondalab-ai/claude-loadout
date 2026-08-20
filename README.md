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
   marker files like `package.json` or `pyproject.toml`, and any project description it can find
   (a `description` field, or the README's title and opening line). If it can't tell, it asks once
   (and remembers your answer). This stays fully local and offline — no model call, just text.
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

You rarely need to hand-write the TOML. Two ways to author rules interactively — and **they write
to different places on purpose**:

- **`smartctx rules`** — the deliberate **profile-wide** path. Walks through your tools one by one
  (natural-language or *keep / drop / skip*) and asks how you want each scoped; the rule lands in
  `$CLAUDE_CONFIG_DIR/smartctx/rules.toml` and applies to **every repo in the profile**. Empty
  answer = skip. Use this for a tool you always want the same way everywhere.
- **At launch**, the [pre-launch gate](#adjusting-before-launch) lets you review and adjust the
  keep/drop for the current repo in one checkbox, and optionally **save** it — writing
  `./.smartctx/` (local, gitignored) so the choice is **scoped to that repo only** and never leaks
  to the others.

> Launch scoping you don't save is per-session — it changes nothing on disk. If you launch in a repo
> that isn't seeded, smartctx nudges you to run `smartctx init` to persist a full keep/drop set for
> it.

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

### Adjusting before launch

The first time you launch in an **unseeded** repo that actually scopes something out (interactive
session), smartctx pauses on a one-line summary before handing the terminal to Claude — so the
estimate and the *not seeded* nudge don't flash past, and you get a chance to adjust:

```
scoped out 4 of 11 prunable tools + 9 connectors · ~87.7k tokens trimmed (estimate)
this repo isn't seeded — run `smartctx init` to persist scoping for it
  [enter] launch · [e] edit keep/drop · [q] cancel?
```

- **enter** launches with the scoping as shown.
- **e** opens a checkbox of the prunable tools (pinned tools aren't listed — they always stay),
  pre-ticked to the current decision. Toggle, confirm, and smartctx re-composes the launch with your
  set. It then offers to **save** those choices to the repo (writing `./.smartctx/`, so future
  launches and `smartctx update` respect them) — decline to keep the edit to just this session.
- **q** cancels without launching.

**Once the repo is seeded** — you saved from the gate, or ran `smartctx init` — the pause stops:
smartctx prints the one-line estimate and launches straight through. Re-tune a seeded repo with
`smartctx update`. Non-interactive launches (`-p`, pipelines) never pause.

To skip the pause without seeding, pass `--no-gate` (or set `SMARTCTX_NO_GATE=1`): smartctx still
prints the estimate and the *not seeded* nudge, but launches immediately without prompting.

### Seeding repos — `smartctx init`

`smartctx rules` scopes one repo interactively. `smartctx init` writes the same
`.smartctx/config.toml` and `.smartctx/rules.toml` non-interactively, so scoping is in place before
you ever launch Claude in a checkout — for the current repo, or a whole directory of them at once.

```sh
smartctx init            # seed the current repo
smartctx init .          # bulk-seed every project folder under the current directory
smartctx init ~/src      # bulk-seed under a specific root
```

The argument is the switch, mirroring `smartctx update`: **no argument seeds the current repo
itself**; **a `ROOT` argument seeds every direct subfolder of `ROOT`** in bulk (dotfile dirs like
`.git` are ignored). The bulk run adds a project-picker step; otherwise both flows are identical.
For each project you keep:

1. **Picks the subset** *(bulk only)*. Projects that already carry a `.smartctx/config.toml` *or* an
   authored `.smartctx/rules.toml` are shown as *already configured* and skipped (never clobbered).
   The rest appear in a checkbox list (all ticked by default): `↑`/`↓` to move, space to toggle, `a`
   for all/none, enter to confirm, `q` to cancel. Where a raw terminal isn't available it falls back
   to a typed prompt (`1,3`, ranges like `2-4`, or `all`; empty cancels). Single-repo `init` skips
   this step — an already-configured repo is reported, with a nudge to run `smartctx update`.
2. **Confirms the profile.** If you run more than one Claude profile (`~/.claude`,
   `~/.claude-perso`, …), each project asks which one to inventory against — with a sticky default,
   so a work cluster and a personal cluster each take one keypress to switch. The chosen profile
   decides which tools exist, and therefore which rules get written.
3. **Confirms the goal.** smartctx shows the goal it auto-detected for the project; press enter to
   accept, type to override, or `s` to skip that project.
4. **Reviews the keep/drop.** After the goal, smartctx ranks the tools and shows the resulting
   keep/drop as a checkbox list, pre-ticked to its automatic decision (space toggles, enter
   confirms, `q` skips the project). Enter straight away accepts the auto decision; toggle to
   overrule it before it's frozen — the same control the single-repo launch flow gives you, in one
   screen. Where a raw terminal isn't available it accepts the auto decision silently.

Each seeded project then prints a receipt — the count line plus the full list of tools kept and
dropped — and the run ends with a summary that also names any projects you skipped and why.

Seeded files are **local, not committed**: init writes a `.smartctx/.gitignore` that ignores the
whole directory, so the generated (machine-derived) config never lands in git. This is the opposite
of a rule you author by hand with `smartctx rules`, which stays shareable — bulk-seeded scoping is
per-machine, hand-authored scoping is for the team.

For each seeded project it ranks the profile's tools against that goal and — after the keep/drop
review in step 4 — freezes that decision into `rules.toml` (only for the kinds launches actually
prune — MCP servers and plugins).
`config.toml` gets the resolved `threshold` and `model_name`. Pass **`--yes`** to run
non-interactively (every eligible project, auto-detected goals, auto keep/drop, active profile) —
required when there's no terminal, e.g. in a script.

> [!NOTE]
> The frozen decisions come from the *auto-detected* goal, which can be low-confidence for a project
> with few signals. Because they're written as `always_keep` / `always_drop` rules, they override
> per-session ranking for that project until you edit them — so review the seeded `rules.toml`, or
> re-run `smartctx rules` where you want a sharper goal. Note too that `--yes` assumes a single
> profile: it seeds every project against the *active* one, so with several profiles run it
> interactively, or once per profile with `CLAUDE_CONFIG_DIR` set and a narrower `ROOT`.

### Refreshing existing seeds — `smartctx update`

Seeds go stale: you install a new plugin or MCP server, the project's purpose shifts, or you want a
tighter keep/drop than the first pass gave you. `smartctx update` re-runs the decision over a repo
that `init` already seeded and rewrites its `.smartctx/`.

```sh
smartctx update            # refresh the repo you're standing in
smartctx update ~/src      # refresh every seeded project under a root (bulk)
```

- **Single vs bulk.** No argument updates the current repo; a `ROOT` updates every seeded project
  under it (bulk shows the same checkbox picker as `init`). "Seeded" means a repo `init` wrote —
  detected by its `.smartctx/config.toml`. A repo with only a hand-committed `rules.toml` (no
  config) is left untouched; a bulk run names it as *not seeded*, and a single run refuses.
- **Fresh goal.** update re-infers the goal from scratch (README/metadata as of now, ignoring the
  cached one), then lets you accept or override — so a project that changed direction gets a current
  goal, not the stale cache.
- **Inventory reconcile.** Tools added to the profile since the last seed appear in the review; tools
  that vanished drop out of the regenerated rules.
- **Your rules survive.** Rules you authored by hand (via `smartctx rules` or by editing the file)
  are shown in the keep/drop review pre-ticked to their current state and preserved verbatim — only
  the machine-written rules are regenerated. Flip a hand-authored decision in the review and update
  replaces just that one with a fresh machine rule. `--yes` refreshes non-interactively — but note
  that because it re-infers the goal, a goal you typed by hand at a previous run is replaced by the
  inferred one; run update interactively (and override at the goal prompt) to keep a hand-typed goal.

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
delete the config it wrote: the profile config (`$CLAUDE_CONFIG_DIR/smartctx/`) and every
per-repo `.smartctx/` it created. For the latter it scans a root you choose (defaulting to
`$HOME`; type `/` to sweep the whole filesystem), lists every smartctx-authored `.smartctx/`
directory found, and removes them only after a single confirmation. A directory is treated as
smartctx's only when it carries an artifact smartctx wrote (`goal`, `config.toml`,
`rules.toml`, or a self-ignore `.gitignore`), so an unrelated `.smartctx/` is left alone. It
never touches your shell rc — alias lines you added are listed for you to remove by hand.
