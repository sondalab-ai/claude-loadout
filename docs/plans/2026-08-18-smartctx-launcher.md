# smartctx Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `smartctx`, a distributable Python launcher that scopes a Claude Code session to the goal-relevant subset of MCP servers, plugins, and skills for that session only.

**Architecture:** A single console entrypoint orchestrates five pure-ish units — config loader, inventory adapter, goal detector, relevance ranker (local model2vec embeddings), launch composer — then runs `claude` as a child with two ephemeral overlays (`--strict-mcp-config --mcp-config` + `--settings enabledPlugins`) and cleans them up on exit. Everything is discovery-driven and config-driven; nothing is bound to one machine.

**Tech Stack:** Python ≥ 3.11, `model2vec` (static embeddings, no torch), `numpy`, stdlib `tomllib`, `subprocess`. Optional extra `smartctx[rules]` adds `llama-cpp-python` (small GGUF instruct model) for compiling NL exclusion rules. Packaged for `pipx`/`uv`.

**Spec:** `docs/specs/2026-08-18-smartctx-launcher-design.md`

## Global Constraints

- Python ≥ 3.11 (stdlib `tomllib`; no `tomli` dependency).
- All `pytest`/`pip` commands assume the virtualenv is active (`. .venv/bin/activate`); Task 1
  creates `.venv`. A fresh implementer shell must activate it before running tests.
- **Fail-open always:** any error → launch the full unscoped `claude` with a stderr warning; never make `claude` unlaunchable.
- **No global config mutation:** overlays are written to a temp dir and deleted after the child exits.
- **No hardcoded machine paths / plugin ids / alias names:** config root resolved from `$CLAUDE_CONFIG_DIR`, default `~/.claude`.
- **Base launch is a normal session** (keeps `CLAUDE.md`, hooks, memory); never `--bare`.
- Config format is **TOML**. Config precedence (later wins): built-in default → `$CLAUDE_CONFIG_DIR/smartctx/config.toml` → `./.smartctx/config.toml` → env (`SMARTCTX_ALWAYS_KEEP`, `SMARTCTX_THRESHOLD`).
- Relevance threshold default: absolute cosine cutoff `0.35`.
- Ranker takes an injected `embed` callable so tests never download a model.
- Rule compiler takes an injected `compile_fn` callable so tests never load an instruct model.
- Exclusion rules (spec §12): precedence `always_keep` config > rules > threshold. Predicate
  actions `always_keep|always_drop|keep_if|drop_if`; `keep_if` no-match ⇒ force drop, `drop_if`
  no-match ⇒ undecided. Context match = case-insensitive substring of each term, combined by
  `match_mode` (`any|all`). Rules load from `$CLAUDE_CONFIG_DIR/smartctx/rules.toml` then
  `./.smartctx/rules.toml` (repo overrides per `target`).
- Rule compilation runs ONLY at authoring time (setup or launch elicitation), never at plain
  launch; launches stay deterministic and offline.

---

## File Structure

```
smart-context/
  pyproject.toml                 # package metadata, deps, console_scripts: smartctx
  src/smartctx/
    __init__.py
    __main__.py                  # python -m smartctx -> cli.main()
    cli.py                       # arg parsing, orchestration, --explain, fail-open, child run
    config.py                    # Config dataclass + load_config() precedence chain
    inventory.py                 # Item dataclass + claude_code_inventory(config_root)
    goal.py                      # GoalResult + detect_goal(cwd) + goal cache
    ranker.py                    # Selection + Ranker(embed) + make_model2vec_embed() + keyword_embed
    compose.py                   # LaunchPlan + compose(kept, all_items, config_root, passthrough)
    rules.py                     # Rule + Predicate + load_rules() + evaluate() + apply_rules()
    compiler.py                  # compile_rule(nl,item,compile_fn) + make_local_instruct(path)
    harness.py                   # Harness Protocol (inventory, compose)
  tests/
    conftest.py                  # fixtures: fake config root, stub embedder
    test_config.py
    test_inventory.py
    test_goal.py
    test_ranker.py
    test_compose.py
    test_rules.py
    test_compiler.py
    test_cli.py
    fixtures/
      config_root/               # sample settings.json + .claude.json
```

**Shared types (defined once, referenced across tasks):**

```python
# inventory.py
@dataclass(frozen=True)
class Item:
    id: str                      # plugin id, mcp server name, or skill name
    kind: str                    # "plugin" | "mcp" | "skill"
    name: str
    description: str

# config.py
@dataclass(frozen=True)
class Config:
    config_root: Path
    always_keep: tuple[str, ...] # ids/globs never pruned
    threshold: float             # absolute cosine cutoff
    model_name: str

# goal.py
@dataclass(frozen=True)
class GoalResult:
    goal: str
    confidence: float            # 0.0–1.0
    source: str                  # "cache" | "signals" | "prompt" | "none"

# ranker.py
@dataclass(frozen=True)
class Selection:
    kept: tuple[Item, ...]
    dropped: tuple[tuple[Item, float], ...]  # (item, score)

# compose.py
@dataclass(frozen=True)
class LaunchPlan:
    argv: list[str]
    env: dict[str, str]
    tmp_paths: list[Path]        # overlay files to delete after child exits

# rules.py
@dataclass(frozen=True)
class Predicate:
    action: str                  # "keep_if" | "drop_if" | "always_keep" | "always_drop"
    match: tuple[str, ...]       # context terms (empty for always_*)
    match_mode: str              # "any" | "all"

@dataclass(frozen=True)
class Rule:
    target: str                  # item id or glob
    nl: str                      # natural-language source
    predicate: Predicate

# apply_rules(items, rules, context) -> RuleOutcome
@dataclass(frozen=True)
class RuleOutcome:
    forced_keep: tuple[Item, ...]
    forced_drop: tuple[Item, ...]
    undecided: tuple[Item, ...]      # no matching decisive rule -> go to ranker

# elicitation targets are computed in the CLI AFTER ranking:
#   ranked_dropped items for which has_rule(item, rules) is False.
# rules.py also exposes: has_rule(item, rules) -> bool
```

---

## Task 1: Project scaffold + Config loader

**Files:**
- Create: `pyproject.toml`, `src/smartctx/__init__.py`, `src/smartctx/config.py`
- Test: `tests/test_config.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Config` dataclass; `load_config(cwd: Path, environ: Mapping[str, str]) -> Config`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from pathlib import Path
from smartctx.config import load_config

def test_env_overrides_repo_and_defaults(tmp_path: Path):
    root = tmp_path / "cfgroot"; root.mkdir()
    (root / "smartctx").mkdir()
    (root / "smartctx" / "config.toml").write_text(
        'always_keep = ["a"]\nthreshold = 0.5\n')
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / ".smartctx").mkdir()
    (repo / ".smartctx" / "config.toml").write_text('always_keep = ["b"]\n')
    env = {"CLAUDE_CONFIG_DIR": str(root), "SMARTCTX_ALWAYS_KEEP": "c,d",
           "SMARTCTX_THRESHOLD": "0.9"}
    cfg = load_config(cwd=repo, environ=env)
    assert cfg.config_root == root
    assert cfg.always_keep == ("c", "d")   # env wins
    assert cfg.threshold == 0.9            # env wins

def test_defaults_when_nothing_set(tmp_path: Path):
    cfg = load_config(cwd=tmp_path, environ={"CLAUDE_CONFIG_DIR": str(tmp_path)})
    assert cfg.always_keep == ()
    assert cfg.threshold == 0.35
    assert cfg.model_name == "minishlab/potion-base-8M"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: smartctx.config`.

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[project]
name = "smartctx"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["model2vec>=0.3", "numpy>=1.24"]

[project.scripts]
smartctx = "smartctx.cli:main"

[project.optional-dependencies]
dev = ["pytest>=8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/smartctx"]
```

- [ ] **Step 4: Write minimal implementation**

```python
# src/smartctx/config.py
from __future__ import annotations
import os, tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DEFAULT_THRESHOLD = 0.35
DEFAULT_MODEL = "minishlab/potion-base-8M"

@dataclass(frozen=True)
class Config:
    config_root: Path
    always_keep: tuple[str, ...]
    threshold: float
    model_name: str

def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}

def _resolve_root(environ: Mapping[str, str]) -> Path:
    raw = environ.get("CLAUDE_CONFIG_DIR")
    return Path(raw).expanduser() if raw else Path.home() / ".claude"

def load_config(cwd: Path, environ: Mapping[str, str] | None = None) -> Config:
    environ = os.environ if environ is None else environ
    root = _resolve_root(environ)
    layers = [_read_toml(root / "smartctx" / "config.toml"),
              _read_toml(cwd / ".smartctx" / "config.toml")]
    always: tuple[str, ...] = ()
    threshold = DEFAULT_THRESHOLD
    model = DEFAULT_MODEL
    for layer in layers:
        if "always_keep" in layer:
            always = tuple(layer["always_keep"])
        if "threshold" in layer:
            threshold = float(layer["threshold"])
        if "model_name" in layer:
            model = str(layer["model_name"])
    if "SMARTCTX_ALWAYS_KEEP" in environ:
        always = tuple(x for x in environ["SMARTCTX_ALWAYS_KEEP"].split(",") if x)
    if "SMARTCTX_THRESHOLD" in environ:
        threshold = float(environ["SMARTCTX_THRESHOLD"])
    return Config(config_root=root, always_keep=always, threshold=threshold, model_name=model)
```

Also create empty `src/smartctx/__init__.py` and `tests/conftest.py` (empty for now).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/smartctx/__init__.py src/smartctx/config.py tests/test_config.py tests/conftest.py
git commit -m "feat: config loader with precedence chain"
```

---

## Task 2: Inventory adapter (Claude Code)

**Files:**
- Create: `src/smartctx/inventory.py`, `src/smartctx/harness.py`
- Test: `tests/test_inventory.py`, `tests/fixtures/config_root/`

**Interfaces:**
- Consumes: `Config.config_root: Path`.
- Produces: `Item`; `claude_code_inventory(config_root: Path) -> list[Item]`. Reads `settings.json` (`enabledPlugins` map — only entries currently `true`), `.claude.json` (`mcpServers` object keys), and `skills/*/SKILL.md` frontmatter (`name`, `description`). Missing files/keys → skipped, never raise.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_inventory.py
from pathlib import Path
from smartctx.inventory import claude_code_inventory, Item

def _root(tmp_path: Path) -> Path:
    root = tmp_path / "root"; root.mkdir()
    (root / "settings.json").write_text(
        '{"enabledPlugins": {"superpowers@x": true, "figma@x": false}}')
    (root / ".claude.json").write_text(
        '{"mcpServers": {"Gmail": {"command": "x"}, "Spotify": {"command": "y"}}}')
    skill = root / "skills" / "astro-visibility"; skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: astro-visibility\ndescription: compute visible sky objects\n---\nbody")
    return root

def test_inventory_collects_enabled_plugins_mcp_and_skills(tmp_path: Path):
    items = claude_code_inventory(_root(tmp_path))
    by_id = {(i.kind, i.id) for i in items}
    assert ("plugin", "superpowers@x") in by_id
    assert ("plugin", "figma@x") not in by_id          # disabled → excluded
    assert ("mcp", "Gmail") in by_id and ("mcp", "Spotify") in by_id
    skill = next(i for i in items if i.kind == "skill")
    assert skill.id == "astro-visibility"
    assert "visible sky" in skill.description

def test_inventory_missing_files_returns_empty(tmp_path: Path):
    assert claude_code_inventory(tmp_path) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_inventory.py -v`
Expected: FAIL — `ModuleNotFoundError: smartctx.inventory`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/smartctx/harness.py
from __future__ import annotations
from pathlib import Path
from typing import Protocol
from smartctx.inventory import Item

class Harness(Protocol):
    def inventory(self, config_root: Path) -> list[Item]: ...
    def compose(self, kept, all_items, config_root, passthrough): ...
```

```python
# src/smartctx/inventory.py
from __future__ import annotations
import json, re
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Item:
    id: str
    kind: str
    name: str
    description: str

def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}

_FRONTMATTER = re.compile(r"^---\n(.*?)\n---", re.DOTALL)

def _frontmatter(text: str) -> dict[str, str]:
    m = _FRONTMATTER.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out

def claude_code_inventory(config_root: Path) -> list[Item]:
    items: list[Item] = []
    settings = _load_json(config_root / "settings.json")
    for pid, enabled in (settings.get("enabledPlugins") or {}).items():
        if enabled:
            items.append(Item(id=pid, kind="plugin", name=pid, description=pid))
    servers = _load_json(config_root / ".claude.json").get("mcpServers") or {}
    for name in servers:
        items.append(Item(id=name, kind="mcp", name=name, description=name))
    skills_dir = config_root / "skills"
    if skills_dir.is_dir():
        for md in skills_dir.glob("*/SKILL.md"):
            fm = _frontmatter(md.read_text(errors="ignore"))
            sid = fm.get("name") or md.parent.name
            items.append(Item(id=sid, kind="skill", name=sid,
                              description=fm.get("description", sid)))
    return items
```

> Note: plugin/mcp descriptions default to the id here. A follow-up may enrich plugin descriptions from marketplace manifests; the ranker still works on ids as weak text. Keep this task's scope to the three sources above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_inventory.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/smartctx/inventory.py src/smartctx/harness.py tests/test_inventory.py
git commit -m "feat: Claude Code inventory adapter"
```

---

## Task 3: Goal detector

**Files:**
- Create: `src/smartctx/goal.py`
- Test: `tests/test_goal.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `GoalResult`; `detect_goal(cwd: Path) -> GoalResult`. Signals: cached `.smartctx/goal` (confidence 1.0, source "cache"); else directory basename + marker files (`package.json`, `pyproject.toml`, `*.tsx`, `README*`) → a goal string with a heuristic confidence; empty dir → confidence 0.0, source "none". Also `write_goal_cache(cwd, goal)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_goal.py
from pathlib import Path
from smartctx.goal import detect_goal, write_goal_cache

def test_cache_takes_precedence(tmp_path: Path):
    write_goal_cache(tmp_path, "frontend work")
    r = detect_goal(tmp_path)
    assert r.goal == "frontend work" and r.source == "cache" and r.confidence == 1.0

def test_signals_from_markers(tmp_path: Path):
    d = tmp_path / "my-astro-tool"; d.mkdir()
    (d / "pyproject.toml").write_text("[project]\nname='x'")
    r = detect_goal(d)
    assert "my-astro-tool" in r.goal and "python" in r.goal.lower()
    assert r.confidence > 0.0 and r.source == "signals"

def test_empty_dir_low_confidence(tmp_path: Path):
    r = detect_goal(tmp_path)
    assert r.confidence == 0.0 and r.source == "none"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_goal.py -v`
Expected: FAIL — `ModuleNotFoundError: smartctx.goal`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/smartctx/goal.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class GoalResult:
    goal: str
    confidence: float
    source: str

_CACHE = lambda cwd: cwd / ".smartctx" / "goal"

def write_goal_cache(cwd: Path, goal: str) -> None:
    d = cwd / ".smartctx"; d.mkdir(exist_ok=True)
    (d / "goal").write_text(goal.strip() + "\n")

def detect_goal(cwd: Path) -> GoalResult:
    cache = _CACHE(cwd)
    if cache.is_file():
        txt = cache.read_text().strip()
        if txt:
            return GoalResult(goal=txt, confidence=1.0, source="cache")
    tags: list[str] = [cwd.name.replace("-", " ").replace("_", " ")]
    conf = 0.0
    if (cwd / "pyproject.toml").exists():
        tags.append("python project"); conf += 0.3
    if (cwd / "package.json").exists():
        tags.append("javascript project"); conf += 0.3
    if any(cwd.glob("*.tsx")) or any(cwd.glob("**/*.tsx")):
        tags.append("react frontend"); conf += 0.3
    if any(cwd.glob("README*")):
        conf += 0.1
    if conf == 0.0:
        return GoalResult(goal=cwd.name, confidence=0.0, source="none")
    return GoalResult(goal=" ".join(tags), confidence=min(conf, 1.0), source="signals")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_goal.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/smartctx/goal.py tests/test_goal.py
git commit -m "feat: goal detector with cache and marker signals"
```

---

## Task 4: Relevance ranker

**Files:**
- Create: `src/smartctx/ranker.py`
- Test: `tests/test_ranker.py`

**Interfaces:**
- Consumes: `Item` (inventory), `Config.threshold`, `Config.always_keep`.
- Produces: `Selection`; `Ranker(embed: Callable[[list[str]], "np.ndarray"])` with `.rank(goal, items, threshold, always_keep) -> Selection`; `make_model2vec_embed(model_name) -> embed`; `keyword_embed` fallback. `always_keep` entries match item id by `fnmatch` glob and are always kept regardless of score.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ranker.py
import numpy as np
from smartctx.inventory import Item
from smartctx.ranker import Ranker

def _stub_embed(texts):
    # deterministic: map keyword -> orthogonal-ish vectors
    table = {"astro": [1, 0, 0], "gmail": [0, 1, 0], "goal": [1, 0.2, 0]}
    return np.array([table.get(next((k for k in table if k in t.lower()), "goal"),
                              [0, 0, 1]) for t in texts], dtype=float)

def test_keeps_relevant_drops_irrelevant():
    items = [Item("astro", "skill", "astro", "astro sky imaging"),
             Item("Gmail", "mcp", "Gmail", "gmail email")]
    r = Ranker(embed=_stub_embed)
    sel = r.rank(goal="astro imaging goal", items=items, threshold=0.5, always_keep=())
    kept = {i.id for i in sel.kept}
    assert "astro" in kept and "Gmail" not in kept

def test_always_keep_glob_survives_low_score():
    items = [Item("Gmail", "mcp", "Gmail", "gmail email")]
    r = Ranker(embed=_stub_embed)
    sel = r.rank(goal="astro", items=items, threshold=0.99, always_keep=("Gm*",))
    assert {i.id for i in sel.kept} == {"Gmail"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ranker.py -v`
Expected: FAIL — `ModuleNotFoundError: smartctx.ranker`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/smartctx/ranker.py
from __future__ import annotations
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Callable
import numpy as np
from smartctx.inventory import Item

@dataclass(frozen=True)
class Selection:
    kept: tuple[Item, ...]
    dropped: tuple[tuple[Item, float], ...]

def _cosine(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    an = a / (np.linalg.norm(a) + 1e-9)
    bn = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-9)
    return bn @ an

class Ranker:
    def __init__(self, embed: Callable[[list[str]], np.ndarray]):
        self._embed = embed

    def rank(self, goal: str, items: list[Item], threshold: float,
             always_keep: tuple[str, ...]) -> Selection:
        if not items:
            return Selection(kept=(), dropped=())
        vecs = self._embed([goal] + [f"{i.name}. {i.description}" for i in items])
        goal_vec, item_vecs = vecs[0], vecs[1:]
        scores = _cosine(goal_vec, item_vecs)
        kept, dropped = [], []
        for item, score in zip(items, scores):
            forced = any(fnmatch(item.id, pat) for pat in always_keep)
            if forced or score >= threshold:
                kept.append(item)
            else:
                dropped.append((item, float(score)))
        return Selection(kept=tuple(kept), dropped=tuple(dropped))

def make_model2vec_embed(model_name: str) -> Callable[[list[str]], np.ndarray]:
    from model2vec import StaticModel
    model = StaticModel.from_pretrained(model_name)
    return lambda texts: np.asarray(model.encode(texts), dtype=float)

def keyword_embed(texts: list[str]) -> np.ndarray:
    # offline fallback: bag-of-words hashing into a fixed space
    dim = 256
    out = np.zeros((len(texts), dim))
    for r, t in enumerate(texts):
        for tok in t.lower().split():
            out[r, hash(tok) % dim] += 1.0
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ranker.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/smartctx/ranker.py tests/test_ranker.py
git commit -m "feat: relevance ranker with injected embedder and always-keep globs"
```

---

## Task 5: Launch composer

**Files:**
- Create: `src/smartctx/compose.py`
- Test: `tests/test_compose.py`

**Interfaces:**
- Consumes: `Selection.kept: tuple[Item,...]`, the full `list[Item]`, `Config.config_root`, passthrough args.
- Produces: `LaunchPlan`; `compose(kept, all_items, config_root, passthrough) -> LaunchPlan`. Writes two temp files: a curated `mcp.json` (`{"mcpServers": {name: <original def>}}` for kept mcp items, read back from `config_root/.claude.json`) and a `settings.json` overlay (`{"enabledPlugins": {pid: false}}` for every plugin item NOT kept). `argv = ["claude", "--strict-mcp-config", "--mcp-config", <mcp>, "--settings", <settings>, *passthrough]`. `env` preserves `CLAUDE_CONFIG_DIR`. Temp files listed in `tmp_paths`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compose.py
import json
from pathlib import Path
from smartctx.inventory import Item
from smartctx.compose import compose

def test_compose_writes_overlays_and_argv(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir()
    (root / ".claude.json").write_text(json.dumps(
        {"mcpServers": {"Gmail": {"command": "g"}, "Spotify": {"command": "s"}}}))
    all_items = [Item("Gmail", "mcp", "Gmail", ""), Item("Spotify", "mcp", "Spotify", ""),
                 Item("figma@x", "plugin", "figma", ""), Item("superpowers@x", "plugin", "sp", "")]
    kept = [all_items[0], all_items[3]]  # keep Gmail + superpowers; drop Spotify + figma
    plan = compose(kept, all_items, root, passthrough=["-c"], environ={"CLAUDE_CONFIG_DIR": str(root)})
    assert plan.argv[0] == "claude"
    assert "--strict-mcp-config" in plan.argv and "-c" == plan.argv[-1]
    mcp_path = Path(plan.argv[plan.argv.index("--mcp-config") + 1])
    settings_path = Path(plan.argv[plan.argv.index("--settings") + 1])
    mcp = json.loads(mcp_path.read_text())
    assert set(mcp["mcpServers"]) == {"Gmail"} and mcp["mcpServers"]["Gmail"] == {"command": "g"}
    settings = json.loads(settings_path.read_text())
    assert settings["enabledPlugins"] == {"figma@x": False}   # only dropped plugin
    assert plan.env["CLAUDE_CONFIG_DIR"] == str(root)
    assert set(plan.tmp_paths) == {mcp_path, settings_path}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_compose.py -v`
Expected: FAIL — `ModuleNotFoundError: smartctx.compose`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/smartctx/compose.py
from __future__ import annotations
import json, os, tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

@dataclass(frozen=True)
class LaunchPlan:
    argv: list[str]
    env: dict[str, str]
    tmp_paths: list[Path]

def _write_tmp(prefix: str, data: dict) -> Path:
    fd, name = tempfile.mkstemp(prefix=f"smartctx-{prefix}-", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    return Path(name)

def compose(kept, all_items, config_root: Path, passthrough: list[str],
            environ: Mapping[str, str] | None = None) -> LaunchPlan:
    environ = os.environ if environ is None else environ
    kept_ids = {i.id for i in kept}
    server_defs = {}
    try:
        server_defs = json.loads((config_root / ".claude.json").read_text()).get("mcpServers", {})
    except (OSError, json.JSONDecodeError):
        server_defs = {}
    curated = {name: server_defs.get(name, {})
               for i in all_items if i.kind == "mcp" and i.id in kept_ids
               for name in [i.id]}
    dropped_plugins = {i.id: False for i in all_items
                       if i.kind == "plugin" and i.id not in kept_ids}
    mcp_path = _write_tmp("mcp", {"mcpServers": curated})
    settings_path = _write_tmp("settings", {"enabledPlugins": dropped_plugins})
    argv = ["claude", "--strict-mcp-config", "--mcp-config", str(mcp_path),
            "--settings", str(settings_path), *passthrough]
    env = dict(environ)
    return LaunchPlan(argv=argv, env=env, tmp_paths=[mcp_path, settings_path])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_compose.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/smartctx/compose.py tests/test_compose.py
git commit -m "feat: launch composer writing ephemeral mcp + settings overlays"
```

---

## Task 6: Exclusion rules store + evaluator

**Files:**
- Create: `src/smartctx/rules.py`
- Test: `tests/test_rules.py`

**Interfaces:**
- Consumes: `Item` (Task 2).
- Produces: `Predicate`, `Rule`, `RuleOutcome`; `load_rules(config_root, cwd) -> list[Rule]`;
  `evaluate(predicate, context) -> "keep" | "drop" | "undecided"`;
  `apply_rules(items, rules, context) -> RuleOutcome`; `has_rule(item, rules) -> bool`;
  `save_rule(config_root, rule) -> None` (writes to `$CLAUDE_CONFIG_DIR/smartctx/rules.toml`).
  Precedence when several rules target one item: exact id over glob; `always_*` over conditional.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rules.py
from pathlib import Path
from smartctx.inventory import Item
from smartctx.rules import Predicate, Rule, evaluate, apply_rules, has_rule, load_rules, save_rule

def _items():
    return [Item("camunda-ds", "plugin", "camunda-ds", "corporate design system"),
            Item("astro", "skill", "astro", "astrophotography")]

def test_evaluate_semantics():
    keep_if = Predicate("keep_if", ("camunda", "bpmn"), "any")
    assert evaluate(keep_if, "camunda frontend work") == "keep"
    assert evaluate(keep_if, "astro imaging") == "drop"          # keep_if no-match -> drop
    drop_if = Predicate("drop_if", ("astro",), "any")
    assert evaluate(drop_if, "astro imaging") == "drop"
    assert evaluate(drop_if, "camunda work") == "undecided"      # drop_if no-match -> undecided
    assert evaluate(Predicate("always_keep", (), "any"), "anything") == "keep"

def test_apply_rules_partitions_items():
    rules = [Rule("camunda-*", "corp", Predicate("keep_if", ("camunda",), "any"))]
    out = apply_rules(_items(), rules, context="astro imaging")
    assert [i.id for i in out.forced_drop] == ["camunda-ds"]     # keep_if no-match -> drop
    assert [i.id for i in out.undecided] == ["astro"]            # no rule -> undecided
    assert out.forced_keep == ()

def test_exact_id_beats_glob():
    rules = [Rule("camunda-*", "g", Predicate("always_drop", (), "any")),
             Rule("camunda-ds", "e", Predicate("always_keep", (), "any"))]
    out = apply_rules(_items()[:1], rules, context="x")
    assert [i.id for i in out.forced_keep] == ["camunda-ds"]

def test_load_and_save_roundtrip(tmp_path: Path):
    root = tmp_path / "root"; (root / "smartctx").mkdir(parents=True)
    save_rule(root, Rule("figma*", "design only", Predicate("keep_if", ("design", "ui"), "any")))
    rules = load_rules(config_root=root, cwd=tmp_path)
    assert any(r.target == "figma*" and r.predicate.match == ("design", "ui") for r in rules)
    assert has_rule(Item("figma@x", "plugin", "figma", ""), rules) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `. .venv/bin/activate && pytest tests/test_rules.py -v`
Expected: FAIL — `ModuleNotFoundError: smartctx.rules`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/smartctx/rules.py
from __future__ import annotations
import tomllib
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from smartctx.inventory import Item

@dataclass(frozen=True)
class Predicate:
    action: str
    match: tuple[str, ...]
    match_mode: str

@dataclass(frozen=True)
class Rule:
    target: str
    nl: str
    predicate: Predicate

@dataclass(frozen=True)
class RuleOutcome:
    forced_keep: tuple[Item, ...]
    forced_drop: tuple[Item, ...]
    undecided: tuple[Item, ...]

def evaluate(predicate: Predicate, context: str) -> str:
    if predicate.action == "always_keep":
        return "keep"
    if predicate.action == "always_drop":
        return "drop"
    ctx = context.lower()
    hits = [term.lower() in ctx for term in predicate.match]
    matched = all(hits) if predicate.match_mode == "all" else any(hits)
    if predicate.action == "keep_if":
        return "keep" if matched else "drop"
    if predicate.action == "drop_if":
        return "drop" if matched else "undecided"
    return "undecided"

def _rules_for(item: Item, rules: list[Rule]) -> list[Rule]:
    matches = [r for r in rules if fnmatch(item.id, r.target)]
    # exact-id rules first, then always_* over conditional
    matches.sort(key=lambda r: (r.target != item.id,
                                r.predicate.action not in ("always_keep", "always_drop")))
    return matches

def has_rule(item: Item, rules: list[Rule]) -> bool:
    return bool(_rules_for(item, rules))

def apply_rules(items: list[Item], rules: list[Rule], context: str) -> RuleOutcome:
    keep, drop, undecided = [], [], []
    for item in items:
        decision = "undecided"
        for rule in _rules_for(item, rules):
            decision = evaluate(rule.predicate, context)
            if decision != "undecided":
                break
        (keep if decision == "keep" else drop if decision == "drop" else undecided).append(item)
    return RuleOutcome(tuple(keep), tuple(drop), tuple(undecided))

def _rules_file(config_root: Path) -> Path:
    return config_root / "smartctx" / "rules.toml"

def _parse(path: Path) -> list[Rule]:
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return []
    out = []
    for r in data.get("rule", []):
        p = r.get("predicate", {})
        out.append(Rule(target=r["target"], nl=r.get("nl", ""),
                        predicate=Predicate(action=p.get("action", "always_keep"),
                                            match=tuple(p.get("match", [])),
                                            match_mode=p.get("match_mode", "any"))))
    return out

def load_rules(config_root: Path, cwd: Path) -> list[Rule]:
    user = {r.target: r for r in _parse(_rules_file(config_root))}
    repo = {r.target: r for r in _parse(cwd / ".smartctx" / "rules.toml")}
    return list({**user, **repo}.values())   # repo overrides per target

def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')

def save_rule(config_root: Path, rule: Rule) -> None:
    path = _rules_file(config_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    p = rule.predicate
    block = (f'\n[[rule]]\ntarget = "{_esc(rule.target)}"\nnl = "{_esc(rule.nl)}"\n'
             f'[rule.predicate]\naction = "{p.action}"\n'
             f'match = [{", ".join(f\'"{_esc(m)}"\' for m in p.match)}]\n'
             f'match_mode = "{p.match_mode}"\n')
    with path.open("a") as fh:
        fh.write(block)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `. .venv/bin/activate && pytest tests/test_rules.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/smartctx/rules.py tests/test_rules.py
git commit -m "feat: exclusion rules store and deterministic evaluator"
```

---

## Task 7: Rule compiler (local instruct model, injected)

**Files:**
- Create: `src/smartctx/compiler.py`
- Modify: `pyproject.toml` (add `[project.optional-dependencies] rules = ["llama-cpp-python>=0.2"]`)
- Test: `tests/test_compiler.py`

**Interfaces:**
- Consumes: `Item` (Task 2), `Predicate` (Task 6).
- Produces: `compile_rule(nl, item, compile_fn) -> Predicate | None` — builds the constrained
  prompt, calls `compile_fn(prompt) -> str`, parses/validates JSON into a `Predicate`; invalid →
  retry once, then return `None` (caller applies the §7 degrade). `make_local_instruct(model_path)
  -> compile_fn` wraps llama-cpp (imported lazily so the base install works without it).
  `build_prompt(nl, item) -> str` is separate and pure (tested directly).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compiler.py
import json
from smartctx.inventory import Item
from smartctx.compiler import compile_rule, build_prompt

_ITEM = Item("camunda-ds", "plugin", "camunda-ds", "corporate design system")

def test_build_prompt_mentions_item_and_actions():
    p = build_prompt("corporate, only for camunda work", _ITEM)
    assert "camunda-ds" in p and "keep_if" in p and "drop_if" in p

def test_compile_rule_parses_valid_json():
    def fake(_prompt):
        return json.dumps({"action": "keep_if", "match": ["camunda", "bpmn"], "match_mode": "any"})
    pred = compile_rule("corporate only for camunda", _ITEM, compile_fn=fake)
    assert pred.action == "keep_if" and pred.match == ("camunda", "bpmn")

def test_compile_rule_retries_then_gives_up():
    calls = {"n": 0}
    def bad(_prompt):
        calls["n"] += 1
        return "not json"
    assert compile_rule("x", _ITEM, compile_fn=bad) is None
    assert calls["n"] == 2                     # one retry
```

- [ ] **Step 2: Run test to verify it fails**

Run: `. .venv/bin/activate && pytest tests/test_compiler.py -v`
Expected: FAIL — `ModuleNotFoundError: smartctx.compiler`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/smartctx/compiler.py
from __future__ import annotations
import json
from typing import Callable
from smartctx.inventory import Item
from smartctx.rules import Predicate

_VALID = {"keep_if", "drop_if", "always_keep", "always_drop"}

def build_prompt(nl: str, item: Item) -> str:
    return (
        "Translate the exclusion rule into a JSON predicate. Output ONLY JSON.\n"
        'Schema: {"action": one of ["keep_if","drop_if","always_keep","always_drop"], '
        '"match": [strings], "match_mode": "any"|"all"}\n'
        "keep_if: keep the item only when the session context matches; "
        "drop_if: drop only when it matches.\n"
        f"Item id: {item.id}\nItem kind: {item.kind}\nItem description: {item.description}\n"
        f"Rule (natural language): {nl}\nJSON:"
    )

def _parse(text: str) -> Predicate | None:
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        data = json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return None
    action = data.get("action")
    match = data.get("match", [])
    if action not in _VALID or not isinstance(match, list):
        return None
    if action in ("keep_if", "drop_if") and not match:
        return None
    mode = data.get("match_mode", "any")
    return Predicate(action=action, match=tuple(str(m) for m in match),
                     match_mode="all" if mode == "all" else "any")

def compile_rule(nl: str, item: Item, compile_fn: Callable[[str], str]) -> Predicate | None:
    prompt = build_prompt(nl, item)
    for _ in range(2):                         # initial try + one retry
        pred = _parse(compile_fn(prompt))
        if pred is not None:
            return pred
    return None

def make_local_instruct(model_path: str) -> Callable[[str], str]:
    from llama_cpp import Llama                 # lazy: base install has no llama-cpp
    llm = Llama(model_path=model_path, n_ctx=2048, verbose=False)
    def compile_fn(prompt: str) -> str:
        out = llm(prompt, max_tokens=128, temperature=0.0, stop=["\n\n"])
        return out["choices"][0]["text"]
    return compile_fn
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `. .venv/bin/activate && pytest tests/test_compiler.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/smartctx/compiler.py pyproject.toml tests/test_compiler.py
git commit -m "feat: NL rule compiler with injected instruct model"
```

---

## Task 8: CLI orchestration (fail-open, --explain, rules, child run)

**Files:**
- Create: `src/smartctx/cli.py`, `src/smartctx/__main__.py`
- Modify: `src/smartctx/config.py` (add `rule_model_path: str | None` field; parse toml
  `rule_model_path` and env `SMARTCTX_RULE_MODEL`; default `None`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above (config, inventory, goal, ranker, compose, rules, compiler).
- Produces: `main(argv=None) -> int`.
  - Subcommand `smartctx rules` → `_cmd_rules(cwd)`: walk inventory, for each item without a
    rule elicit + compile + save.
  - Otherwise scoping flow: `load_config` → `claude_code_inventory` → resolve goal (prompt once
    if confidence < 0.15 and interactive; **persist a prompted goal via `write_goal_cache`** —
    preflight ruling) → `load_rules` → `apply_rules` → `Ranker.rank(context, undecided)` →
    launch-time elicitation for rule-less dropped candidates (interactive only) → `compose`.
  - `--explain` prints kept/dropped + argv and returns 0 without launching.
  - Otherwise `subprocess.run(argv, env)`, delete `tmp_paths` in `finally`, return child rc.
  - **Any exception in the scoping pipeline → warn to stderr and run full `["claude", *passthrough]`.**
  - `_build_compiler(cfg)` returns a `compile_fn` from `make_local_instruct(cfg.rule_model_path)`
    or `None` (no path / llama-cpp missing) — monkeypatched in tests.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py
import json, sys
from pathlib import Path
import smartctx.cli as cli

class _RC:
    def __init__(self, code): self.returncode = code

def _root(tmp_path):
    root = tmp_path / "root"; root.mkdir()
    (root / "settings.json").write_text('{"enabledPlugins": {"figma@x": true}}')
    (root / ".claude.json").write_text('{"mcpServers": {"Gmail": {"command": "g"}}}')
    return root

def test_explain_does_not_launch(tmp_path, monkeypatch, capsys):
    _root(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "root"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_model2vec_embed", lambda name: cli.keyword_embed)
    launched = {"ran": False}
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: launched.__setitem__("ran", True))
    rc = cli.main(["--explain"])
    assert rc == 0 and launched["ran"] is False
    assert "kept" in capsys.readouterr().out.lower()

def test_fail_open_launches_full_claude_on_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "root"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "claude_code_inventory",
                        lambda root: (_ for _ in ()).throw(RuntimeError("boom")))
    captured = {}
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda argv, **k: captured.setdefault("argv", argv) or _RC(0))
    rc = cli.main(["-c"])
    assert captured["argv"] == ["claude", "-c"]

def test_forced_drop_rule_excludes_item(tmp_path, monkeypatch, capsys):
    root = _root(tmp_path)
    (root / "smartctx").mkdir()
    (root / "smartctx" / "rules.toml").write_text(
        '[[rule]]\ntarget = "Gmail"\nnl = "never"\n'
        '[rule.predicate]\naction = "always_drop"\nmatch = []\nmatch_mode = "any"\n')
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_model2vec_embed", lambda name: cli.keyword_embed)
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: _RC(0))
    cli.main(["--explain"])
    out = capsys.readouterr().out
    kept_line = out[out.index("kept:"):out.index("dropped:")]
    assert "Gmail" not in kept_line          # always_drop rule removed it pre-ranking

def test_rules_subcommand_authors_rule(tmp_path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_build_compiler",
        lambda cfg: (lambda prompt: '{"action":"always_keep","match":[],"match_mode":"any"}'))
    replies = iter(["keep this plugin", "keep this server"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(replies, ""))
    rc = cli.main(["rules"])
    assert rc == 0
    assert "always_keep" in (root / "smartctx" / "rules.toml").read_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `. .venv/bin/activate && pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: smartctx.cli`.

- [ ] **Step 3: Write minimal implementation**

First, modify `src/smartctx/config.py` — add the `rule_model_path` field and parse it:

```python
# in Config dataclass, add field:
    rule_model_path: str | None
# in load_config, after model default block, add:
    rule_model = None
    for layer in layers:
        if "rule_model_path" in layer:
            rule_model = str(layer["rule_model_path"])
    if "SMARTCTX_RULE_MODEL" in environ:
        rule_model = environ["SMARTCTX_RULE_MODEL"]
# and pass rule_model_path=rule_model into the returned Config(...)
```

(Task 1's two config tests still pass — they assert specific fields, not the field set.)

```python
# src/smartctx/cli.py
from __future__ import annotations
import subprocess, sys
from pathlib import Path
from smartctx.config import load_config
from smartctx.inventory import claude_code_inventory, Item
from smartctx.goal import detect_goal, write_goal_cache
from smartctx.ranker import Ranker, make_model2vec_embed, keyword_embed
from smartctx.compose import compose
from smartctx.rules import load_rules, apply_rules, has_rule, save_rule, Rule, Predicate, evaluate
from smartctx.compiler import compile_rule, make_local_instruct

def _warn(msg: str) -> None:
    print(f"smartctx: {msg}", file=sys.stderr)

def _interactive(passthrough: list[str]) -> bool:
    return sys.stdin.isatty() and "-p" not in passthrough and "--print" not in passthrough

def _build_embed(model_name: str):
    try:
        return make_model2vec_embed(model_name)
    except Exception as exc:                       # import error, download failure, etc.
        _warn(f"model unavailable ({exc}); using keyword fallback")
        return keyword_embed

def _build_compiler(cfg):
    if not cfg.rule_model_path:
        return None
    try:
        return make_local_instruct(cfg.rule_model_path)
    except Exception as exc:
        _warn(f"rule model unavailable ({exc}); rule authoring disabled")
        return None

def _resolve_goal(cwd: Path, passthrough: list[str]) -> str:
    goal = detect_goal(cwd)
    if goal.confidence < 0.15 and _interactive(passthrough):
        entered = input(f"smartctx: session goal? [{goal.goal}] ").strip()
        if entered:
            write_goal_cache(cwd, entered)         # preflight ruling: persist, don't re-ask
            return entered
    return goal.goal

def _elicit(item: Item, context: str, compile_fn, config_root: Path) -> str:
    nl = input(f"smartctx: rule for '{item.id}' ({item.kind})? [enter=skip] ").strip()
    if not nl:
        return "undecided"
    pred = compile_rule(nl, item, compile_fn) if compile_fn else None
    if pred is None:                               # spec §7 degrade
        choice = input("  couldn't compile; [k]eep always / [d]rop always / [s]kip? ").strip().lower()
        pred = {"k": Predicate("always_keep", (), "any"),
                "d": Predicate("always_drop", (), "any")}.get(choice)
        if pred is None:
            return "undecided"
    save_rule(config_root, Rule(target=item.id, nl=nl, predicate=pred))
    return evaluate(pred, context)

def _scoped_plan(passthrough: list[str], cwd: Path):
    cfg = load_config(cwd=cwd)
    items = claude_code_inventory(cfg.config_root)
    if not items:
        return None, None
    context = _resolve_goal(cwd, passthrough)
    rules = load_rules(cfg.config_root, cwd)
    outcome = apply_rules(items, rules, context)
    embed = _build_embed(cfg.model_name)
    ranked = Ranker(embed=embed).rank(context, list(outcome.undecided), cfg.threshold, cfg.always_keep)
    kept = list(outcome.forced_keep) + list(ranked.kept)
    dropped = list(ranked.dropped)
    if _interactive(passthrough):                  # launch-time elicitation for rule-less drops
        compile_fn = _build_compiler(cfg)
        ruleless = [i for i, _ in dropped if not has_rule(i, rules)]
        if ruleless:
            _warn(f"{len(ruleless)} item(s) would be dropped with no rule; asking (enter to skip)")
            for item in ruleless:
                if _elicit(item, context, compile_fn, cfg.config_root) == "keep":
                    kept.append(item)
                    dropped = [(i, s) for i, s in dropped if i.id != item.id]
    forced_drop_ids = {i.id for i in outcome.forced_drop}
    kept = [i for i in kept if i.id not in forced_drop_ids]
    plan = compose(kept, items, cfg.config_root, passthrough)
    return (kept, dropped), plan

def _cmd_rules(cwd: Path) -> int:
    cfg = load_config(cwd=cwd)
    items = claude_code_inventory(cfg.config_root)
    rules = load_rules(cfg.config_root, cwd)
    compile_fn = _build_compiler(cfg)
    context = _resolve_goal(cwd, [])
    authored = 0
    for item in items:
        if has_rule(item, rules):
            continue
        if _elicit(item, context, compile_fn, cfg.config_root) != "undecided":
            authored += 1
    print(f"smartctx: authored {authored} rule(s)")
    return 0

def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cwd = Path.cwd()
    if argv and argv[0] == "rules":
        return _cmd_rules(cwd)
    explain = "--explain" in argv
    passthrough = [a for a in argv if a != "--explain"]
    try:
        result, plan = _scoped_plan(passthrough, cwd)
    except Exception as exc:
        _warn(f"scoping failed ({exc}); launching full session")
        return subprocess.run(["claude", *passthrough]).returncode
    if plan is None:
        return subprocess.run(["claude", *passthrough]).returncode
    kept, dropped = result
    if explain:
        print(f"goal-scoped session\nkept: {[i.id for i in kept]}")
        print(f"dropped: {[(i.id, round(s, 3)) for i, s in dropped]}")
        print("argv: " + " ".join(plan.argv))
        return 0
    try:
        return subprocess.run(plan.argv, env=plan.env).returncode
    finally:
        for p in plan.tmp_paths:
            try:
                p.unlink()
            except OSError:
                pass
```

```python
# src/smartctx/__main__.py
from smartctx.cli import main
import sys
if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `. .venv/bin/activate && pytest tests/test_cli.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the full suite + editable install smoke test**

Run: `. .venv/bin/activate && pytest -q && pip install -e . && smartctx --explain`
Expected: all tests PASS; `smartctx --explain` prints a plan (or a fallback warning) without launching a nested session.

- [ ] **Step 6: Commit**

```bash
git add src/smartctx/cli.py src/smartctx/__main__.py src/smartctx/config.py tests/test_cli.py
git commit -m "feat: CLI orchestration with rules, fail-open, and --explain"
```

---

## Task 9: README + alias documentation

**Files:**
- Create: `README.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Write README** covering: what it does (1 paragraph), `pipx install` (+ the
  `pipx install "smartctx[rules]"` extra for rule authoring), the alias integration examples
  (`CLAUDE_CONFIG_DIR=~/.claude-perso smartctx`), the config chain + a sample `.smartctx/config.toml`
  with an `always_keep` example, `--explain`, the exclusion-rules workflow (`smartctx rules`,
  launch-time elicitation, a sample `rules.toml`, and the note that NL→predicate compilation needs
  the optional local instruct model set via `rule_model_path`/`SMARTCTX_RULE_MODEL`), and the
  fail-open guarantee. Mark the personal always-keep list clearly as an example, not a default.

- [ ] **Step 2: Verify the sample config in the README parses**

Run: `python -c "import tomllib,io; tomllib.loads(open('README.md').read().split('```toml')[1].split('```')[0])"`
Expected: no error.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: usage, alias integration, config, fail-open"
```

---

## Task ordering & dependencies

Tasks 1–5 unchanged (config, inventory, goal, ranker, compose). Task 6 (rules) depends on Item
(T2). Task 7 (compiler) depends on Item (T2) + Predicate (T6). Task 8 (CLI) depends on all and
modifies config.py (adds `rule_model_path`). Task 9 (README) docs only. Task 1 is already
COMPLETE (commit fa84e41). Execute 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9.

## Self-Review (completed against spec)

- **Spec coverage:** §2 feasibility levers → Task 5 (compose) + Task 8 (argv). §3
  profiles/`CLAUDE_CONFIG_DIR` → Task 1 (`_resolve_root`) + Task 5 (env preserve). §4 components →
  Tasks 1–8. §5 data flow (rules before rank) → Task 8 `_scoped_plan`. §6 always-keep config chain
  → Task 1 + Task 4 (glob). §7 fail-open + rule/instruct degrade → Task 8 (all degrade paths). §8
  testing incl. `--explain` → every task + Task 8. §9 distribution + `[rules]` extra → Task 1
  (`pyproject`) + Task 7 (extra) + Task 9 (README). §11 defaults → Tasks 1/4/9. §12 exclusion rules
  (store/eval/compiler/elicitation/precedence) → Tasks 6, 7, 8.
- **Placeholder scan:** no TBD/TODO; all code steps carry real code.
- **Type consistency:** `Item(id, kind, name, description)`, `Config(config_root, always_keep,
  threshold, model_name, rule_model_path)`, `Selection(kept, dropped)`, `LaunchPlan(argv, env,
  tmp_paths)`, `Predicate(action, match, match_mode)`, `Rule(target, nl, predicate)`,
  `RuleOutcome(forced_keep, forced_drop, undecided)` used consistently across Tasks 2/4/5/6/7/8.
- **Preflight ruling wired:** Task 8 `_resolve_goal` calls `write_goal_cache` on a prompted goal.
- **Known follow-ups (not blocking v1):** plugin/mcp descriptions default to id (Task 2 note);
  standalone-skill pruning stays coarse per spec §10; second harness adapter deferred per spec §10;
  rule context is the goal string only (no separate tag vector) — sufficient for substring match.
