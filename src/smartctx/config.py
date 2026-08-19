from __future__ import annotations
import os, sys, tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DEFAULT_THRESHOLD = 0.20   # calibrated against potion-base-8M score distribution (see --explain)
DEFAULT_MODEL = "minishlab/potion-base-8M"

def _warn(msg: str) -> None:                       # local, avoids importing cli (cycle)
    print(f"smartctx: {msg}", file=sys.stderr)

@dataclass(frozen=True)
class Config:
    config_root: Path
    global_config_path: Path            # .claude.json: mcpServers + project scopes
    always_keep: tuple[str, ...]
    threshold: float
    model_name: str
    rule_model_path: str | None

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

def load_config(cwd: Path, environ: Mapping[str, str] | None = None) -> Config:
    environ = os.environ if environ is None else environ
    root, global_config_path = _resolve_paths(environ)
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
    rule_model = None
    for layer in layers:
        if "rule_model_path" in layer:
            rule_model = str(layer["rule_model_path"])
    if "SMARTCTX_RULE_MODEL" in environ:
        rule_model = environ["SMARTCTX_RULE_MODEL"]
    if rule_model:
        rule_model = str(Path(rule_model).expanduser())
    if "SMARTCTX_ALWAYS_KEEP" in environ:
        always = tuple(x for x in environ["SMARTCTX_ALWAYS_KEEP"].split(",") if x)
    if "SMARTCTX_THRESHOLD" in environ:
        threshold = float(environ["SMARTCTX_THRESHOLD"])
    return Config(config_root=root, global_config_path=global_config_path,
                  always_keep=always, threshold=threshold,
                  model_name=model, rule_model_path=rule_model)
