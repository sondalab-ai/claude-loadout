# claude-loadout — Session Memory & Recall (Design Spec)

> **What is this file.** Implementation contract for a second selection pillar in
> `claude-loadout`: today the launcher prunes *capabilities* (MCP servers, plugins, skills);
> this spec adds ranked selection of *knowledge* (memories, decisions, technical-debt entries)
> so that past experience reaches a session without being poured into it wholesale.
> **Audience:** the implementing engineer, and anyone reviewing whether this belongs in the
> product at all. **Owner:** marcello.barile.
> **Companion files:** builds on `docs/specs/2026-08-18-smartctx-launcher-design.md` (inventory,
> goal detection, ranking, compose) and `docs/specs/2026-08-21-skill-scoping-design.md`
> (the `--settings` overlay this spec reuses as its injection point). Closes the open TODO item
> in `TODO.md`. This file is the contract; trade-offs and rejected alternatives are inline.

Date: 2026-09-04 · Status: **Proposed** · Target harness: Claude Code 2.1.261

**Status legend** (used throughout): `Proposed` — this document only, no code exists. `PoC implemented
(not delivered)` — code exists but is not shipped. `Delivered` — merged and available to users.
This spec is `Proposed` in its entirety.

---

## 1. Problem

The originating request (`TODO.md`):

> Analyse a way to give the agent a persistent "memory state" that reshapes itself based on its
> past experience […] this knowledge should be somehow accessible while working no matter what the
> session is about — it should not pollute the context while being useful when needed.
> Goals: avoid context drift, error propagation, no debt-awareness.

Stripped of the brain metaphor, the request states three testable goals:

| Goal | Failure it prevents |
|---|---|
| **G1 — no context drift** | The agent re-litigates decisions already made, or contradicts an established convention. |
| **G2 — no error propagation** | The agent re-introduces a bug or dead end that was already diagnosed and fixed. |
| **G3 — debt awareness** | The agent forgets that a shim, a skipped test, or a PoC stub it left behind is still open. |

The metaphor ("mimic human memory") is deliberately **not** a requirement: consolidation, decay and
associative recall are design tools here, not acceptance criteria. The three goals above are.

The hard part is not storage — four stores already exist (§2). It is that every existing mechanism
recalls **in bulk at session start**, which is exactly the context pollution the request rules out.

## 2. What already exists, and who owns it

| System | Provenance | What it does | What it does not do |
|---|---|---|---|
| Native auto-memory (`<config>/projects/<slug>/memory/*.md` + `MEMORY.md` index) | **Claude Code harness feature** | One fact per file. Frontmatter is `name` / `description` plus a **nested** `metadata:` block whose keys are the harness's own — `node_type: memory` is the kind discriminator, `type` carries a *different* vocabulary (`project`, `feedback`, …), alongside `originSessionId` / `modified`. The index is injected every session | No selection: the whole index is resident regardless of the task |
| `remember` plugin (`.remember/` per repo) | **Third party** — `Digital-Process-Tools/claude-remember` v0.21.0, distributed via the official Anthropic marketplace | Session narrative capture, background consolidation, rotation; `SessionStart` injects `now.md` / `recent.md` / `archive.md` / `core-memories.md` | No task-aware recall. Its `UserPromptSubmit` hook injects **only a timestamp** (`scripts/user-prompt-hook.sh` header; README hook table row) |
| `debug-decisions` skill | **Ours** (`~/.claude/skills/debug-decisions`) | Versioned architectural decisions with revert plans and an `INDEX` | Decisions are pulled by explicit command, never ranked into a session |
| `memory-org` skill | **Ours** (`~/.claude/skills/memory-org`) | Placement convention: repo memories in `docs/memory/` symlinked into `~/.claude/projects/<slug>/memory/` | A convention only — no store, no retrieval, no lifecycle |

**Neither of ours is a prerequisite.** `cld decision` and `cld debt` are subcommands of the pip
package: absorbing those skills *removes* an installation dependency rather than creating one, and
the store reader needs only directories, never the skills that conventionally populate them.

**Consequence for "ship as one product":** two of the four are ours and can be absorbed into
`claude-loadout` (`debug-decisions`, `memory-org`). The other two cannot and must not be
re-implemented — native auto-memory is a harness feature whose file format we **follow**, and
`remember` is third-party code we **interoperate** with read-only, if at all (§6).

## 3. Positioning — the launcher selects knowledge, it does not own it

`claude-loadout` already answers "which capabilities does this session need?" with an embedding
ranker over items carrying a `name` and a `description`. Memories carry exactly those two fields.
The product claim is therefore narrow and defensible:

> Storage is solved. **Ranked, budgeted recall is not.** `claude-loadout` selects which knowledge
> enters a session, with the same machinery, the same threshold, and the same savings accounting it
> already applies to skills, plugins and MCP servers.

One caveat the design has to carry rather than hide: memories supply `name` and `description`, but
those descriptions are written for humans and are often terse (`description: Never use` occurs in a
real store). Whether cosine scores separate well enough to make `threshold` a useful knob for this
item kind — as opposed to the budget doing all the work — is an open question that acceptance
criterion 3 exists to answer.

## 4. Verified levers (probed 2026-09-04 on Claude Code 2.1.261)

Every row below was checked empirically, in the house style of
`docs/specs/2026-08-21-skill-scoping-design.md §2`. Rows E–G are constraints, not capabilities.

| # | Observation | Evidence |
|---|---|---|
| A | **Hooks declared in the tmp `--settings` overlay are honored per-session, and they *merge* with — never shadow — hooks from the other sources.** `SessionStart` and `UserPromptSubmit` entries in a `--settings` JSON file both fired. With an overlay `SessionStart` hook active, a project-level `.claude/settings.json` hook and an installed plugin's hook (`remember`, which bootstraps `.remember/`) still fired in the same run. | Three `claude -p` probes in a throwaway project dir: overlay-only (markers written); overlay + project settings + plugin (all three side effects observed); control without overlay (plugin side effect present, confirming the comparison is meaningful). |
| B | **`UserPromptSubmit` hook stdout reaches the model's context.** | Probe hook printed `Context note: the hook codeword is BETA-7731`; the model quoted it back. |
| C | **`--append-system-prompt` reaches the model**, and the file-backed `--append-system-prompt-file` is accepted and equally effective. | Two probes: system codeword `ALPHA-4412` (inline) and `GAMMA-9080` (file) both quoted back. **Caveat:** `--append-system-prompt-file` has no row of its own in `claude --help` (it appears only inside another flag's description), so it is undocumented-but-working — §11 records the fallback. |
| D | **`Item` matches memory files at the top level only, and the current parser cannot read the rest.** `Item(id, kind, name, description)` (`src/ccloadout/inventory.py:8`) lines up with `name:` / `description:`, and `Ranker.rank` embeds `f"{name}. {description}"` (`src/ccloadout/ranker.py:31`) — but `_frontmatter` **skips every indented line** (`inventory.py:35`), so a nested `metadata:` block parses to the empty string and every lifecycle key inside it is silently dropped. Inline lists (`tags: [a, b]`) arrive as raw strings. `debug-decisions` files are further off: `id` / `date` / `project` / `status` / `git_sha` / `tags`, with the human title in a `#` heading and no `description` at all. | Source read, then executed against a real native memory file and a real decision file. **Consequence:** Slice 1 must extend `_frontmatter` to one level of nesting plus inline lists — a stated code change, not a shape that already fits (§5.1). |
| E | **The launch-time goal signal describes the repo, not the task.** `detect_goal` (`src/ccloadout/goal.py:115`) derives the goal from `README` / `pyproject.description`. | Source read. Adequate for capability pruning; weak for G2, where the useful memory is about the bug you are *about* to hit. |
| F | **The launcher does *not* exec — the parent survives the whole session.** `cli.py:1106` runs `subprocess.run(plan.argv, env=plan.env)` inside a `try/finally` that cleans up the tmp files; `grep -rn 'os.exec'` over `src/` matches nothing. | Source read; grep. **Consequence:** end-of-session capture is available in the parent for free, so §5.3 needs a hook only for mid-session signals. |
| H | **The harness injects its `MEMORY.md` index lines and nothing else.** A memory file that is not listed in the index reaches no session; a listed one contributes its title and hook, never its body. So entries under `<config_root>/projects/<slug>/memory/` that the index names are **already resident** before this subsystem adds anything. | Two `claude -p` probes in a throwaway project (CC 2.1.263): an unlisted file's codeword came back `NONE`; after adding its index line the model quoted the line verbatim but still answered `CODEWORD-NOT-SEEN`. **Consequence:** T1 excludes indexed entries (§5.2), or it pays twice for what the session already has — on the author's own repo that was 3 of 7 entries. |
| G | **`UserPromptSubmit` can block a turn, but only on exit 2.** Probed directly (CC 2.1.263): a hook exiting **1** lets the turn through unchanged; a hook exiting **2** blocks it with `UserPromptSubmit operation blocked by hook … Original prompt: …`, so the text is shown back rather than lost. | Our own probe for the exit codes; the latency numbers are a third-party report, and weaker than they first look: the p50 of 8718 ms and 6 timeouts in 256 runs come from an **external reporter**, not the `remember` maintainers, were measured on Windows 11 ARM64 under QEMU, were caused by 19–27 process spawns per prompt, and have since been fixed upstream (`scripts/user-prompt-hook.sh` header, https://github.com/Digital-Process-Tools/claude-remember/issues/227). The *erase-the-prompt* failure mode is real and platform-independent; the latency numbers are an upper bound from a pathological setup. Both still bind §5.2's requirements. |

## 5. Architecture

### 5.1 Store

Three item kinds, one file each. Entries we write follow the native auto-memory layout so the two
stores stay mutually readable — but "readable without migration" is a claim about *layout*, not
about the current parser: `_frontmatter` drops nested keys (lever D), so **Slice 1 extends it** to
one level of nesting plus inline lists. Without that change nothing below the `metadata:` line is
visible.

```yaml
---
name: <kebab-case-slug>
description: <one line — this is what the ranker embeds>
metadata:
  node_type: memory              # the harness's own discriminator; preserved, never repurposed
  loadout_kind: memory | decision | debt   # ours, namespaced to avoid colliding with `type`
  scope: global | repo
  created: YYYY-MM-DD
  anchors: [<path>, <path#symbol>]   # optional; drives staleness checks (§5.6)
  content_sha: <sha1 of the anchored region at write time>   # optional, with anchors
  status: open | resolved        # loadout_kind: debt only
---
```

`metadata.type` is **not** ours to define: the harness already uses it for its own vocabulary
(`project`, `feedback`, …) alongside `node_type`. Our kind lives in `loadout_kind`. Usage counters
are deliberately *absent* from the file — they live in a sidecar (§5.5).

Locations, in precedence order: `<repo>/docs/memory/` (git-tracked, the `memory-org` convention
absorbed), then `<config_root>/projects/<slug>/memory/` (the harness's own directory).

**Cross-project reach is a property, not a place.** An earlier draft put shared entries in
`<config_root>/loadout/memory/`; that was wrong on two counts. It was unreachable — nothing ever
wrote there — and it would have made uninstalling this tool strand notes in a directory no other
tool reads. Instead an entry carries `metadata.scope: global` and stays in whichever canonical store
it was born in; the reader collects globals from every project's directory. `[memory] scopes`
selects which halves a session sees, and is the knob that makes this configurable rather than
absolute. Where `memory-org` has been applied the
first two are the same files through a symlink, so the reader deduplicates by resolved real path;
where it has not — the common case, including this repo — they are two distinct stores, and turning
on `git_tracked` creates a second one rather than linking the first. When two distinct files share a
`name:` slug, the earlier location wins and the shadowed entry is reported by `cld doctor`; slugs
are never silently merged. A repo may opt out of the git-tracked location for shared monorepos
(`[memory] git_tracked = false`) — see §11: that flag is a trust decision as much as a storage one.

`decision` entries carry the `debug-decisions` payload (context, choice, revert plan) in the body;
absorbing that skill means its `/decision*` commands become `cld decision *` subcommands writing
this same shape.

**Reading the existing decision corpus.** Absorption is not a migration: the store reader must read
the corpus **in place**, non-destructively — 182 decision files across 27 project directories exist
today, 5 of those directories being symlinks into repositories created by `/decision-link`. Two
paths must be read, not one: `<config_root>/debug-decisions/<slug>/` for consistency with every
other location, **and** `~/.claude/debug-decisions/<slug>/`, which the skill hardcodes
(`debug-decisions/SKILL.md:22`). With `CLAUDE_CONFIG_DIR=~/.claude-perso` — a live configuration —
reading only the config-root path finds zero of the 182 files.
Their frontmatter carries no `name` and no `description`, so the reader maps:

| `Item` field | Source in a decision file |
|---|---|
| `name` | the `id` frontmatter key (already a dated slug) |
| `description` | the body's first `#` heading — the human title the ranker needs |
| `id` | `decision:<project-slug>/<id>` |

`status: active \| superseded` maps onto the entry lifecycle; `tags`, `git_sha` and `project` are
preserved as-is. Nothing is rewritten: if the user later uninstalls the skill, the files stay
readable, and if they keep it, both tools see the same directory.

### 5.2 Recall — three tiers, because one tier cannot satisfy the request

"Available whatever the session is about" and "does not pollute the context" are jointly
unsatisfiable by any single injection point. The design splits them:

| Tier | When | Mechanism | Cost | Ships in |
|---|---|---|---|---|
| **T1 — resident index** | Launch | Ranked one-line entries, taken in rank order until the budget is spent (§5.4), written to a tmp file and passed via `--append-system-prompt-file` (lever C), alongside the existing tmp `--mcp-config` / `--settings` | Bounded by an explicit token budget (§5.4) | Slice 1 |
| **T2 — on-demand retrieval** | Mid-session, agent-initiated | `cld recall "<query>"` — full store search, returning bodies. Taught to the agent by the T1 payload itself (§5.2.1), not by an installed skill | Zero until invoked | Slice 1 |
| **T1.5 — prompt-aware re-rank** | On each user prompt | `UserPromptSubmit` hook (levers A+B) re-ranks the store against the actual prompt text, injecting 0–2 entries | Small, but paid on every prompt | Slice 3, **default off** |

T1 answers "no matter what the session is about" at repo granularity; T2 answers "useful when
needed" at zero resident cost. Both leave the lever-E gap partly open, and the spec should not
pretend otherwise: **T1.5 is the only tier that closes it automatically**, and it is Slice 3,
default off, and droppable. In Slices 1–2, G2 is served by T2 — which depends on the agent choosing
to call it — and by staleness demotion (§5.6), which prevents a wrong memory from being served but
does not surface a right one. If T1.5 is ultimately dropped, G2 ships partially served; that is a
stated outcome, not an oversight to be discovered later.

**Never duplicate the harness.** Entries the harness already injects through its own `MEMORY.md`
index (lever H) are excluded from T1 with the reason `already-in-context`, and the freed budget goes
to entries the session would not otherwise have. They stay reachable through T2. The same rule
governs T1.5, which is told what T1 made resident.

**5.2.1 — T2 must work with nothing installed.** The store is reached through the package's own
executable, so the subsystem must not depend on a skill, plugin or MCP server being present. An
earlier draft exposed `cld recall` as a bundled skill; that is wrong, because a skill has to be
installed into `$CLAUDE_CONFIG_DIR/skills/` and a fresh `pip install ccloadout` installs none.
Instead the T1 payload — already resident, already budgeted — carries a short usage contract naming
the store and the command. Two requirements:

0. The payload teaches **two** commands, not one: `recall` to read, and `memory add` to write.
   Without the second, the only party that knows what a session learned — the session — has no way
   to say so, and every note has to come from the user by hand. The invitation is deliberately
   narrow (a root cause, a dead end, a decision made with the user; not routine progress), and what
   it produces is an ordinary entry: rankable, budgeted, flaggable and deletable in `cld memory
   audit`. Nothing an agent writes reaches a later session unranked.
1. The injected text names the **absolute resolved path** of the running entry point (available to
   `compose` at launch), not the bare name `cld`. A bare name that is not on the launched session's
   `PATH` fails silently, and the agent cannot tell that from an empty store.
2. Cost is a handful of lines, charged against `budget_tokens` like any other resident text.

An optional `cld`-installed skill may ship later as **discoverability sugar for the human** (it buys
a slash command); it buys the agent nothing and is never a prerequisite. See §11 for the
self-reference it introduces.

**T1.5 hard requirements** (contract, not guidance — all of them, or it does not ship):

1. One process, no shell chain.
2. No model load in the hot path — measured at ~520 ms for `potion-base-8M`, which alone exceeds
   the timeout in requirement 3. The hot path therefore uses the dependency-free `keyword_embed`
   (`src/ccloadout/ranker.py:62`), and **the on-disk index must be built with that same embedder**.
   Precomputing `model2vec` vectors and comparing them against `keyword_embed` query vectors would
   not raise: both spaces are 256-dimensional, so `_cosine` returns plausible noise and the failure
   is invisible in review and in tests. One embedder per index, recorded in the index header and
   checked on read.

   > **Measured, and resolved by a fourth option (Slice 3).** `keyword_embed` does not rank memories
   > usably: on a fixture of 3 on-topic and 6 off-topic entries it puts an off-topic entry above two
   > on-topic ones, while `potion-base-8M` separates them cleanly (0.32–0.48 against ≤0.17). Loading
   > that model costs ~520 ms, past this timeout. Rather than choose between a warm process,
   > keyword-grade recall and dropping the tier, Slice 3 scores the hot path **lexically** — BM25
   > with inverse document frequency and crude suffix stripping (`src/ccloadout/lexical.py`), no
   > model and no numpy. Why it works where the crc32 fallback does not: hashing collides and
   > weights every token alike, while IDF ignores words the whole store shares and stemming lets a
   > prompt's "skills pruned" meet an entry's "skill pruning". Measured end to end, hook process
   > included: **38 ms median, 56 ms worst of seven**, against a 300 ms budget.
3. A hard wall-clock timeout (default 300 ms), enforced inside the hook with `setitimer`.
4. `exit 0` on **every** path — timeout, missing index, import error, corrupt store. The binding
   rule is *never exit 2*, which is the only code that blocks a turn (lever G); exiting 0
   unconditionally satisfies it without having to reason about which failures are recoverable. An
   uncaught Python exception is exit 1, which does not block — so the discipline buys silence, not
   safety, and the earlier claim that it prevented prompt loss was wrong.
5. Off by default, behind `[memory] prompt_recall = false`.
6. The hook is told which entries the launch payload already made resident and never repeats one,
   so the two tiers add context instead of duplicating it.

### 5.3 Write path

The launcher does **not** exec: `cli.py:1106` runs the session under `subprocess.run` and regains
control when it exits (lever F). That splits capture in two, and only one half needs a hook:

- **End of session — in the parent, no hook.** After `subprocess.run` returns, the launcher already
  holds the goal, the ranked plan, the exit code and the wall time, and can diff the repository
  itself. The session envelope is appended to a candidates file there: no `exit 0` discipline, no
  hook-merge assumption, no cost inside the session. An earlier draft put this in a `Stop` hook on
  the strength of a false premise; the hook is removed.
- **Mid-session — `PostToolUse` hook (lever A).** Only **debt signals** matched by explicit patterns
  the user configures (`[memory] debt_patterns`, default `TODO(loadout)`). These are invisible from
  outside the session, so this is the one thing a hook buys. No inference, no model call: a pattern
  matched or it did not. The hook command is written as an absolute resolved path, for the reason
  given in §5.2.1.

  > **A tool-name filter is not enough (measured live).** Restricting to `Write`/`Edit` misses the
  > common case: asked to create a file, Claude Code reached for `Bash` and a `printf … > file`
  > redirect, and the hook saw nothing. `Bash` is therefore included, but only when the command
  > actually writes — it carries a redirect, a heredoc, `tee`, `sed -i` or `patch`. `grep
  > "TODO(loadout)"` records nothing, because searching for a marker is not creating one. This is
  > mechanical, not inferential: the rule is "the command writes **and** contains the pattern".

  A signal becomes a *candidate*, never a ledger entry. `cld memory consolidate` shows it and the
  user turns it into open debt, anchored to the file, or discards it.

`debug-decisions` registers a `Stop` hook of its own (a retrospective nudge). Hooks merge rather
than shadow (lever A), so it is unaffected either way; absorption removes the duplication by making
the skill unnecessary, never by disabling it behind the user's back.

Candidates are never memories. Promotion is a separate, out-of-band step —
`cld memory consolidate` — which deduplicates, merges near-duplicates, and asks for confirmation on
anything it would create. Running a model in a hook is explicitly rejected (§11). The candidates
file is capped (`[memory] candidates_max_bytes`, default 256 KB) and rotates rather than growing
without bound: a user who never consolidates must not accumulate forever.

`--bare` is a passthrough flag that disables hooks, plugins and auto-memory harness-side, and `cld`
forwards unknown flags verbatim (`cli.py:1081`). Under `cld --bare` the `PostToolUse` half is
therefore inert. The launcher detects the flag and says so once, rather than reporting a capture
path that is not running.

### 5.4 Budget and accounting

Recall spends the tokens that pruning saves, so the two must be reported together — but the current
`savings.py` cannot express that, and this is a code change, not a configuration one:

- `PRUNABLE = frozenset(DEFAULT_TOKEN_COSTS)` (`savings.py:20`), so simply adding a `memory` cost
  would make memories *prunable* and count **un-injected** entries as savings — the sign inverted.
- `estimate_savings` (`savings.py:60`) sums only dropped items and `Savings` has no field for
  injected tokens.

So: `Savings` gains an `injected` field and a `net` accessor, `memory` is registered as a cost kind
**outside** `PRUNABLE`, and the reported figure is `net = eager_saved − injected`. Deferred MCP
savings stay reported separately, because `savings.py:22-26` already documents them as freeing
"~nothing up front" — folding them into a net delta would let hypothetical savings mask a real
cost. The estimator is the existing ~4 characters/token heuristic from `measure.py:11`, named as a
heuristic wherever the number is printed; `cld measure` remains the only real measurement.

`[memory] budget_tokens` (default 800) caps T1. **One admission rule, and only one:** entries are
taken in rank order until the budget is exhausted — there is no top-K. Pinned entries (§5.5) are
admitted first but may never occupy more than half the budget, so promotion cannot starve ranked
recall.

**Empty or near-empty store.** Below `[memory] min_entries` (default 1) T1 emits nothing at all —
not even the usage contract of §5.2.1. Injecting an instruction to query an empty store spends
tokens to prime a call that returns nothing. Above it, the payload always states the entry count,
so a recall miss is distinguishable from a broken command.

### 5.5 Reshaping (the "past experience" clause, made measurable)

Usage counters live in a **sidecar**, `<config_root>/loadout/usage.json`, never in the entry files.
Writing `uses`/`last_used` back into `<repo>/docs/memory/*.md` would dirty git-tracked files on
every launch: `git status` noise, counters committed by accident, and a merge conflict per
collaborator. The sidecar is written with a lock file and an atomic replace, because two `cld`
sessions in the same repository are ordinary, and the candidates file (§5.3) is appended under the
same discipline.

A counter is incremented only when an entry actually reached a launched session: `--explain` returns
before launching (`cli.py:1099-1103`) and the launch gate lets the user drop entries after ranking,
so neither path counts. That keeps the signal meaning "was delivered", not "was ranked".

Three behaviors follow, none needing a metaphor:

- **Promotion** — an entry delivered repeatedly in a repository is pinned into T1 for that
  repository, within the half-budget cap of §5.4.
- **Decay** — after `decay_days` (default 90) with no delivery, an entry's rank score is multiplied
  by `[memory] decay_factor` (default 0.5) for admission purposes only; `cld memory prune` then
  proposes it for deletion. Nothing is ever deleted automatically, and the stored file is unchanged.
- **Reporting** — `cld doctor` shows which memories are earning their tokens and which are not.

### 5.6 Staleness

An entry may carry `anchors` and a `content_sha` taken at write time. At recall the reader checks
both: a missing path demotes the entry, and a changed `content_sha` flags it as *possibly stale*
without demoting it. Without the stored hash there is nothing to compare against — an anchor that
still resolves tells you nothing, which is why §5.1 stores it.

This is a **partial** defense against G2, and it is worth being exact about the limit: it catches
memories whose anchor moved or was rewritten, and it does not catch a memory that is simply wrong
about code nobody touched. Symbol-level anchors (`<path>#<symbol>`) need per-language resolution;
Slice 2 ships path-level anchors only, and symbol anchors are deferred rather than assumed.

## 6. Scope and non-goals

**In scope:** the store (§5.1), T1 and T2 recall, the budget accounting, staleness checks, the
debt ledger, and absorption of `debug-decisions` + `memory-org` into the CLI.

**Non-goals, explicitly:**

- **Do not re-implement `remember`.** It is third-party and actively maintained. If ingestion is
  ever wanted, it is a read-only adapter over `.remember/*.md`, and it is not in this spec.
- **Do not own the native memory format.** We read and write the harness's shape; if it changes,
  we follow.
- **No vector database, no server, no background daemon.** The ranker plus a flat file index is the
  whole retrieval stack.
- **Claude Code only.** Portability to other harnesses is deliberately out of scope. A `Harness`
  Protocol does exist (`src/ccloadout/harness.py`, recorded in `2026-08-18 §4.6`) but nothing
  imports it; this subsystem uses the §4 levers directly rather than routing through it, which
  leaves that seam exactly as dormant as it already was.
- **No installation prerequisites.** The subsystem requires no skill, plugin or MCP server to be
  installed — only the `ccloadout` package itself (§5.2.1).
- **No model call in any hook.**
- **No automatic deletion or automatic debt closure** (§11).

## 7. Features mapped to goals

Most of the contract traces to a stated goal; the rest is packaging, and is labelled as such rather
than given a goal it does not serve.

| Feature | Goal | Slice |
|---|---|---|
| Ranked T1 index via `--append-system-prompt-file` | G1 | 1 |
| `cld recall`, taught by the T1 payload (§5.2.1) | G1, G2 | 1 |
| Token budget + net savings accounting | (the "no pollution" constraint) | 1 |
| Staleness / anchor verification | G2 | 2 |
| Debt ledger with explicit lifecycle | G3 | 2 |
| `PostToolUse` capture of configured debt markers | G3 | 2 |
| The payload invites the session to record what it learned | G1, G2 | 1 |
| Usage counters, promotion, decay | ("reshapes itself", made measurable) | 2 |
| `decision` kind absorbed from `debug-decisions` | G1 | 2 |
| Prompt-aware re-rank hook (default off) | G2 | 3 |
| `memory-org` absorption + `git_tracked` | no goal — packaging | 2 |
| `cld memory add\|list\|show\|prune\|consolidate` | no goal — store maintenance the other rows presuppose | 1–2 |
| `cld doctor` store health | no goal — operability | 1 |
| `cld decision supersede` | G1 (a superseded decision must stop being recalled) | 2 |

**Link expansion (§5.2.2).** After admission, each admitted entry pulls in the entries it names —
`metadata.links` or `[[wikilink]]` in its body — when they fit the remaining budget, marked in the
payload as *linked to X*. Depth is one, and links are followed only from entries admitted on their
own score: a rejected entry cannot smuggle its neighbours in, and a second hop would fill the budget
with cousins. It answers the case ranking cannot: two notes that are each incomplete alone.

**Deferred — not in this contract:** deeper link traversal; similarity-derived edges (that is the
ranking again, under another name);
read-only ingestion adapters for `.remember/`; export/import; a `cld memory` terminal user interface
(TUI); symbol-level anchors (§5.6). Each is defensible later; none is required by G1–G3.

**`cld decision revert` is deliberately not absorbed.** It executes destructive git operations, it
traces to no goal here, and putting it in a launcher widens the blast radius of a tool whose job is
to compose an argv. The skill keeps it; if the skill is uninstalled, that capability is lost, and
that is a stated cost rather than a silent one.

## 8. CLI surface

```
cld recall <query>              # T2: search the store, print matching bodies
cld memory add|list|show|prune  # store maintenance
cld memory consolidate          # promote candidates -> entries, dedupe, confirm
cld decision new|list|show|supersede           # absorbed from debug-decisions (no `revert` — §7)
                                # /decision-link is NOT replaced by [memory] git_tracked: that
                                # flag chooses where new entries are written, while the skill
                                # command symlinks and migrates an existing corpus. Dropping it
                                # loses that migration; the reader reads both paths instead (§5.1).
cld debt add|list|resolve       # the debt ledger (G3)
cld doctor                      # extended: store health, budget use, stale entries
```

## 9. Configuration

```toml
[memory]
enabled = false          # opt-in: the existing product is unchanged for users who skip this
budget_tokens = 800      # cap on the T1 resident index
threshold = 0.24         # a separate knob, not an override of the capability threshold. It starts
                         # at the ranker default (config.py:8), which was calibrated on
                         # skill/plugin descriptions. Measured on memory-shaped fixtures it still
                         # separates: on-topic 0.32-0.48, off-topic <= 0.17, so 0.24 admits the
                         # former and rejects the latter. Re-centre if real stores disagree.
scopes = ["repo", "global"]   # "repo" covers BOTH repo-local locations of §5.1 (docs/memory and
                              # <config_root>/projects/<slug>/memory); "global" is
                              # <config_root>/loadout/memory
git_tracked = true       # false for shared monorepos where Claude artifacts must not be committed.
                         # Also a trust setting: a git-tracked store accepts entries from anyone
                         # who can merge a PR (§11)
prompt_recall = false    # T1.5, Slice 3
min_entries = 1          # below this, T1 injects nothing at all (§5.4)
decay_days = 90
decay_factor = 0.5       # rank multiplier applied to an entry past decay_days (§5.5)
candidates_max_bytes = 262144   # candidates file rotates rather than growing forever (§5.3)
```

`enabled = false` by default is the load-bearing line: `claude-loadout` remains exactly what it is
today for anyone who wants capability pruning and nothing else.

## 10. Delivery slices

- **Slice 1 — recall that pays for itself.** The `_frontmatter` extension (nested keys + inline
  lists) that everything else depends on, the store reader over the three memory locations, a
  `memory` kind in the inventory and ranker, T1 injection carrying the §5.2.1 usage contract,
  `cld recall`, and the `Savings.injected` / `net` change. Reviewable end-to-end: a session launches
  with N relevant memories resident and the net token delta is reported with both terms visible.
- **Slice 2 — knowledge that maintains itself.** Path-level anchors and staleness, the usage
  sidecar with promotion and decay, `cld memory consolidate`, the debt ledger with its
  `PostToolUse` capture, and `debug-decisions` absorption — a reader over both corpus paths (§5.1),
  never a conversion.
- **Slice 3 — prompt-aware recall.** The `UserPromptSubmit` hook under the §5.2 contract, default
  off, shipped only if it holds its latency budget under measurement.

## 11. Risks and rejected alternatives

| Risk / alternative | Decision |
|---|---|
| Injecting the whole store at `SessionStart` (what every existing system does) | **Rejected** — it is the pollution the request exists to avoid. |
| Auto-resolving a debt entry when its file changes | **Rejected.** An unrelated edit would silently close real debt — precisely the G3 failure. File change raises a *staleness prompt*; only `cld debt resolve` closes an entry. |
| Automatic memory deletion by decay | **Rejected.** Decay demotes and proposes; the user deletes. |
| Running a summarizer model in a `Stop` or `UserPromptSubmit` hook | **Rejected** (lever G). Consolidation is out-of-band and explicit. |
| T1.5 latency regression on every prompt | Mitigated by the §5.2 contract; shipped default-off; dropped entirely if it cannot hold 300 ms. |
| Recall spends more context than pruning saves | Made visible rather than assumed — net accounting is an acceptance criterion (§12). |
| The injected usage contract is advisory: probes prove the text **arrives**, not that the model acts on it | Accepted, and bounded by design: T1's ranked index is useful standalone, so an ignored instruction degrades to *less recall*, never to *no memory*. This is also why the fix is not an MCP tool — that would buy reliability at the cost of resident tool schemas, which is what the product exists to avoid. |
| The optional discoverability skill is itself prunable — installed under `$CLAUDE_CONFIG_DIR/skills/`, the ranker can drop it via `skillOverrides` | A default `always_keep` entry does **not** work: `config.py:66-68` replaces the tuple per layer instead of merging, so any user who sets their own `always_keep` would un-protect it. If the skill ever ships it must be force-kept in code, outside the user-editable list. A recall skill pruned by the recall system is the kind of self-reference only ever found in production. |
| **Prompt injection through the store.** T1 puts entry text into the system prompt — the highest-trust position in the context — and with `git_tracked = true` the first store location is `<repo>/docs/memory/`. An entry arriving via `git pull` or a merged PR becomes system-prompt text in every collaborator's next session; the `decision` reader widens this, since `description` is taken from a file's first `#` heading. §5.3's debt capture closes the loop from repo content to candidates | **Mitigated, not solved.** (a) Entries are injected inside a delimited block labelled as untrusted reference material with its provenance (path and scope) on each entry; (b) only `scope: global` and config-root entries are eligible for the *unlabelled* portion of the payload; (c) `git_tracked = true` is documented as a trust decision, not merely a storage one; (d) consolidation never promotes a candidate without confirmation. A user who commits a hostile memory to their own repo is inside their own trust boundary; a user who merges someone else's is warned by the label. |
| Concurrent sessions racing on store writes | Counters live in a sidecar under `<config_root>/loadout/`, written with a lock and an atomic replace; the candidates file is appended under the same discipline (§5.5). Entry files themselves are never written by a launch. |
| Store shape drifts from the harness's native memory format | We follow, never fork. A format change is a reader change, not a migration. |
| `--append-system-prompt-file` is undocumented in `--help` and could change | The fallback is the documented inline `--append-system-prompt`, but "detect at launch" needs a stated method, since grepping `--help` for the flag finds nothing. Detection is a one-shot `claude -p` probe run by `cld doctor` and cached per harness version; a cache miss uses the inline form. Inline is also bounded by `ARG_MAX` and visible in `ps`, which is a second reason `budget_tokens` stays small. |

## 12. Acceptance criteria

Each is a test that a broken implementation fails. Criteria 3 and 4 are the only ones that measure
recall *quality*; the rest are mechanism-level, and a spec whose criteria are all mechanism-level
cannot tell a working ranker from a random one.

**Slice 1**

1. With `[memory] enabled = false`, `compose` produces a byte-identical `LaunchPlan` (argv, settings
   overlay, env) to the same call before this subsystem existed — asserted on the composed bytes,
   not inferred from the suite still passing.
2. On a fixture store, the T1 payload's estimated size never exceeds `budget_tokens` under the
   `measure.py:11` heuristic, and `cld --explain` prints both terms of `net = eager_saved −
   injected` for that session, with deferred MCP savings reported separately.
3. **Ranking quality.** Given a fixture store of 3 on-topic and 20 off-topic entries and a fixture
   repo goal, every on-topic entry is admitted to T1 before any off-topic one. A ranker returning
   arbitrary order fails; the current threshold-only design would too, if scores do not separate.
4. `cld recall` returns an entry T1 excluded **on relevance** — below threshold against the repo
   goal, above it against the query — not one that merely fell outside the budget.
5. Below `min_entries` the T1 payload is empty: no index, and no usage contract either.
6. With an empty `$CLAUDE_CONFIG_DIR/skills/` and no plugins installed, the payload names a path
   that exists and is executable, and invoking that exact path against the fixture store returns
   entries.
7. The extended `_frontmatter` reads a real native auto-memory file (nested `metadata:` with
   `node_type`) and a real `debug-decisions` file (inline `tags: [a, b]`, no `description`) into
   populated `Item`s. Against today's parser this test fails.

**Slice 2**

8. An anchor whose path no longer exists demotes the entry; an anchor whose `content_sha` changed
   flags it as possibly stale **without** demoting it; an unchanged anchor does neither.
9. A debt entry survives an unrelated edit to its anchored file and is closed only by an explicit
   `cld debt resolve`.
10. Counters increment only for entries actually delivered to a launched session: `--explain` and
    entries dropped at the launch gate leave the sidecar unchanged.
11. Two concurrent launches in the same repository both record their increments — no lost update —
    and neither writes to any file under `<repo>/docs/memory/`.
12. The decision reader loads the corpus from both `<config_root>/debug-decisions/` and
    `~/.claude/debug-decisions/`, and rewrites nothing (file mtimes and bytes unchanged after a
    full launch).

**Slice 3, if it ships**

13. The `UserPromptSubmit` hook exits 0 under a forced timeout, a deleted index, and a corrupt
    store — verified by test, since the failure mode destroys user input.
14. An index whose header records a different embedder than the hot path is rejected on read rather
    than scored: the two 256-dimensional spaces would otherwise compare cleanly and return noise.
