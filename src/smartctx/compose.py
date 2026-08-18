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
