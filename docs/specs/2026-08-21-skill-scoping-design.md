# smartctx — Fine-Grained Skill Scoping (Design Spec)

> **What is this file.** Implementation contract for making standalone user-level skills
> *prunable per-session* in `smartctx`, the same way MCP servers and plugins already are.
> **Audience:** the implementing engineer. **Owner:** marcello.barile.
> **Companion files:** supersedes the "Standalone skills — coarse (accepted), all-off only"
> row in `docs/specs/2026-08-18-smartctx-launcher-design.md §2`; the implementation plan will
> live under `docs/plans/`. This spec is the contract; trade-offs and rejected alternatives
> are recorded inline.

Date: 2026-08-21 · Status: **Design approved, pre-implementation** · Target harness: Claude Code 2.1.238

---

## 1. Problem

The original launcher spec (2026-08-18 §2) recorded standalone user-level skills — those in
`$CLAUDE_CONFIG_DIR/skills/<name>/SKILL.md` — as **non-prunable**: the only CLI lever was
`--disable-slash-commands` (all skills off) or nothing. So today `smartctx` inventories these
skills, shows them, but force-keeps every one (`cli.py:170`, `savings.py:16`), and they never
enter the ranker or the launch gate.

**Goal:** make user-level skills first-class *prunable* items — ranked, gated, and dropped for
the current session only — with zero mutation of global config and **no auth breakage**.

## 2. Feasibility — verified levers (2026-08-21, Claude Code 2.1.238)

Every claim below was tested empirically against a live `claude -p` on the real config dir.

| # | Observation | Evidence |
|---|---|---|
| A | User-level skills are discovered **only** from `$CLAUDE_CONFIG_DIR/skills`. Plugin skills and project `.claude/skills` are separate. | `inventory.py:79`; CC binary strings. |
| B | **`skillOverrides: {"<name>": "off"}`** in the `--settings` overlay removes a user skill **and its description** from context. `astro-visibility` description quotable by default → "No skill named astro-visibility" with the override. Per-skill (`astro=NO mind=YES`). | live `claude -p`, control vs override probe. |
| C | Overriding `CLAUDE_CONFIG_DIR` breaks auth — it is bound to the config-dir path; any fresh shadow path yields "Not logged in". The mirror approach is rejected. | live `claude -p`: real path → OK, shadow path → not logged in. |
| D | The upstream requests for this (`#39749` duplicate, `#62174` not-planned, `#40770` stale) are all closed; the `#62174` "descriptions still load under off" bug is **fixed** in 2.1.238 (evidence B). | GitHub + live probe. |

**Chosen mechanism (native, minimal, no auth risk):**

For every dropped user skill, emit `skillOverrides[<id>] = "off"` into the tmp `--settings` file
that `compose` already writes (alongside `enabledPlugins`). Nothing else: no `--setting-sources`,
no `--plugin-dir`, no `CLAUDE_CONFIG_DIR` games, no synthetic plugin, no settings carryforward.

> **Superseded first cut.** An earlier implementation dropped the whole user settings source via
> `--setting-sources project,local`, carried `settings.json` forward through `--settings`, and
> re-injected kept skills as a symlink `--plugin-dir`. It worked, but `skillOverrides` makes it
> ~40 lines of avoidable machinery and blast radius. Kept in §6 for the record.

## 3. Scope & non-goals

**In scope:** user-level standalone skills — a top-level `$CLAUDE_CONFIG_DIR/skills/<name>/SKILL.md`.
At the time of writing there are **9** (`astro-visibility`, `cv-builder`, `mind-gym`,
`personal-trainer`, `memory-org`, `spec-versioning`, `sync-skills`, `debug-decisions`,
`copilot-adversarial-review`).

**Out of scope:**
- Plugin-bundled skills — already prunable transitively by dropping the plugin (existing behavior).
- **A plugin that happens to live under `skills/`** (e.g. `anthropic-skills`: a `.claude-plugin/`
  dir with a nested `skills/`). Its own `skills/*/SKILL.md` glob does not match a top-level
  `SKILL.md`, so it is inventoried nowhere and re-injected nowhere — and, being loaded via the
  plugin path, it survives the user-source drop untouched (verified live: nested `claude-api`
  present under scoping). It is neither counted above nor affected.
- Project `.claude/skills` — already project-local; not smartctx's to filter.

**Honest ROI.** These ~9 skills contribute only their description lines (a few hundred tokens
total), not the 2–4k first estimated. The feature is about a clean, consistent scoping surface,
not large token savings. Recorded here so it is not oversold.

## 4. Design

### 4.1 Pipeline (skills become prunable-full)

- **`inventory.py`** — unchanged; already emits user skills as `Item(kind="skill")`.
- **`savings.py`** — remove `skill` from the PRUNABLE exception. Add a `skill` entry to the
  token-cost map (flat fallback `50`; a real per-skill `measured[id]` from `smartctx measure`
  wins when present).
- **`ranker` / `rules`** — already kind-agnostic (a skill Item is ranked in `test_ranker`,
  ruled in `test_rules`); no change needed beyond removing the force-keep guard.
- **`cli.py`** — delete the `stays = lambda i: i.kind not in PRUNABLE` guard that routed
  non-prunable items to `always_loaded`; a `scope_skills` flag replaces it (force-keeps skills
  when off). Skills now flow through keep/drop like MCP/plugins.
- **gate / explain** — skills move out of the "always loaded — not prunable" section into the
  droppable section.

### 4.2 `compose` — native `skillOverrides`

```python
dropped_skills = {i.id: "off" for i in all_items if i.kind == "skill" and i.id not in kept_ids}
settings = {"enabledPlugins": dropped_plugins}
if dropped_skills:
    settings["skillOverrides"] = dropped_skills
```

That is the whole mechanism. The `--settings` overlay already exists; `skillOverrides` rides it.
No `--setting-sources`, no `--plugin-dir`, no carryforward, no extra tmp paths. When no skill is
dropped, no `skillOverrides` key is written — behavior is byte-identical to before.

### 4.3 Rollout

**On by default** (owner decision, 2026-08-21). Escape hatch `--no-scope-skills` /
`SMARTCTX_NO_SCOPE_SKILLS` force-keeps every skill (`scope_skills=False` → nothing dropped →
no `skillOverrides` emitted). On-by-default is trivially safe here: `skillOverrides` touches
only the named skills and mutates nothing global.

## 5. Risks & mitigations

| Risk | Mitigation |
|---|---|
| `skillOverrides` is an internal-ish settings key. | It backs the user-facing `/skills` toggle and CC's own "disabled via skillOverrides" errors — stable enough; pinned to CC 2.1.238 in this spec. |
| `skillOverrides: "off"` might stop auto-trigger but still load descriptions (the `#62174` bug). | **Fixed in 2.1.238** — verified live: the description is unquotable and the skill is absent from the model's list. |
| Skill id vs `skillOverrides` key mismatch. | Both key on the SKILL.md `name` (= `Item.id`); covered by a compose unit test and live E2E. |
| Auth breakage. | Avoided by construction — `CLAUDE_CONFIG_DIR` untouched. |

## 6. Alternatives considered

- **`--setting-sources project,local` + `--plugin-dir` re-injection** (the first, superseded
  implementation). Dropped the whole user settings source to hide user skills, carried
  `settings.json` forward via `--settings` to restore hooks/permissions, and re-injected kept
  skills as a symlink plugin. Worked and was fully tested, but `skillOverrides` does the same job
  natively in ~3 lines with zero blast radius — so it was removed.
- **Mirror `CLAUDE_CONFIG_DIR` (symlink all except `skills`).** Rejected: breaks auth outright,
  plus atomic-rename-onto-symlink would destroy refreshed credentials on cleanup.
- **Migrate user skills into smartctx-managed plugins**, prune via `enabledPlugins`. Rejected:
  invasive one-time move of the user's real skill files.
- **`--disable-slash-commands`.** Rejected: all-or-nothing, not scoping.
- **Wait for upstream.** Rejected: `#39749` (duplicate), `#62174` (not planned), `#40770` (stale)
  are all closed; no shipping fix. `skillOverrides` already exists and does the job.

## 7. Test plan

1. `savings.token_estimate` now counts skills; measured cost preferred over fallback.
2. `compose` writes `skillOverrides[<id>] = "off"` for exactly the dropped skills, and no
   `skillOverrides` key when none are dropped; never emits `--setting-sources` / `--plugin-dir`.
3. Gate/explain: a dropped skill is offered in the gate and shown in the dropping section.
4. `--no-scope-skills` force-keeps skills (never dropped, no overrides).
5. **Live E2E:** a scoped session shows the dropped skill absent and a kept skill present
   (`astro=NO mind=YES`), with the launch command carrying only `--settings`.
