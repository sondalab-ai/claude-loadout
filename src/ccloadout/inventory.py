from __future__ import annotations
import json, re
from dataclasses import dataclass
from pathlib import Path
from ccloadout.config import default_global_config_path

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

def _unquote(raw: str) -> str:
    return raw.strip().strip("\"'")

def _inline_list(val: str) -> list[str]:
    inner = val[1:-1].strip()
    return [_unquote(part) for part in inner.split(",") if part.strip()] if inner else []

def _scalar(val: str) -> str | list[str]:
    return _inline_list(val) if val.startswith("[") and val.endswith("]") else _unquote(val)

# A key with no spaces, i.e. what a real YAML key looks like in these files. Prose that happens to
# contain a colon ("Triggers on: do X") must not be mistaken for a mapping — before this guard a
# plain multi-line description parsed into a dict and the ranker scored the skill on garbage.
_NESTED_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*:(\s|$)")

def _is_mapping(filled: list[str], base: int) -> bool:
    at_base = [ln.strip() for ln in filled if len(ln) - len(ln.lstrip()) == base]
    return bool(at_base) and all(_NESTED_KEY.match(ln) for ln in at_base)

def _nested(body: list[str]) -> dict[str, str | list[str]] | list[str] | str:
    # One level of a nested block: either a `- item` sequence or `key: value` pairs.
    # Deeper indentation is consumed but not parsed — the store schema is one level deep.
    filled = [ln for ln in body if ln.strip()]
    if not filled:
        return {}
    base = min(len(ln) - len(ln.lstrip()) for ln in filled)
    if filled[0].strip().startswith("- "):
        return [_unquote(ln.strip()[2:]) for ln in filled
                if len(ln) - len(ln.lstrip()) == base and ln.strip().startswith("- ")]
    if not _is_mapping(filled, base):               # a plain multi-line scalar, folded like YAML does
        return " ".join(" ".join(ln.strip() for ln in filled).split())
    out: dict[str, str | list[str]] = {}
    for ln in filled:
        if len(ln) - len(ln.lstrip()) != base or ":" not in ln:
            continue
        k, _, v = ln.partition(":")
        out[k.strip()] = _scalar(v.strip())
    return out

def _frontmatter(text: str) -> dict[str, str | list[str] | dict[str, str | list[str]]]:
    # Minimal YAML: top-level `key: value` pairs, block scalars (`>` folded / `|` literal),
    # inline lists (`[a, b]`), and one level of nested mapping or sequence. Enough for SKILL.md
    # name/description — including folded descriptions — and for the memory/decision stores,
    # whose lifecycle keys live under a nested `metadata:` block.
    m = _FRONTMATTER.match(text)
    if not m:
        return {}
    lines = m.group(1).splitlines()
    out: dict[str, str | list[str] | dict[str, str | list[str]]] = {}
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]; i += 1
        if ":" not in line or line[:1] in (" ", "\t", "#"):   # only unindented keys, skip comments
            continue
        k, _, v = line.partition(":")
        key, val = k.strip(), v.strip()
        if val in _BLOCK_SCALARS or not val:                  # gather the indented/blank block body
            body = []
            while i < n and (not lines[i].strip() or lines[i][0] in " \t"):
                body.append(lines[i]); i += 1
            if not val:                                       # nested mapping or sequence
                out[key] = _nested(body)
            elif val[0] == ">":                               # folded: newlines become spaces
                out[key] = " ".join(" ".join(ln.strip() for ln in body).split())
            else:                                             # literal: keep line breaks
                out[key] = "\n".join(ln.strip() for ln in body).strip("\n")
        else:
            out[key] = _scalar(val)
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

def _servers_in(data: dict) -> dict:
    # Plugins ship server maps two ways: bare ({"name": {...}}) or wrapped ({"mcpServers": {...}}).
    if not isinstance(data, dict):
        return {}
    inner = data.get("mcpServers") if isinstance(data.get("mcpServers"), dict) else data
    return {k: v for k, v in inner.items() if isinstance(v, dict)}

def _rooted(value, root: Path):
    # Claude Code runs plugin servers with ${CLAUDE_PLUGIN_ROOT} expanded and from the plugin's
    # directory; under --mcp-config neither happens, so both are made explicit here.
    if isinstance(value, dict):
        return {k: _rooted(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [_rooted(v, root) for v in value]
    if isinstance(value, str):
        value = value.replace("${CLAUDE_PLUGIN_ROOT}", str(root))
        return str(root / value[2:]) if value.startswith("./") else value
    return value

def plugin_mcp_servers(config_root: Path, pid: str) -> dict[str, dict]:
    """MCP servers an installed plugin provides, keyed as Claude Code names them in tool names.

    `--strict-mcp-config` drops plugin servers along with everything else, so a kept plugin
    would lose them; compose puts these back into the curated config. The key
    `plugin_<plugin>_<server>` reproduces the harness's own `mcp__plugin_<plugin>_<server>__*`
    tool names (verified on Claude Code 2.1.287), so permission allowlists keep matching.
    """
    install = _installed_plugin_paths(config_root).get(pid)
    if install is None:
        return {}
    servers = _servers_in(_load_json(install / ".mcp.json"))
    declared = _load_json(install / ".claude-plugin" / "plugin.json").get("mcpServers")
    if isinstance(declared, str):                   # a path to a server file, relative to the plugin
        declared = _load_json(install / declared)
    if isinstance(declared, dict):
        servers.update(_servers_in(declared))
    plugin = pid.split("@", 1)[0]
    return {f"plugin_{plugin}_{name}": _rooted(spec, install) for name, spec in servers.items()}

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
