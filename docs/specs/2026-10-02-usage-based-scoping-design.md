# claude-loadout — Usage-Based Skill Scoping (Design Spec)

> **What is this file.** Design contract for replacing the embedding-model keep/drop decision with
> rules plus each repository's real skill usage, and for extending scoping from user skills to
> bundled, org and repository skills through graded `skillOverrides` values.
> **Audience:** the implementing engineer, and anyone deciding whether the redesign is worth its
> cost. **Owner:** marcello.barile.
> **Companion files:** supersedes §3 (scope) of `docs/specs/2026-08-21-skill-scoping-design.md`,
> which limited scoping to `$CLAUDE_CONFIG_DIR/skills/*` and the value `"off"`. Builds on
> `docs/specs/2026-08-18-smartctx-launcher-design.md` (inventory, compose, rules). The evidence comes
> from a usage audit of real sessions (Sep 7 – Oct 2, 2026). This file is the contract; trade-offs
> and rejected alternatives are inline.

Date: 2026-10-02 · Status: **Proposed** · Target harness: Claude Code 2.1.287

**Status legend** (used throughout): `Proposed` — this document only, no code exists. `PoC implemented
(not delivered)` — code exists but is not shipped. `Delivered` — merged and available to users.
This spec is `Proposed` in its entirety.

---

## 1. Problem

The audit measured what cld actually removes from a session and how it decides.

| Finding | Evidence |
|---|---|
| **Small savings.** A camunda-hub session (work profile) loses about 2.1k tokens of a ~70k first prompt (≈3%). | 808 tokens of dropped user-skill descriptions plus 1,249 tokens of claude.ai connector tool names, measured from transcript `skill_listing` and `deferred_tools_delta` attachments; median first prompt 69,902 tokens. |
| **The ranker decides nothing.** In both repositories checked, every dropped item was dropped by a manual rule. | `cld --explain` lists `rule` as the reason for all 5 drops in camunda-hub and all 4 in claude-loadout. |
| **The scores don't separate relevant from irrelevant.** | camunda-hub: cv-builder 0.39 scores above superpowers 0.31 and code-review 0.28. claude-loadout (threshold 0.20): mind-gym 0.43, cv-builder 0.40, personal-trainer 0.33 and astro-visibility 0.32 all pass. Skills and plugins score in a narrow 0.16–0.54 band (`potion-base-8M` static embeddings). |
| **Most of the skill list is out of reach.** | perso session, 60 skills: 1 user, 23 plugin, 22 bundled or repository, 14 org. work session, 77 skills: 2 user, 28 plugin, 39 bundled or repository, 8 org. cld today touches user skills and whole plugins only. |
| **Real usage is narrow.** | Over a month, about 17 distinct skills per profile were invoked (subagents included, they added none). No `anthropic-skills:*` skill was invoked at all. |

A threshold change cannot fix an ordering problem, and a better embedding still has no signal for
"does this repository ever use this skill". Usage is that signal, and it is already in the
transcripts cld's Stop hook reads.

## 2. Verified levers

Probed on Claude Code 2.1.287 with `claude -p --model haiku --settings <overlay>` in a scratch
directory, reading the initial `skill_listing` attachment (`names`) from the resulting transcript.

| # | Observation | Evidence |
|---|---|---|
| L1 | `skillOverrides` accepts four values: `"on"` (default when absent), `"name-only"` (listed without description), `"user-invocable-only"` (hidden from the model, `/name` still works), `"off"` (hidden from both). | Settings schema text in the 2.1.287 binary. |
| L2 | `"off"` removes **user** (`mind-gym`), **bundled** (`dataviz`, `keybindings-help`), **org** (`anthropic-skills:pptx`) and **repository** (`.claude/skills/probe-repo-skill`) skills from the listing. | Listing went from 64 to 59 names; each target absent. |
| L3 | **Plugin skills cannot be overridden individually.** `superpowers:writing-skills` stayed listed under the keys `superpowers:writing-skills`, `writing-skills`, `superpowers@claude-plugins-official:writing-skills`; `superpowers:executing-plans` under `plugin:superpowers:executing-plans` and `superpowers:executing-plans@claude-plugins-official`. Plugins stay all-or-nothing through `enabledPlugins`. | Second probe run, both names still in `names`. |
| L4 | `"name-only"` reduces the listing entry to the bare name (about 5 tokens), and **the model can still invoke the skill**: asked to use it, Haiku called the Skill tool and the skill ran. | Listing entry `probe-repo-skill` with no description; `Skill` tool_use in the transcript. |
| L5 | `"user-invocable-only"` removes the skill from the listing, `/probe-repo-skill` still runs, and the model cannot invoke it on request. | Two probe runs, one typing the command, one asking the model. |
| L6 | `disableBundledSkills` (setting) and `CLAUDE_CODE_DISABLE_BUNDLED_SKILLS` (environment) turn off every bundled skill at once. Too coarse: `run`, `artifact-design`, `loop` and `claude-api` are in use. | Settings schema text in the 2.1.287 binary. |

Not yet probed: how an overlay `skillOverrides` merges with the user's own `skillOverrides` in
`settings.json`, and whether `"on"` in the overlay re-enables a skill the user turned off (§9).

## 3. Scope and non-goals

**In scope:** user, bundled, org and repository skills (every skill L2 reaches); a per-repository
usage record; the decision ladder in §4; `--explain` and `cld rules` output; a one-time backfill.

**Not in scope:**
- **Plugin skills one by one** (L3). Plugins keep today's all-or-nothing rules and pins. Usage feeds
  suggestions in `cld rules` (§4.3), never an automatic plugin drop: a plugin also carries hooks and
  agents, and dropping one by inference is too blunt.
- **MCP servers and claude.ai connectors.** Unchanged.
- **Memory recall.** Keeps using the embedding model; nothing here touches it.

## 4. Design

### 4.1 Decision ladder (per skill, first match wins)

| # | Condition | Value | Why |
|---|---|---|---|
| 1 | An explicit rule drops it (`always_drop`, or a conditional rule that drops) | `off` | The user said so. The only path to `off`. |
| 2 | Pinned (`always_keep`) or an explicit keep rule | `on` (emitted only if needed, §9) | The user said so. |
| 3 | A `slash_only` rule (new action) | `user-invocable-only` | For skills you type yourself (`init`, `security-review`, rare `hub-*` workflows): zero listing cost, `/name` keeps working. |
| 4 | Used in this repository within `usage_window_days` (default 30), by the model or by `/name` | `on` | It earns its listing. |
| 5 | Repository has fewer than `min_sessions` recorded (default 5) | `on` | Cold start: no evidence yet, change nothing. |
| 6 | Otherwise | `name-only` | Unused here. The model still sees the name and can still invoke it (L4). |

**Feedback-loop guard.** A demoted skill must stay reachable, or its usage stays at zero forever.
That is why usage alone never goes below `name-only`: if the model picks a `name-only` skill, the
use is recorded and the next launch restores it to `on`. Only an explicit rule produces `off` or
`user-invocable-only`.

### 4.2 Where it plugs in

- `compose` emits `skillOverrides: {<name>: <value>}` from an id → value map instead of a kept set
  (`compose.py` today hard-codes `"off"`).
- The keep/drop result (`Selection`, `kept`/`dropped`, the dropped reason) becomes a value per
  item. Every two-state point found during exploration has to follow: `_scoped_plan`,
  `_decide_keep` (init/update), `_materialize_rules`, `_reconcile_rules`, the launch-gate checkbox
  and savings.
- `Ranker` leaves the keep/drop path. `cld rules` shows its score per item as a hint while authoring.
- Savings: `name-only` saves the footprint minus the name; `user-invocable-only` and `off` save the
  whole footprint (`Item.footprint`, added in 0.11.0 alongside this spec).

### 4.3 Plugins

Unchanged mechanism. `cld rules` and `--explain` show, per plugin, when any of its skills, agents
or MCP tools was last used in this repository, so a never-used plugin is easy to spot and drop by
rule.

## 5. Usage recording

- **Source: the Stop hook**, which already reads the whole transcript (`stop_hook.parse_transcript`
  already collects `Skill` tool names). It also counts `/name` commands, which appear as
  `<command-name>/name</command-name>` in user messages and produce no `Skill` tool call.
- **Subagents:** the hook also reads `<transcript stem>/subagents/*.jsonl`. The audit found no
  subagent-only skill, but the cost is small and the record stays complete.
- **Idempotent:** Stop fires on every turn. The hook stores a per-session snapshot
  (`session_id → {skill: {model, slash}}`), overwritten on each run; readers aggregate snapshots. No
  double counting, no per-session marker needed. Snapshots older than 90 days are pruned on write.
- **Skill universe:** the initial `skill_listing` attachment in the transcript lists every skill
  the session saw. The hook stores its names per repository with a last-seen date. That is how cld
  learns bundled, org and repository skill names without reading the Claude Code binary.
- **File:** `<config_root>/loadout/skill_usage.json`, keyed by `repo_root()` (the main checkout, so
  worktrees aggregate), written with `jsonstore.update_json` (lock plus atomic replace). It is kept
  separate from `usage.json`, whose memory-delivery counters feed `promoted_ids`.
- **Gate:** its own setting, `[scoping] record_usage` (default on). It does not depend on `[memory]`.
- **Backfill:** `cld usage backfill` scans the profile's existing transcripts once (interactive
  sessions only, `entrypoint: cli`), so existing repositories start with a month of evidence.

## 6. What the user sees

- `--explain` groups skills by value, each with its reason (`rule`, `pinned`, `used 3 days ago`,
  `cold start`, `unused for 30 days`), and shows savings from footprints.
- `cld usage` prints the record for the current repository: skills by last use, and plugins with
  no use in the window.

## 7. How much it can save

Measured on the latest session of each profile, against the month of usage above:

| Profile | Skill listing | Unused non-plugin skills | Saved with `name-only` | Plus connectors | Share of first prompt |
|---|---|---|---|---|---|
| work (camunda-hub) | 28,243 chars ≈ 7.1k tokens | 40 skills, 19,128 chars | ≈ 4.5k tokens | ≈ 5.8k tokens | ≈ 8% of ~70k |
| perso (sondalab-ui) | 21,341 chars ≈ 5.3k tokens | 30 skills, 13,516 chars | ≈ 3.2k tokens | ≈ 3.8k tokens | ≈ 7.5% of ~50k |

**Ceiling.** Plugin skills (superpowers alone lists 15, about 8 of them used) and the plugin agent
list stay. Even perfect pruning of the whole listing would save about 10% of the first prompt. The
auto-trigger argument (fewer candidate skills, fewer wrong picks) is unmeasured: the audit found no
wrong-skill invocation. This redesign roughly triples today's saving; it does not change the order
of magnitude.

## 8. Slices

| Slice | Delivers | Acceptance criteria |
|---|---|---|
| **Slice 1** — record | Stop-hook recording, `skill_usage.json`, `cld usage`, `cld usage backfill`. No launch behavior changes. | After a session that invokes a skill by tool and one by `/name`, `cld usage` shows both, attributed to the main checkout even when run from a worktree; running the hook twice on the same transcript does not double counts; backfill over a fixture transcript directory reproduces the same record. |
| **Slice 2** — graded overrides, opt-in | The §4.1 ladder behind `[scoping] mode = "usage"` (default `"ranker"`), compose emits graded values, `--explain` shows values and reasons, `slash_only` rule action. | With the mode on, an unused bundled skill is `name-only`, a used one has no override, a `slash_only` skill is `user-invocable-only`, an `always_drop` skill is `off`; a fresh repository (under `min_sessions`) gets no usage-based override; savings match footprints. |
| **Slice 3** — default | `mode = "usage"` becomes the default; the ranker leaves scoping and appears as a hint in `cld rules`; plugin last-use shown. | Every scoping decision in `--explain` cites a rule, a pin or usage, never a score; the 2026-08-21 skill-scoping tests still pass for user skills. |

## 9. Risks and open questions

| Risk or question | Mitigation or next step |
|---|---|
| **Merge with the user's own `skillOverrides`.** Does the overlay replace or merge per key, and does an overlay `"on"` re-enable a skill the user turned off? | Probe before Slice 2. Until then, never emit `"on"`: absence already means on, and a user's own `"off"` must win. |
| **`skillOverrides` is not a documented public contract.** Values or key formats may change. | Re-run the L1–L5 probes on each Claude Code minor version (add them to `cld doctor` as an optional self-test); fail open by emitting nothing when a probe fails. |
| **`name-only` lowers auto-trigger quality** for a skill the model would have picked from its description. | Only unused skills are demoted, and a single use restores them. `--explain` makes every demotion visible. |
| **Stop-hook cost.** | The hook already reads the whole transcript (52 MB measured at 0.29 s, inside the 5 s budget); counting adds a pass over parsed events. |
| **Key format for repository and org skills.** | Verified for `anthropic-skills:pptx` and a repository skill by name (L2); re-check namespaced repository skills if they appear. |
| **Window and thresholds** (`usage_window_days`, `min_sessions`). | Defaults from the audit's month of data; both configurable. |

## 10. Alternatives considered

- **A stronger embedding model.** Rejected: it may fix some orderings, but it still has no notion of
  whether a repository uses a skill, and the threshold stays a guess.
- **One-time model classification of each skill** (for example "coding tool" versus "personal").
  Deferred: useful as a hint for cold-start repositories, not as the decision. It adds a model call
  and non-determinism.
- **`disableBundledSkills`.** Rejected as the main lever: all-or-nothing, and several bundled skills
  are in daily use (L6).
- **Dropping unused skills to `"off"`.** Rejected: creates the feedback loop §4.1 guards against.
