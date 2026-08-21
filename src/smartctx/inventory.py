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
_BLOCK_SCALARS = {">", "|", ">-", "|-", ">+", "|+"}

def _frontmatter(text: str) -> dict[str, str]:
    # Minimal YAML: top-level `key: value` pairs, plus block scalars (`>` folded / `|` literal)
    # whose body is the following more-indented lines. Enough for SKILL.md name/description —
    # notably descriptions written as a folded `>` block, which the ranker embeds and the UI shows.
    m = _FRONTMATTER.match(text)
    if not m:
        return {}
    lines = m.group(1).splitlines()
    out: dict[str, str] = {}
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]; i += 1
        if ":" not in line or line[:1] in (" ", "\t", "#"):   # only unindented keys, skip comments
            continue
        k, _, v = line.partition(":")
        key, val = k.strip(), v.strip()
        if val in _BLOCK_SCALARS:                             # gather the indented/blank block body
            body = []
            while i < n and (not lines[i].strip() or lines[i][0] in " \t"):
                body.append(lines[i].strip()); i += 1
            if val[0] == ">":                                 # folded: newlines become spaces
                out[key] = " ".join(" ".join(body).split())
            else:                                             # literal: keep line breaks
                out[key] = "\n".join(body).strip("\n")
        else:
            out[key] = val.strip("\"'")
    return out

def _installed_plugin_paths(config_root: Path) -> dict[str, Path]:
    # pid ("name@marketplace") -> install dir, from Claude Code's plugin registry.
    data = _load_json(config_root / "plugins" / "installed_plugins.json")
    out: dict[str, Path] = {}
    for pid, entries in (data.get("plugins") or {}).items():
        for e in entries or []:
            p = e.get("installPath")
            if p:
                out[pid] = Path(p)
                break
    return out

def _plugin_description(install_path: Path | None, pid: str) -> str:
    # Real manifest description gives the ranker a meaningful signal; the raw
    # id (e.g. "name@marketplace") is noise that matches unrelated goals.
    if install_path is None:
        return pid
    desc = _load_json(install_path / ".claude-plugin" / "plugin.json").get("description")
    return desc.strip() if isinstance(desc, str) and desc.strip() else pid

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
    plugin_paths = _installed_plugin_paths(config_root)
    for pid, enabled in (settings.get("enabledPlugins") or {}).items():
        if enabled:
            desc = _plugin_description(plugin_paths.get(pid), pid)
            items.append(Item(id=pid, kind="plugin", name=pid, description=desc))
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
