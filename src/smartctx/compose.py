from __future__ import annotations
import json, os, shlex, tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from smartctx.config import default_global_config_path
from smartctx.inventory import resolve_mcp_servers

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

def _write_tmp_text(prefix: str, text: str) -> Path:
    fd, name = tempfile.mkstemp(prefix=f"smartctx-{prefix}-", suffix=".txt")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    return Path(name)

def _session_start_hook(header_path: Path) -> dict:
    # A SessionStart hook whose stdout becomes the session's additionalContext — the same
    # mechanism profile hooks use, so the scoping summary shows inside claude instead of
    # flashing past on stderr before the TUI takes the screen.
    return {"SessionStart": [
        {"hooks": [{"type": "command", "command": f"cat {shlex.quote(str(header_path))}"}]}
    ]}

def compose(kept, all_items, config_root: Path, passthrough: list[str],
            environ: Mapping[str, str] | None = None,
            cwd: Path | None = None,
            global_config_path: Path | None = None,
            launch_config_dir: Path | None = None,
            session_header: str | None = None) -> LaunchPlan:
    environ = os.environ if environ is None else environ
    kept_ids = {i.id for i in kept}
    if global_config_path is None:
        global_config_path = default_global_config_path(config_root)
    server_defs = resolve_mcp_servers(global_config_path, cwd)  # same discovery as inventory
    curated = {i.id: server_defs[i.id]             # omit kept servers lacking a real definition
               for i in all_items
               if i.kind == "mcp" and i.id in kept_ids and i.id in server_defs}
    dropped_plugins = {i.id: False for i in all_items
                       if i.kind == "plugin" and i.id not in kept_ids}
    mcp_path = _write_tmp("mcp", {"mcpServers": curated})
    tmp_paths = [mcp_path]
    settings: dict = {"enabledPlugins": dropped_plugins}
    if session_header:                             # surface the scoping summary inside the session
        header_path = _write_tmp_text("header", session_header)
        settings["hooks"] = _session_start_hook(header_path)
        tmp_paths.append(header_path)
    settings_path = _write_tmp("settings", settings)
    tmp_paths.append(settings_path)
    argv = ["claude", "--strict-mcp-config", "--mcp-config", str(mcp_path),
            "--settings", str(settings_path), *passthrough]
    env = dict(environ)
    if launch_config_dir is not None:              # propagate a prompted profile to claude itself
        env["CLAUDE_CONFIG_DIR"] = str(launch_config_dir)
    return LaunchPlan(argv=argv, env=env, tmp_paths=tmp_paths)
