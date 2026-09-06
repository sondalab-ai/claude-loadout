# Claude Loadout

[![PyPI](https://img.shields.io/pypi/v/ccloadout.svg)](https://pypi.org/project/ccloadout/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://pypi.org/project/ccloadout/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

**Start each Claude Code session with only the tools it needs.**

`claude-loadout` is a small launcher that sits in front of Claude Code. It looks at what you are working
on, figures out which of your installed MCP servers, plugins, and skills are actually relevant,
and starts the session with just those. Everything else stays installed and untouched; it simply
is not loaded for that run.

## TL;DR

- **Problem:** every session loads *all* your MCP servers, plugins, and skills. Each one puts its
  self-description into the context window, and every skill's description is also something Claude
  weighs when it decides what to auto-invoke. More installed means a more crowded, noisier start.
- **Fix:** `claude-loadout` scopes each session to the tools that fit the task. It detects the goal, ranks
  your tools against it with a local model, applies your keep/drop rules, and launches Claude Code
  with only the relevant subset.
- **Scoped once, then reused:** the first time you scope a repo, the decision is saved to
  `.loadout/`. Every later launch reuses it; you refresh it deliberately with `claude-loadout update`. An
  unseeded repo is scoped on the fly at launch.
- **Also your notes, optionally:** the same ranking can put the memories relevant to *this* session
  into it — and only those, inside a token budget you set. Off until you run `cld memory enable`.
  See [Memory recall](#memory-recall).
- **Safe:** nothing about your global setup changes, and if anything fails claude-loadout falls back to a
  normal full session.
- **Install:** `pipx install ccloadout`, then type `claude-loadout` (or its short alias `cld`) in place of `claude` (`cld -p "..."`). It forwards every argument to Claude Code. Aliases are optional.

---

## Why

Every Claude Code session loads *all* of your installed MCP servers, plugins, and skills: a
calendar integration, a browser driver, three documentation servers, a design system, whether or
not today's task touches them. Two costs stack up before you type a word:

- **Context.** Each server and plugin puts its own description into the session, and skills appear
  in `/context` too. That is window spent on tools you are not using today.
- **Auto-triggering.** A skill's `description` is what Claude reads to decide when to load the
  skill on its own ([Claude Code docs](https://code.claude.com/docs/en/skills)). Every skill you
  install adds one more candidate Claude weighs whenever a request looks relevant, so a crowded set
  is also more chances to reach for the wrong one.

The more you install, the noisier every session starts, whatever the task.

`claude-loadout` addresses that per session, with nothing to toggle by hand. You keep everything
installed. The first time you scope a repo, claude-loadout decides what is worth bringing in and saves
that choice; later launches reuse it, and you refresh it when you want with `claude-loadout update`.

## What it does

1. **Figures out the goal.** It reads signals from your working directory, the folder name,
   marker files like `package.json` or `pyproject.toml`, and any project description it can find
   (a `description` field, or the README's title and opening line). If it can't tell, it asks once
   (and remembers your answer). This stays fully local and offline, no model call, just text.
2. **Ranks your tools against that goal** using a small, fast, local model, no network call, no
   data leaving your machine.
3. **Applies your rules.** You can pin tools to always keep, and write plain-language rules like
   *"this corporate plugin only in work sessions."*
4. **Launches Claude Code with the relevant subset.** The off-topic servers and plugins simply
   aren't loaded for that session.

If anything goes wrong at any step, claude-loadout quietly launches the full, normal session instead -
**it can never leave you unable to start Claude.**

## Quick start

```sh
pipx install ccloadout
```

The command installs under two names for the same entrypoint: `claude-loadout` (canonical) and
`cld` (a shorter alias for everyday use). The examples below use `claude-loadout`; substitute `cld`
wherever you prefer.

The simplest use is to run `claude-loadout` directly, exactly where you would run `claude`: `claude-loadout`,
`claude-loadout -p "summarize this repo"`, and so on. Every argument is forwarded to Claude Code untouched.

Aliases are an optional convenience. `claude-loadout` figures out which Claude profile you're using from
the `CLAUDE_CONFIG_DIR` environment variable (default `~/.claude`) and passes it straight through,
so one install wraps any alias, use whatever names you already have:

```sh
alias claude-work="CLAUDE_CONFIG_DIR=~/.claude-work claude-loadout"
alias claude-perso="CLAUDE_CONFIG_DIR=~/.claude-perso claude-loadout"

# Optional, wrap plain `claude` too:
# alias claude="claude-loadout"
```

That's it. Run `claude-work` (or whatever you aliased) as you always have, every Claude Code
argument you pass is forwarded untouched, e.g. `claude-work -p "summarize this repo"`.

If you run `claude-loadout` (or `--explain` / `rules`) with **`CLAUDE_CONFIG_DIR` unset** and more than
one profile exists (`~/.claude`, `~/.claude-perso`, ...), it asks which profile to use rather than
silently falling back to `~/.claude`, where your `always_keep` wouldn't apply. Set the variable
explicitly (as in the aliases above) to skip the prompt. There is no default at the prompt: pick a
number, or press `Ctrl-D` to cancel without launching.

### Installing from source

Working from a clone? Install the CLI from the repo instead:

```sh
make install   # pipx install . --force
```

Re-run it after any code change, pipx keeps the previously built copy until you reinstall, so
edits to the source won't reach the `claude-loadout` on your `PATH` until you `make install` again.
`make dev` gives you an editable install (`pip install -e ".[dev]"`) if you'd rather skip that
step while hacking, and `make test` runs the suite.

## See what it would do, before it does it

Curious, or tuning things? Add `--explain` and claude-loadout prints its plan and exits **without
launching anything**:

```sh
claude-loadout --explain
```

You'll see the goal it detected, which items it would keep, which it would drop (and why), and
the exact command it would run. It's the best way to get a feel for the tool and to calibrate how
aggressively it prunes.

## Check your setup, and what to do next

Just installed, or unsure whether things are wired up? `claude-loadout doctor` inspects your
environment without launching anything and prints the next steps:

```sh
claude-loadout doctor
```

It enumerates every Claude profile it finds (`~/.claude`, `~/.claude-perso`, ...), marks the one
selected by `CLAUDE_CONFIG_DIR` as active, and for each shows which config files exist and how many
MCP servers / plugins / skills it would inventory. It then reports which embedding model is in use
(bundled, external, or keyword fallback), whether a rule model is configured, and finishes with the
alias + `--explain` + `rules` cheat sheet to get going.

## How much it saves

The plan reports two different kinds of saving, kept separate on purpose:

- **Up front**: skill and plugin descriptions (and the agent lists plugins carry) sit in the
  system prompt from the first turn, so dropping them trims context immediately. This is the
  `trimmed up front` figure in `--explain` and each launch.
- **On demand**: MCP tool schemas, claude.ai connectors included, load lazily: Claude Code lists
  them as *loaded on-demand*, so their cost lands only if a tool is actually used. Dropping them
  (via `--strict-mcp-config`) avoids that potential cost and blocks the invocation, but frees
  ~nothing up front. It's reported separately as `on-demand avoided`, don't add it to the
  up-front number.
- **`claude-loadout doctor`** shows the *ceiling* per profile for both; **`claude-loadout rules`** closes with
  the same once you're done authoring.

> [!NOTE]
> These are **estimates** over the items actually removed, flat per-kind figures
> (skill ≈ 50, plugin ≈ 600 up front; MCP server ≈ 1200 on-demand), unless `claude-loadout measure`
> has recorded a real per-server cost. Tune the constants per kind:

```toml
# .loadout/config.toml
[token_costs]
mcp = 1500
plugin = 800
```

### Measuring the real cost, `claude-loadout measure`

For actual numbers instead of estimates, `claude-loadout measure` connects to each MCP server,
runs the MCP handshake (`initialize` → `tools/list`), and tokenizes the real tool set:

```sh
claude-loadout measure
```

It's **opt-in** because it makes network/subprocess connections (unlike every other
command, which stays offline). Servers that need credentials you don't hold, fail to
connect, or time out are reported `unmeasured`, never counted as zero. The count uses a
~4-chars/token heuristic, so it's labelled `(measured, heuristic)` rather than exact -
it's not Claude's own tokenizer. Results are cached to `$CLAUDE_CONFIG_DIR/loadout/costs.json`.

`measure` is also a **diagnostic**: it shows the cost of *every* server Claude Code knows,
including claude.ai connectors and plugin-bundled servers. Once cached, those measured
numbers feed the savings display in two ways:

- for MCP servers declared in your `.claude.json`/`.mcp.json` (matched by bare name), the
  measured cost replaces the per-kind estimate in the on-demand figure;
- for **claude.ai connectors**, the measured cost is what `--explain`, `doctor`, and each
  launch report as dropped by strict mode (see the pruning table below), before you run
  `measure` there's no offline way to know they exist, so they only appear afterwards.

---

## How it works

Under the hood, a scoped launch is a normal Claude Code session plus two small, temporary overlay
files:

- **MCP servers**: claude-loadout writes a curated MCP config listing only the kept servers and starts
  Claude with `--strict-mcp-config`, so only those load. This flag also excludes your claude.ai
  account connectors for that session (see the table below).
- **Plugins**: dropped plugins are switched off via a `--settings` overlay. Anything a plugin
  provides (its skills, agents, MCP servers, hooks) goes with it.

Both overlay files live in your temp directory and are deleted when the session ends. Your real
configuration is never touched, claude-loadout **never** edits `settings.json` or `.claude.json`, and
it is **not** the nuclear `--bare` mode: your `CLAUDE.md`, hooks, and memory all stay in place.

### What it prunes, and what it doesn't

| | Scoped per session? |
|---|---|
| MCP servers (`.claude.json` / `.mcp.json`) | **Yes**: only the kept set loads |
| Plugins (and everything they provide) | **Yes**: dropped plugins are disabled |
| claude.ai connectors (Gmail, Calendar, ...) | **All dropped (all-or-nothing in v1)**: `--strict-mcp-config` loads only the curated overlay, so account connectors don't load at all. A connector that needs account authorization (Gmail, Calendar, ...) can't be re-added even if claude-loadout wanted to: its OAuth lives in your claude.ai account and doesn't transfer to a config claude-loadout can pass to Claude (verified, the re-injected server reports "not authorized"). Connectors that need no auth *are* technically re-injectable, but v1 keeps none either way. Run `claude-loadout measure` to see them listed with their token cost in `--explain`. |
| Standalone skills (`$CLAUDE_CONFIG_DIR/skills`) | **Yes**: off-topic skills are dropped via `skillOverrides: "off"` (removes the skill and its description from context). On by default; `--no-scope-skills` keeps them all |
| `CLAUDE.md`, hooks, memory | **No**: always preserved |

---

## Configuration

Everything is optional, claude-loadout works with zero configuration. When you do want to tune it,
settings are TOML and resolved through a chain, where **a later layer replaces an earlier one for
each key** (layers don't merge; a list value is overwritten wholesale):

1. Built-in defaults
2. User / profile, `$CLAUDE_CONFIG_DIR/loadout/config.toml`
3. Repo-local, `./.loadout/config.toml`
4. Environment, `LOADOUT_ALWAYS_KEEP`, `LOADOUT_THRESHOLD`, `LOADOUT_RULE_MODEL`

| Key | Meaning | Default |
|---|---|---|
| `always_keep` | Item ids or glob patterns to never prune. Unknown ids are ignored. | *(empty)* |
| `threshold` | Cosine cutoff; an item is kept when its relevance score is `>= threshold`. Higher prunes more; lower keeps more. Calibrate with `--explain`. | `0.24` |
| `model_name` | The embedding model used for ranking. | `minishlab/potion-base-8M` |
| `[memory]` | Opt-in session memory recall. See [Memory recall](#memory-recall). | *(off)* |
| `rule_model_path` | Absolute path to a local GGUF instruct model for compiling natural-language rules. **Not bundled, you supply it.** Unset means natural-language rule authoring is off (you still get the *keep / drop / skip* prompt). See [Exclusion rules](#exclusion-rules). | *(unset)* |

```toml
# .loadout/config.toml
# always_keep below is an EXAMPLE, the shipped default is empty.
always_keep = ["superpowers", "remember", "caveman*"]

threshold = 0.24

# Optional: only needed for natural-language rule authoring (see "Exclusion rules").
# This model is NOT shipped with claude-loadout, download a GGUF yourself and point here.
# Use an absolute path, "~" is not expanded.
rule_model_path = "/Users/you/models/Qwen2.5-0.5B-Instruct.gguf"
```

The default embedding model ships **inside the package** (~29 MB, `minishlab/potion-base-8M`,
MIT-licensed), a fresh install ranks offline out of the box, with no first-run download. Point
`model_name` at another model2vec model (a Hub id or a local directory) only if you want to
override the default; a Hub id is fetched on demand, and if a model can't be loaded at all
claude-loadout falls back to a keyword-matching heuristic and warns, it still runs.

## Memory recall

Your notes from past sessions, ranked against what this session is about, and only the ones that fit
a token budget. **Off until you turn it on.**

```sh
cld memory enable      # this repository
cld memory             # status, and what to do next
```

`cld init` asks once per run whether to enable it for the repositories it seeds. Everything is
reversible with `cld memory disable`.

### How the pieces fit

```
   you write a note ──┐
                      ├─→  store  ──rank──→  session context  ──→  cld recall (session asks for more)
   a session ends ────┘      ↑                     │
   (candidate)               │                     └──→  cld memory flag  (session says "this is wrong")
                             └── cld memory audit  ←──────────────┘
```

Four verbs, in the order you meet them:

| | |
|---|---|
| `cld memory add "<note>"` | write something down now |
| `cld memory consolidate` | turn finished sessions into notes, one confirmation each |
| `cld recall "<query>"` | search the store and print entries in full |
| `cld memory audit` | review what you have, delete what has gone stale |

### Where notes live

It reads stores that already exist rather than inventing another one:

- `./docs/memory/` in the repository (git-tracked — this is where new notes go by default)
- `$CLAUDE_CONFIG_DIR/projects/<slug>/memory/` — Claude Code's own memory directory
- `$CLAUDE_CONFIG_DIR/loadout/memory/` for notes that apply across repositories
- your `debug-decisions` corpus, if you keep one

```toml
# .loadout/config.toml
[memory]
enabled = true
budget_tokens = 800     # ceiling on what recall may inject
threshold = 0.24        # relevance cutoff, same scale as tool ranking
git_tracked = true      # new notes land in ./docs/memory and travel with the repo
promote_after = 3       # deliveries after which a note is pinned into recall
decay_days = 90         # untouched for this long, a note is demoted (never deleted)
prompt_recall = false   # also re-rank on every prompt (see below)
```

### What it costs, and what it saves

`cld --explain` shows both, because recall spends the tokens pruning saves:

```
  memory
    injected:       6 of 7 entries  ≈ 366 tokens (heuristic)

  savings
    up front:       ≈ 3.5k tokens — skill + plugin context, gone from turn one
    net up front:   ≈ 3.1k tokens — after the memory payload (gain)
```

### Auditing

Notes rot. `cld memory audit` lists them with the signals that decide whether they still earn their
place — how often used, an anchor that disappeared, a flag someone raised. Unchecking marks a note
for deletion; nothing is removed until you type `delete`. Add `--all-repos` for every project at
once, which is where the forgotten ones live.

To see why a session recalled what it did:

```
$ cld memory audit --context "how does skill scoping work"
  what a session on 'how does skill scoping work' would recall
  threshold 0.24 · budget 800 tokens

    ✓ 0.632  deci 2026-08-21-1326-per-session-skill-sco… Per-session skill scoping: settings-f…
    ✓ 0.536  memo skill-scoping-mechanism                How loadout prunes user-level skills…
    ── below the line ──
    · 0.157  memo strict-mcp-config-connectors           How --strict-mcp-config affects clau…  below-threshold
```

Every note gets a score and a reason: `below-threshold`, `over-budget`, `stale-anchor`, `decayed`,
`promoted`, `flagged`, `resolved`.

### When a session finds a bad note

It can say so, but not act on it:

```sh
cld memory flag skill-scoping-mechanism --reason "names a lever renamed in 2.1.261"
```

The flag is stored beside your notes, never inside them. It demotes the note straight away and
hides it from per-prompt recall; you resolve it in the audit, or clear it with `--clear`.
`cld memory audit --json` gives a session the whole store to read. Deleting stays yours.

### Debt you left behind

The thing neither a session log nor a decision record captures is the shim you meant to remove:

```sh
cld debt add --anchor src/ccloadout/rules.py "fail-fast stub until the compiler lands"
cld debt list
cld debt resolve fail-fast-stub-until-the-compiler-lands
```

A file changing under an anchor never closes an entry by itself — an unrelated edit would silently
close real debt. It flags it and leaves the call to you. Resolved entries stop being injected but
stay findable with `cld recall`.

### Decisions

`cld decision new|list|show|supersede` writes the same files as the `debug-decisions` skill, in the
same directory, so both tools see one corpus. `cld decision revert` is deliberately absent:
executing destructive git operations does not belong in a launcher.

### Recall on every prompt (optional)

`prompt_recall = true` adds a hook that re-ranks the store against what you actually typed and adds
at most two notes the session did not already have. It scores lexically rather than with the
embedding model — loading that costs ~520 ms, and this runs on every prompt — and measures **38 ms
median, 56 ms worst** end to end.

It exits successfully on every path, including its own timeout: on `UserPromptSubmit` a failing hook
does not merely error, it erases what you were typing.

> **One caution.** Injected notes sit in the highest-trust position a session has. The block says so
> — it is labelled untrusted reference data — because with `git_tracked = true` a note can reach you
> through a merged pull request. Set it to `false`, or leave `[memory]` off, in repositories whose
> notes you would not accept as reference material.

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
nl = "corporate design-system plugin, only for work sessions"
[rule.predicate]
action = "keep_if"
match = ["design system", "frontend", "work", "ui"]
match_mode = "any"
```

- `keep_if`, keep the tool **only** when the goal matches; drop it otherwise.
- `drop_if`, drop it when the goal matches; otherwise fall through to normal ranking.
- `always_keep` / `always_drop`, unconditional.

`match` terms are compared case-insensitively against the goal and combined with `match_mode`
(`any` or `all`). Rules live at `$CLAUDE_CONFIG_DIR/loadout/rules.toml` (yours) and/or
`./.loadout/rules.toml` (this repo, which wins per `target`).

### Writing rules the easy way

You rarely need to hand-write the TOML. Two ways to author rules interactively, and **they write
to different places on purpose**:

- **`claude-loadout rules`**: the deliberate **profile-wide** path. Walks through your tools one by one
  (natural-language or *keep / drop / skip*) and asks how you want each scoped; the rule lands in
  `$CLAUDE_CONFIG_DIR/loadout/rules.toml` and applies to **every repo in the profile**. Empty
  answer = skip. Use this for a tool you always want the same way everywhere.
- **At launch**, the [pre-launch gate](#adjusting-before-launch) lets you review and adjust the
  keep/drop for the current repo in one checkbox, and optionally **save** it, writing
  `./.loadout/` (local, gitignored) so the choice is **scoped to that repo only** and never leaks
  to the others.

> Launch scoping you don't save is per-session, it changes nothing on disk. If you launch in a repo
> that isn't seeded, claude-loadout nudges you to run `claude-loadout init` to persist a full keep/drop set for
> it.

> [!IMPORTANT]
> **Natural-language rules need setup that isn't included by default.** Out of the box these
> prompts offer only a fixed *keep always / drop always / skip* choice, plain-English answers
> like *"only in work sessions"* do **not** work until you add both of the following:
>
> 1. **The optional extra** (adds `llama-cpp-python`):
>    ```sh
>    pipx install "ccloadout[rules]"
>    ```
> 2. **A local GGUF instruct model**: download one yourself (e.g. `Qwen2.5-0.5B-Instruct.gguf`;
>    claude-loadout does **not** ship it) and set [`rule_model_path`](#configuration) to its absolute path.
>
> This is deliberate: a GGUF instruct model is hundreds of MB (vs. the ~29 MB embedding model that
> *is* bundled), and it runs only while you author rules. **Launches themselves never call any
> instruct model**: they stay fully deterministic and offline regardless.

With both in place, your plain-language answer is compiled into a rule by the local model. If a
particular answer can't be translated, that one item falls back to the *keep / drop / skip* choice.

### Adjusting before launch

The first time you launch in an **unseeded** repo that actually scopes something out (interactive
session), claude-loadout pauses on a one-line summary before handing the terminal to Claude, so the
estimate and the *not seeded* nudge don't flash past, and you get a chance to adjust:

```
scoped out 4 of 11 tools + 9 connectors · ~2.4k trimmed up front · ~85.3k on-demand avoided
this repo isn't seeded, run `claude-loadout init` to persist scoping for it
  [enter] launch · [e] edit keep/drop · [q] cancel?
```

- **enter** launches with the scoping as shown.
- **e** opens a checkbox of the prunable tools (pinned tools aren't listed, they always stay),
  pre-ticked to the current decision. Toggle, confirm, and claude-loadout re-composes the launch with your
  set. It then offers to **save** those choices to the repo (writing `./.loadout/`, so future
  launches and `claude-loadout update` respect them), decline to keep the edit to just this session.
- **q** cancels without launching.

**Once the repo is seeded**: you saved from the gate, or ran `claude-loadout init`, the prompt stops:
claude-loadout prints the one-line estimate, holds it on screen for a moment (~1.5s, `Ctrl-C` to abort),
then launches, long enough to read, without a keypress. Re-tune a seeded repo with `claude-loadout
update`. Non-interactive launches (`-p`, pipelines) never pause.

To launch instantly with no pause at all, pass `--no-gate` (or set `LOADOUT_NO_GATE=1`): claude-loadout
still prints the estimate and the *not seeded* nudge, but hands straight to claude.

To keep every standalone skill loaded (skip skill scoping for a session), pass `--no-scope-skills`
(or set `LOADOUT_NO_SCOPE_SKILLS=1`).

### Seeding repos, `claude-loadout init`

`claude-loadout rules` scopes one repo interactively. `claude-loadout init` writes the same
`.loadout/config.toml` and `.loadout/rules.toml` non-interactively, so scoping is in place before
you ever launch Claude in a checkout, for the current repo, or a whole directory of them at once.

```sh
claude-loadout init            # seed the current repo
claude-loadout init .          # bulk-seed every project folder under the current directory
claude-loadout init ~/src      # bulk-seed under a specific root
```

The argument is the switch, mirroring `claude-loadout update`: **no argument seeds the current repo
itself**; **a `ROOT` argument seeds every direct subfolder of `ROOT`** in bulk (dotfile dirs like
`.git` are ignored). The bulk run adds a project-picker step; otherwise both flows are identical.
For each project you keep:

1. **Picks the subset** *(bulk only)*. Projects that already carry a `.loadout/config.toml` *or* an
   authored `.loadout/rules.toml` are shown as *already configured* and skipped (never clobbered).
   The rest appear in a checkbox list (all ticked by default): `↑`/`↓` to move, space to toggle, `a`
   for all/none, enter to confirm, `q` to cancel. Where a raw terminal isn't available it falls back
   to a typed prompt (`1,3`, ranges like `2-4`, or `all`; empty cancels). Single-repo `init` skips
   this step, an already-configured repo is reported, with a nudge to run `claude-loadout update`.
2. **Confirms the profile.** If you run more than one Claude profile (`~/.claude`,
   `~/.claude-perso`, ...), each project asks which one to inventory against, with a sticky default,
   so a work cluster and a personal cluster each take one keypress to switch. The chosen profile
   decides which tools exist, and therefore which rules get written.
3. **Confirms the goal.** claude-loadout shows the goal it auto-detected for the project; press enter to
   accept, type to override, or `s` to skip that project.
4. **Reviews the keep/drop.** After the goal, claude-loadout ranks the tools and shows the resulting
   keep/drop as a checkbox list, pre-ticked to its automatic decision (space toggles, enter
   confirms, `q` skips the project). Enter straight away accepts the auto decision; toggle to
   overrule it before it's frozen, the same control the single-repo launch flow gives you, in one
   screen. Where a raw terminal isn't available it accepts the auto decision silently.

Each seeded project then prints a receipt, the count line plus the full list of tools kept and
dropped, and the run ends with a summary that also names any projects you skipped and why.

Seeded files are **local, not committed**: init writes a `.loadout/.gitignore` that ignores the
whole directory, so the generated (machine-derived) config never lands in git. This is the opposite
of a rule you author by hand with `claude-loadout rules`, which stays shareable, bulk-seeded scoping is
per-machine, hand-authored scoping is for the team.

For each seeded project it ranks the profile's tools against that goal and, after the keep/drop
review in step 4, freezes that decision into `rules.toml` (only for the kinds launches actually
prune, MCP servers and plugins).
`config.toml` gets the resolved `threshold` and `model_name`. Pass **`--yes`** to run
non-interactively (every eligible project, auto-detected goals, auto keep/drop, active profile) -
required when there's no terminal, e.g. in a script.

> [!NOTE]
> The frozen decisions come from the *auto-detected* goal, which can be low-confidence for a project
> with few signals. Because they're written as `always_keep` / `always_drop` rules, they override
> per-session ranking for that project until you edit them, so review the seeded `rules.toml`, or
> re-run `claude-loadout rules` where you want a sharper goal. Note too that `--yes` assumes a single
> profile: it seeds every project against the *active* one, so with several profiles run it
> interactively, or once per profile with `CLAUDE_CONFIG_DIR` set and a narrower `ROOT`.

### Refreshing existing seeds, `claude-loadout update`

Seeds go stale: you install a new plugin or MCP server, the project's purpose shifts, or you want a
tighter keep/drop than the first pass gave you. `claude-loadout update` re-runs the decision over a repo
that `init` already seeded and rewrites its `.loadout/`.

```sh
claude-loadout update            # refresh the repo you're standing in
claude-loadout update ~/src      # refresh every seeded project under a root (bulk)
```

- **Single vs bulk.** No argument updates the current repo; a `ROOT` updates every seeded project
  under it (bulk shows the same checkbox picker as `init`). "Seeded" means a repo `init` wrote -
  detected by its `.loadout/config.toml`. A repo with only a hand-committed `rules.toml` (no
  config) is left untouched; a bulk run names it as *not seeded*, and a single run refuses.
- **Fresh goal.** update re-infers the goal from scratch (README/metadata as of now, ignoring the
  cached one), then lets you accept or override, so a project that changed direction gets a current
  goal, not the stale cache.
- **Inventory reconcile.** Tools added to the profile since the last seed appear in the review; tools
  that vanished drop out of the regenerated rules.
- **Your rules survive.** Rules you authored by hand (via `claude-loadout rules` or by editing the file)
  are shown in the keep/drop review pre-ticked to their current state and preserved verbatim, only
  the machine-written rules are regenerated. Flip a hand-authored decision in the review and update
  replaces just that one with a fresh machine rule. `--yes` refreshes non-interactively, but note
  that because it re-infers the goal, a goal you typed by hand at a previous run is replaced by the
  inferred one; run update interactively (and override at the goal prompt) to keep a hand-typed goal.

## Design guarantees

- **Session-local.** Scoping affects only the session it launches. Your Claude configuration is
  never modified. (claude-loadout does write two of its own files under your control: authored rules in
  `loadout/rules.toml`, and a remembered goal in `./.loadout/goal`.)
- **Fail-open, always.** A missing config, malformed rules file, unavailable model, or any other
  error degrades to launching the full, unscoped Claude Code, with a warning where it helps. The
  child process's exit code is passed straight back. claude-loadout can slim a session down; it can
  never stop one from starting.

## Requirements

- Python ≥ 3.11 (runtime deps `model2vec` and `numpy` install automatically)
- Claude Code
- Optional, for natural-language rule authoring: the `ccloadout[rules]` extra
  (`llama-cpp-python`) plus a local GGUF instruct model
- The uninstall script is bash (the tool itself is platform-independent)

## Uninstalling

`scripts/uninstall.sh` removes the package (pipx, falling back to pip) and asks whether to
delete the config it wrote: the profile config (`$CLAUDE_CONFIG_DIR/loadout/`) and every
per-repo `.loadout/` it created. For the latter it scans a root you choose (defaulting to
`$HOME`; type `/` to sweep the whole filesystem), lists every claude-loadout-authored `.loadout/`
directory found, and removes them only after a single confirmation. A directory is treated as
claude-loadout's only when it carries an artifact claude-loadout wrote (`goal`, `config.toml`,
`rules.toml`, or a self-ignore `.gitignore`), so an unrelated `.loadout/` is left alone. It
never touches your shell rc, alias lines you added are listed for you to remove by hand.

## Releasing (maintainers)

Publishing to PyPI is automated. `.github/workflows/publish.yml` runs on every merge to `master`
and publishes only when `version` in `pyproject.toml` is not already on PyPI, so a release is just
a PR that bumps the version. Merges that leave the version unchanged are a no-op.

The workflow uses [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC), so
no API token is stored in the repo. Configure it once on PyPI: project `ccloadout` -> Manage ->
Publishing -> add a GitHub trusted publisher with owner `marcellobarile`, repository
`claude-loadout`, and workflow `publish.yml`.
