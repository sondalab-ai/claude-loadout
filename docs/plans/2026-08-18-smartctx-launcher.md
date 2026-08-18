# smartctx Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `smartctx`, a distributable Python launcher that scopes a Claude Code session to the goal-relevant subset of MCP servers, plugins, and skills for that session only.

**Architecture:** A single console entrypoint orchestrates five pure-ish units — config loader, inventory adapter, goal detector, relevance ranker (local model2vec embeddings), launch composer — then runs `claude` as a child with two ephemeral overlays (`--strict-mcp-config --mcp-config` + `--settings enabledPlugins`) and cleans them up on exit. Everything is discovery-driven and config-driven; nothing is bound to one machine.

**Tech Stack:** Python ≥ 3.11, `model2vec` (static embeddings, no torch), `numpy`, stdlib `tomllib`, `subprocess`. Packaged for `pipx`/`uv`.

**Spec:** `docs/specs/2026-08-18-smartctx-launcher-design.md`

## Global Constraints

- Python ≥ 3.11 (stdlib `tomllib`; no `tomli` dependency).
- **Fail-open always:** any error → launch the full unscoped `claude` with a stderr warning; never make `claude` unlaunchable.
- **No global config mutation:** overlays are written to a temp dir and deleted after the child exits.
- **No hardcoded machine paths / plugin ids / alias names:** config root resolved from `$CLAUDE_CONFIG_DIR`, default `~/.claude`.
- **Base launch is a normal session** (keeps `CLAUDE.md`, hooks, memory); never `--bare`.
- Config format is **TOML**. Config precedence (later wins): built-in default → `$CLAUDE_CONFIG_DIR/smartctx/config.toml` → `./.smartctx/config.toml` → env (`SMARTCTX_ALWAYS_KEEP`, `SMARTCTX_THRESHOLD`).
- Relevance threshold default: absolute cosine cutoff `0.35`.
- Ranker takes an injected `embed` callable so tests never download a model.

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
    harness.py                   # Harness Protocol (inventory, compose)
  tests/
    conftest.py                  # fixtures: fake config root, stub embedder
    test_config.py
    test_inventory.py
    test_goal.py
    test_ranker.py
    test_compose.py
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

## Task 6: CLI orchestration (fail-open, --explain, child run)

**Files:**
- Create: `src/smartctx/cli.py`, `src/smartctx/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `main(argv=None) -> int`. Flow: parse own flags (`--explain`), treat the rest as passthrough → `load_config` → `claude_code_inventory` → `detect_goal` (prompt once if confidence < 0.15 and stdin is a TTY and not `-p/--print` in passthrough) → build embedder (`make_model2vec_embed`, fall back to `keyword_embed` on any import/load error) → `Ranker.rank` → `compose`. `--explain` prints goal + kept/dropped + argv and returns 0 without launching. Otherwise `subprocess.run(argv, env)` then delete `tmp_paths` in `finally`; return child returncode. **Any exception in the scoping pipeline → warn to stderr and exec full `["claude", *passthrough]`.**

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py
import json, sys
from pathlib import Path
import smartctx.cli as cli

def _root(tmp_path):
    root = tmp_path / "root"; root.mkdir()
    (root / "settings.json").write_text('{"enabledPlugins": {"figma@x": true}}')
    (root / ".claude.json").write_text('{"mcpServers": {"Gmail": {"command": "g"}}}')
    return root

def test_explain_does_not_launch(tmp_path, monkeypatch, capsys):
    root = _root(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
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

class _RC:
    def __init__(self, code): self.returncode = code
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: smartctx.cli`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/smartctx/cli.py
from __future__ import annotations
import subprocess, sys
from pathlib import Path
from smartctx.config import load_config
from smartctx.inventory import claude_code_inventory
from smartctx.goal import detect_goal
from smartctx.ranker import Ranker, make_model2vec_embed, keyword_embed
from smartctx.compose import compose

def _warn(msg: str) -> None:
    print(f"smartctx: {msg}", file=sys.stderr)

def _build_embed(model_name: str):
    try:
        return make_model2vec_embed(model_name)
    except Exception as exc:  # import error, download failure, etc.
        _warn(f"model unavailable ({exc}); using keyword fallback")
        return keyword_embed

def _scoped_plan(passthrough: list[str], cwd: Path):
    cfg = load_config(cwd=cwd)
    items = claude_code_inventory(cfg.config_root)
    if not items:
        return None, None  # nothing to prune
    goal = detect_goal(cwd)
    if goal.confidence < 0.15 and sys.stdin.isatty() and "-p" not in passthrough and "--print" not in passthrough:
        entered = input(f"smartctx: session goal? [{goal.goal}] ").strip()
        goal_text = entered or goal.goal
    else:
        goal_text = goal.goal
    embed = _build_embed(cfg.model_name)
    sel = Ranker(embed=embed).rank(goal_text, items, cfg.threshold, cfg.always_keep)
    plan = compose(sel.kept, items, cfg.config_root, passthrough)
    return sel, plan

def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    explain = "--explain" in argv
    passthrough = [a for a in argv if a != "--explain"]
    cwd = Path.cwd()
    try:
        sel, plan = _scoped_plan(passthrough, cwd)
    except Exception as exc:
        _warn(f"scoping failed ({exc}); launching full session")
        return subprocess.run(["claude", *passthrough]).returncode
    if plan is None:
        return subprocess.run(["claude", *passthrough]).returncode
    if explain:
        print(f"goal-scoped session\nkept: {[i.id for i in sel.kept]}")
        print(f"dropped: {[(i.id, round(s, 3)) for i, s in sel.dropped]}")
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

Run: `pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite + editable install smoke test**

Run: `pytest -q && pip install -e . && smartctx --explain`
Expected: all tests PASS; `smartctx --explain` prints a plan (or a fallback warning) without launching a nested session.

- [ ] **Step 6: Commit**

```bash
git add src/smartctx/cli.py src/smartctx/__main__.py tests/test_cli.py
git commit -m "feat: CLI orchestration with fail-open and --explain"
```

---

## Task 7: README + alias documentation

**Files:**
- Create: `README.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Write README** covering: what it does (1 paragraph), `pipx install`, the alias integration examples (`CLAUDE_CONFIG_DIR=~/.claude-perso smartctx`), the config chain + a sample `.smartctx/config.toml` with an `always_keep` example, `--explain`, and the fail-open guarantee. Mark the personal always-keep list clearly as an example, not a default.

- [ ] **Step 2: Verify the sample config in the README parses**

Run: `python -c "import tomllib,io; tomllib.loads(open('README.md').read().split('```toml')[1].split('```')[0])"`
Expected: no error.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: usage, alias integration, config, fail-open"
```

---

## Self-Review (completed against spec)

- **Spec coverage:** §2 feasibility levers → Task 5 (compose) + Task 6 (argv). §3 profiles/`CLAUDE_CONFIG_DIR` → Task 1 (`_resolve_root`) + Task 5 (env preserve). §4 components → Tasks 1–6. §6 always-keep config chain → Task 1 + Task 4 (glob). §7 fail-open → Task 6 (all three degrade paths). §8 testing incl. `--explain` → every task + Task 6. §9 distribution → Task 1 (`pyproject`) + Task 7 (README). §11 defaults (0.35, TOML, no vendored model, wrap aliases only) → Tasks 1/4/7.
- **Placeholder scan:** no TBD/TODO; all code steps carry real code.
- **Type consistency:** `Item(id, kind, name, description)`, `Config(config_root, always_keep, threshold, model_name)`, `Selection(kept, dropped)`, `LaunchPlan(argv, env, tmp_paths)` used consistently across Tasks 2/4/5/6.
- **Known follow-ups (not blocking v1):** plugin/mcp descriptions default to id (Task 2 note); standalone-skill pruning stays coarse per spec §10; second harness adapter deferred per spec §10.
