from __future__ import annotations
import json, os, sys, tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from ccloadout.config import default_global_config_path
from ccloadout.inventory import resolve_mcp_servers

@dataclass(frozen=True)
class LaunchPlan:
    argv: list[str]
    env: dict[str, str]
    tmp_paths: list[Path]

def _write_tmp_text(prefix: str, text: str, suffix: str = ".txt") -> Path:
    fd, name = tempfile.mkstemp(prefix=f"loadout-{prefix}-", suffix=suffix)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    return Path(name)

def _write_tmp(prefix: str, data: dict) -> Path:
    fd, name = tempfile.mkstemp(prefix=f"loadout-{prefix}-", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    return Path(name)

def compose(kept, all_items, config_root: Path, passthrough: list[str],
            environ: Mapping[str, str] | None = None,
            cwd: Path | None = None,
            global_config_path: Path | None = None,
            launch_config_dir: Path | None = None,
            memory_payload: str | None = None,
            prompt_recall: dict | None = None) -> LaunchPlan:
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
    # Standalone user skills are pruned per-session with the native skillOverrides lever: "off"
    # removes both the skill and its description from context (verified CC 2.1.238). It rides the
    # same --settings overlay, so no --setting-sources / CLAUDE_CONFIG_DIR games and no auth risk.
    dropped_skills = {i.id: "off" for i in all_items
                      if i.kind == "skill" and i.id not in kept_ids}
    settings: dict = {"enabledPlugins": dropped_plugins}
    if prompt_recall:
        # Hooks in this overlay merge with the user's own rather than replacing them (verified),
        # so adding one cannot silence an installed plugin's capture.
        settings["hooks"] = {"UserPromptSubmit": [{"hooks": [
            {"type": "command",
             "command": f"{sys.executable} -m ccloadout.prompt_hook"}]}]}
    if dropped_skills:
        settings["skillOverrides"] = dropped_skills

    mcp_path = _write_tmp("mcp", {"mcpServers": curated})
    settings_path = _write_tmp("settings", settings)
    tmp_paths = [mcp_path, settings_path]
    argv = ["claude", "--strict-mcp-config", "--mcp-config", str(mcp_path),
            "--settings", str(settings_path)]
    if memory_payload:                             # ranked recall rides in as system-prompt text
        memory_path = _write_tmp_text("memory", memory_payload, suffix=".md")
        tmp_paths.append(memory_path)
        argv += ["--append-system-prompt-file", str(memory_path)]
    argv += passthrough
    env = dict(environ)
    if prompt_recall:                              # the hook reads only env + stdin, never argv
        env.update({k: str(v) for k, v in prompt_recall.items()})
    if launch_config_dir is not None:              # propagate a prompted profile to claude itself
        env["CLAUDE_CONFIG_DIR"] = str(launch_config_dir)
    return LaunchPlan(argv=argv, env=env, tmp_paths=tmp_paths)
