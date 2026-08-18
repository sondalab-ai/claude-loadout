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
