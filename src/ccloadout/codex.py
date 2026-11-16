from __future__ import annotations
import copy, hashlib, json, os, shlex, sys, tempfile, tomllib
from pathlib import Path
from typing import Mapping, Sequence
from ccloadout.inventory import Item, _frontmatter
from ccloadout import tomlw

CONFIG_FILE = "config.toml"
STATE_DIRS = ("sessions", "log")
HOOK_TIMEOUT_S = 5
EVENT_LABELS = {"UserPromptSubmit": "user_prompt_submit", "PostToolUse": "post_tool_use"}

def read_toml(path: Path) -> dict:
    """The parsed TOML file at path, or {} when it is missing.

    A malformed file raises tomllib.TOMLDecodeError, so the launch flow falls back to an
    unscoped session instead of writing a config Codex would read differently.
    """
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}

def _project_root(cwd: Path, config: dict) -> Path:
    markers = config.get("project_root_markers", [".git"])
    if not markers:
        return cwd
    for d in (cwd, *cwd.parents):
        if any((d / m).exists() for m in markers):
            return d
    return cwd

def _project_dirs(cwd: Path, config: dict) -> list[Path]:
    chain = [cwd, *cwd.parents]
    root = _project_root(cwd, config)
    return chain[:chain.index(root) + 1] if root in chain else [cwd]

def _trusted(cwd: Path, config: dict) -> bool:
    projects = config.get("projects") or {}
    for d in (cwd, *cwd.parents):
        level = (projects.get(str(d)) or {}).get("trust_level")
        if level:
            return level == "trusted"
    return False

def project_servers(cwd: Path | None, config: dict) -> dict[str, dict]:
    """MCP servers from the trusted project's .codex/config.toml files, closest file winning.

    Codex walks from the project root to cwd and loads only a trusted project's layers, so an
    untrusted project contributes nothing.
    """
    if cwd is None or not _trusted(cwd, config):
        return {}
    servers: dict[str, dict] = {}
    for d in reversed(_project_dirs(cwd, config)):
        servers.update(read_toml(d / ".codex" / CONFIG_FILE).get("mcp_servers") or {})
    return servers

def _skill_dirs(cwd: Path | None, config: dict) -> list[Path]:
    project = [d / ".agents" / "skills" for d in _project_dirs(cwd, config)] if cwd else []
    return project + [Path.home() / ".agents" / "skills", Path("/etc/codex/skills")]

def _enabled(spec) -> bool:
    return isinstance(spec, dict) and spec.get("enabled", True) is not False

def inventory(codex_home: Path, cwd: Path | None = None,
              config_file: Path | None = None) -> list[Item]:
    """MCP servers and skills Codex would load for cwd under codex_home.

    Servers come from the user config and a trusted project's config; disabled ones are left
    out. Skills come from the documented .agents/skills folders and /etc/codex/skills.
    """
    config = read_toml(config_file or codex_home / CONFIG_FILE)
    servers = {k: v for k, v in (config.get("mcp_servers") or {}).items() if _enabled(v)}
    servers.update({k: v for k, v in project_servers(cwd, config).items() if _enabled(v)})
    items = [Item(id=name, kind="mcp", name=name, description=name) for name in servers]
    seen: set[str] = set()
    for folder in _skill_dirs(cwd, config):
        for md in sorted(folder.glob("*/SKILL.md")) if folder.is_dir() else []:
            fm = _frontmatter(md.read_text(errors="ignore"))
            sid = fm.get("name") or md.parent.name
            if sid in seen:
                continue
            seen.add(sid)
            desc = fm.get("description", sid)
            items.append(Item(id=sid, kind="skill", name=sid, description=desc,
                              footprint=len(f"- {sid}: {desc}")))
    return items

def fingerprint(value: dict) -> str:
    """Codex's `version_for_toml`: sha256 of the value as compact JSON with sorted keys.

    Codex keys a hook's persisted trust by this hash of the hook's identity, so loadout can
    trust its own hooks without the bypass flag; a mismatch only leaves a hook untrusted.
    """
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()

def hook_hash(event: str, command: str, timeout: int) -> str:
    """The trust hash Codex computes for one command hook with no matcher or status message.

    Mirrors codex-rs hooks `hook_hash`: the normalised identity, where Codex has filled in the
    timeout and async, and drops unset fields.
    """
    return fingerprint({"event_name": EVENT_LABELS[event],
                        "hooks": [{"type": "command", "command": command, "timeout": timeout,
                                   "async": False}]})

def _rekey(state: dict, homes: Sequence[Path], new_home: Path) -> dict:
    prefixes = {f"{h}{os.sep}" for h in homes}
    out = {}
    for key, value in state.items():
        old = next((p for p in prefixes if key.startswith(p)), None)
        out[f"{new_home}{os.sep}{key[len(old):]}" if old else key] = value
    return out

def _add_hooks(config: dict, hooks: dict[str, str], tmp_home: Path) -> None:
    table = config.setdefault("hooks", {})
    state = table.get("state") or {}
    for event, module in hooks.items():
        command = f"{shlex.quote(sys.executable)} -m {module}"
        groups = table.setdefault(event, [])
        key = f"{tmp_home / CONFIG_FILE}:{EVENT_LABELS[event]}:{len(groups)}:0"
        groups.append({"hooks": [{"type": "command", "command": command,
                                  "timeout": HOOK_TIMEOUT_S}]})
        state[key] = {"trusted_hash": hook_hash(event, command, HOOK_TIMEOUT_S)}
    if state:
        table["state"] = state

def scoped_config(config: dict, kept_ids: set[str], all_items: Sequence[Item],
                  project: dict[str, dict], memory_payload: str | None,
                  hooks: dict[str, str] | None, real_home: Path, tmp_home: Path) -> dict:
    """The user config Codex reads for one run: kept servers, skills off, memory, hooks.

    User servers are cut to the kept ones; a dropped project server is turned off with its own
    definition plus enabled = false, since the project layer still loads it. Dropped skills go
    off by name, after the user's own entries so they win. The memory text is appended to
    developer_instructions. The user's hook trust entries are re-keyed to the temp home.
    """
    out = copy.deepcopy(config)
    user = {k: v for k, v in (config.get("mcp_servers") or {}).items() if k in kept_ids}
    off = {k: {**v, "enabled": False} for k, v in project.items()
           if k not in kept_ids and _enabled(v)}
    out["mcp_servers"] = {**user, **off}
    dropped = [i.id for i in all_items if i.kind == "skill" and i.id not in kept_ids]
    if dropped:
        skills = out.setdefault("skills", {})
        skills["config"] = [*(skills.get("config") or []),
                            *({"name": sid, "enabled": False} for sid in dropped)]
    if memory_payload:
        prior = out.get("developer_instructions")
        out["developer_instructions"] = f"{prior}\n\n{memory_payload}" if prior else memory_payload
    state = (out.get("hooks") or {}).get("state")
    if isinstance(state, dict):                     # the user's trust follows the moved config
        out["hooks"]["state"] = _rekey(state, (real_home, real_home.resolve()), tmp_home)
    if hooks:
        _add_hooks(out, hooks, tmp_home)
    return out

def build_home(real_home: Path) -> Path:
    """A fresh temp CODEX_HOME holding a symlink to every real entry except config.toml.

    sessions/ and log/ are created in the real home first, so a run's transcripts and logs land
    there and outlive the temp dir. Returned resolved, as Codex canonicalises CODEX_HOME and
    keys hook trust by that path.
    """
    real_home.mkdir(parents=True, exist_ok=True)
    for name in STATE_DIRS:
        (real_home / name).mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="loadout-codex-")).resolve()
    for entry in sorted(real_home.iterdir()):
        if entry.name != CONFIG_FILE:
            (tmp / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
    return tmp

def compose(binary: str, config_env: str, kept: Sequence[Item], all_items: Sequence[Item],
            codex_home: Path, passthrough: list[str], environ: Mapping[str, str],
            cwd: Path | None, config_file: Path | None, memory_payload: str | None,
            hooks: dict[str, str] | None, hook_env: dict | None):
    """The LaunchPlan for one scoped `codex` run under a temp CODEX_HOME.

    The plan's tmp_dirs holds the temp home; removing it leaves the real home untouched.
    """
    from ccloadout.compose import LaunchPlan
    kept_ids = {i.id for i in kept}
    config = read_toml(config_file or codex_home / CONFIG_FILE)
    project = project_servers(cwd, config)
    home = build_home(codex_home)
    scoped = scoped_config(config, kept_ids, all_items, project, memory_payload, hooks,
                           codex_home, home)
    (home / CONFIG_FILE).write_text(tomlw.dumps(scoped))
    env = dict(environ)
    if hook_env:
        env.update({k: str(v) for k, v in hook_env.items()})
    env[config_env] = str(home)
    servers = tuple(k for k in {**(config.get("mcp_servers") or {}), **project}
                    if k in kept_ids)
    return LaunchPlan(argv=[binary, *passthrough], env=env, tmp_paths=[], servers=servers,
                      tmp_dirs=(home,))
