from __future__ import annotations
import json, re
from dataclasses import dataclass
from pathlib import Path
from smartctx.config import default_global_config_path

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

def resolve_mcp_servers(global_config_path: Path, cwd: Path | None) -> dict:
    # Single source of truth for MCP discovery, shared by inventory + launch composition.
    # Merge order = least to most specific (later wins, spec §4.2):
    #   user-global -> projects[cwd] inside .claude.json -> repo .mcp.json.
    global_cfg = _load_json(global_config_path)
    servers = dict(global_cfg.get("mcpServers") or {})
    if cwd is not None:
        project = (global_cfg.get("projects") or {}).get(str(cwd)) or {}
        servers.update(project.get("mcpServers") or {})
        servers.update(_load_json(cwd / ".mcp.json").get("mcpServers") or {})
    return servers

def claude_code_inventory(config_root: Path, cwd: Path | None = None,
                          global_config_path: Path | None = None) -> list[Item]:
    items: list[Item] = []
    settings = _load_json(config_root / "settings.json")
    for pid, enabled in (settings.get("enabledPlugins") or {}).items():
        if enabled:
            items.append(Item(id=pid, kind="plugin", name=pid, description=pid))
    if global_config_path is None:
        global_config_path = default_global_config_path(config_root)
    servers = resolve_mcp_servers(global_config_path, cwd)
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
