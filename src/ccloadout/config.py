from __future__ import annotations
import os, sys, tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping
from ccloadout.savings import DEFAULT_TOKEN_COSTS

DEFAULT_THRESHOLD = 0.24   # potion-base-8M; re-centered from 0.20 when plugin items gained real
                           # manifest descriptions (richer text shifts scores up) — holds the prior
                           # keep-rate constant rather than pruning less. See inventory._plugin_description.
DEFAULT_MODEL = "minishlab/potion-base-8M"

def _warn(msg: str) -> None:                       # local, avoids importing cli (cycle)
    print(f"claude-loadout: {msg}", file=sys.stderr)

@dataclass(frozen=True)
class MemoryConfig:
    enabled: bool = False        # opt-in: without it the launcher behaves exactly as before
    budget_tokens: int = 800     # ceiling on the resident recall payload
    threshold: float = DEFAULT_THRESHOLD
    min_entries: int = 1         # below this, inject nothing at all — not even the usage contract
    promote_after: int = 3       # deliveries in a repo after which an entry is pinned into recall
    decay_days: int = 90         # no delivery for this long demotes an entry (never deletes it)
    decay_factor: float = 0.5
    git_tracked: bool = True     # new entries land in <repo>/docs/memory and travel in git
    prompt_recall: bool = False  # re-rank the store against each prompt (adds a hook to the session)
    prompt_recall_max: int = 2   # entries the prompt hook may add per turn
    prompt_timeout_ms: int = 300 # the hook's own wall-clock ceiling; it exits 0 when it fires
    debt_patterns: tuple[str, ...] = ("TODO(loadout)",)   # markers a PostToolUse hook watches for

@dataclass(frozen=True)
class Config:
    config_root: Path
    global_config_path: Path            # .claude.json: mcpServers + project scopes
    always_keep: tuple[str, ...]
    threshold: float
    model_name: str
    rule_model_path: str | None
    token_costs: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_TOKEN_COSTS))
    memory: MemoryConfig = field(default_factory=MemoryConfig)

def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except OSError:                                 # absent file is the normal case
        return {}
    except tomllib.TOMLDecodeError as exc:         # spec §7: parse error -> keep all + warn
        _warn(f"config parse error in {path} ({exc}); using defaults")
        return {}

def _resolve_paths(environ: Mapping[str, str]) -> tuple[Path, Path]:
    # Claude Code: settings/skills live in CLAUDE_CONFIG_DIR (or ~/.claude by default),
    # but .claude.json sits at <CLAUDE_CONFIG_DIR>/.claude.json — or ~/.claude.json (HOME
    # root, beside the dir) when the default profile is used.
    raw = environ.get("CLAUDE_CONFIG_DIR")
    if raw:
        base = Path(raw).expanduser()
        return base, base / ".claude.json"
    return Path.home() / ".claude", Path.home() / ".claude.json"

def default_global_config_path(config_root: Path) -> Path:
    # Derive the .claude.json path for a profile dir when the env context is unknown
    # (e.g. doctor enumerating sibling profiles). The default ~/.claude profile keeps
    # its global state in ~/.claude.json, others in <profile>/.claude.json.
    return Path.home() / ".claude.json" if config_root == Path.home() / ".claude" \
        else config_root / ".claude.json"

def load_config(cwd: Path, environ: Mapping[str, str] | None = None,
                config_root_override: Path | None = None) -> Config:
    environ = os.environ if environ is None else environ
    if config_root_override is not None:                # explicit profile choice (cli prompt)
        root = config_root_override
        global_config_path = default_global_config_path(root)
    else:
        root, global_config_path = _resolve_paths(environ)
    layers = [_read_toml(root / "loadout" / "config.toml"),
              _read_toml(cwd / ".loadout" / "config.toml")]
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
    token_costs = dict(DEFAULT_TOKEN_COSTS)
    for layer in layers:
        if isinstance(layer.get("token_costs"), dict):
            token_costs.update({str(k): int(v) for k, v in layer["token_costs"].items()})
    rule_model = None
    for layer in layers:
        if "rule_model_path" in layer:
            rule_model = str(layer["rule_model_path"])
    if "LOADOUT_RULE_MODEL" in environ:
        rule_model = environ["LOADOUT_RULE_MODEL"]
    if rule_model:
        rule_model = str(Path(rule_model).expanduser())
    memory = MemoryConfig()
    for layer in layers:
        table = layer.get("memory")
        if isinstance(table, dict):
            memory = replace(memory, **_memory_fields(table))
    if "LOADOUT_ALWAYS_KEEP" in environ:
        always = tuple(x for x in environ["LOADOUT_ALWAYS_KEEP"].split(",") if x)
    if "LOADOUT_THRESHOLD" in environ:
        threshold = float(environ["LOADOUT_THRESHOLD"])
    return Config(config_root=root, global_config_path=global_config_path,
                  always_keep=always, threshold=threshold,
                  model_name=model, rule_model_path=rule_model,
                  token_costs=token_costs, memory=memory)


def _memory_fields(table: dict) -> dict:
    # Coerce to the declared type and warn instead of trusting the file: a `prompt_timeout_ms`
    # written as 0.3 used to reach the hook's env verbatim and kill recall for the whole session.
    out: dict = {}
    for key, value in table.items():
        field = MemoryConfig.__dataclass_fields__.get(key)
        if field is None:
            continue
        try:
            if key == "debt_patterns":
                out[key] = tuple(str(x) for x in value)
            elif field.type == "bool":
                out[key] = bool(value)
            elif field.type == "int":
                out[key] = int(value)
            elif field.type == "float":
                out[key] = float(value)
            else:
                out[key] = value
        except (TypeError, ValueError):
            _warn(f"[memory] {key} = {value!r} is not a {field.type}; using the default")
    return out

def set_memory_enabled(repo: Path, enabled: bool) -> Path:
    """Flip `[memory] enabled` in a repository's own config, leaving everything else alone.

    Surgical on purpose: the file may already carry a seeded threshold, model and other
    sections, and a rewrite would silently drop them. The key is placed inside the [memory]
    table and nowhere else — an `enabled` key belonging to another section is not ours to touch.
    """
    path = repo / ".loadout" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    value = "true" if enabled else "false"
    lines = path.read_text().splitlines() if path.exists() else []
    start = next((i for i, ln in enumerate(lines) if ln.strip() == "[memory]"), None)
    if start is None:
        block = ["", "[memory]", f"enabled = {value}"]
        path.write_text("\n".join([*lines, *block]).lstrip("\n") + "\n")
        return path
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")),
               len(lines))
    at = next((i for i in range(start + 1, end) if lines[i].split("=")[0].strip() == "enabled"),
              None)
    if at is None:
        lines.insert(start + 1, f"enabled = {value}")
    else:
        lines[at] = f"enabled = {value}"
    path.write_text("\n".join(lines) + "\n")
    return path
