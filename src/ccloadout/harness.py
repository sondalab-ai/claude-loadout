from __future__ import annotations
import os, shlex, sys
from pathlib import Path
from typing import Mapping, Protocol, Sequence
from ccloadout import codex as _codex
from ccloadout.compose import LaunchPlan, write_tmp_json, write_tmp_text
from ccloadout.config import claude_paths, default_global_config_path
from ccloadout.inventory import Item, claude_code_inventory, plugin_mcp_servers, resolve_mcp_servers

class Harness(Protocol):
    """The coding-agent CLI that loadout scopes and launches.

    The launch flow asks it for the installed tools (inventory) and for one run's plan (compose),
    and uses `binary` and `config_env` for the unscoped fallback launch and a prompted profile.
    Session hooks and the memory text go in through compose; each harness installs them its way,
    keeping only the hook events it supports.
    """
    binary: str                                    # the command the launch flow runs
    config_env: str                                # env var that selects the harness's config root
    label: str                                     # the harness's name in prompts and hints
    profile_glob: str | None                       # sibling roots under HOME; None = no prompt
    events: frozenset[str]                         # session hook events this harness installs

    def config_paths(self, environ: Mapping[str, str],
                     root: Path | None = None) -> tuple[Path, Path]: ...

    def inventory(self, config_root: Path, cwd: Path | None = None,
                  config_file: Path | None = None) -> list[Item]: ...

    def compose(self, kept: Sequence[Item], all_items: Sequence[Item], config_root: Path,
                passthrough: list[str],
                environ: Mapping[str, str] | None = None,
                cwd: Path | None = None,
                config_file: Path | None = None,
                profile_dir: Path | None = None,
                memory_payload: str | None = None,
                hooks: dict[str, str] | None = None,
                hook_env: dict | None = None) -> LaunchPlan: ...

class ClaudeHarness:
    """Claude Code: settings, plugins and skills under CLAUDE_CONFIG_DIR, launched as `claude`.

    A run gets a strict curated MCP config and a --settings overlay that turns dropped plugins
    (enabledPlugins) and user skills (skillOverrides) off and adds the session hooks; the memory
    text rides in through --append-system-prompt-file.
    """
    binary = "claude"
    config_env = "CLAUDE_CONFIG_DIR"
    label = "Claude"
    profile_glob = ".claude*"
    events = frozenset({"UserPromptSubmit", "PostToolUse", "Stop"})

    def config_paths(self, environ: Mapping[str, str],
                     root: Path | None = None) -> tuple[Path, Path]:
        """(profile dir, its .claude.json): from root when given, else from the environment.

        A given root derives .claude.json as doctor does; otherwise CLAUDE_CONFIG_DIR or the
        ~/.claude default decides, as Claude Code itself does.
        """
        if root is None:
            return claude_paths(environ)
        return root, default_global_config_path(root)

    def inventory(self, config_root: Path, cwd: Path | None = None,
                  config_file: Path | None = None) -> list[Item]:
        """Installed plugins, MCP servers and user skills of the profile at config_root.

        cwd adds the project's MCP servers; config_file is the profile's .claude.json,
        derived from config_root when not given.
        """
        return claude_code_inventory(config_root, cwd, config_file)

    def compose(self, kept: Sequence[Item], all_items: Sequence[Item], config_root: Path,
                passthrough: list[str],
                environ: Mapping[str, str] | None = None,
                cwd: Path | None = None,
                config_file: Path | None = None,
                profile_dir: Path | None = None,
                memory_payload: str | None = None,
                hooks: dict[str, str] | None = None,
                hook_env: dict | None = None) -> LaunchPlan:
        """The argv, env and temp files for one scoped `claude` run that keeps only `kept`.

        hooks maps a hook event to the module that handles it; hook_env is added to the env the
        hooks read. profile_dir, when set, is passed on as CLAUDE_CONFIG_DIR.
        """
        environ = os.environ if environ is None else environ
        kept_ids = {i.id for i in kept}
        if config_file is None:
            config_file = default_global_config_path(config_root)
        server_defs = resolve_mcp_servers(config_file, cwd)  # same discovery as inventory
        curated = {i.id: server_defs[i.id]             # omit kept servers lacking a real definition
                   for i in all_items
                   if i.kind == "mcp" and i.id in kept_ids and i.id in server_defs}
        hooks = {e: m for e, m in (hooks or {}).items() if e in self.events}
        settings = self._settings(kept_ids, all_items, hooks)
        for i in all_items:                            # strict mode drops plugin servers too: a kept
            if i.kind == "plugin" and i.id in kept_ids:  # plugin brings its own back
                curated.update(plugin_mcp_servers(config_root, i.id))
        mcp_path = write_tmp_json("mcp", {"mcpServers": curated})
        settings_path = write_tmp_json("settings", settings)
        tmp_paths = [mcp_path, settings_path]
        argv = [self.binary, "--strict-mcp-config", "--mcp-config", str(mcp_path),
                "--settings", str(settings_path)]
        if memory_payload:                             # ranked recall rides in as system-prompt text
            memory_path = write_tmp_text("memory", memory_payload, suffix=".md")
            tmp_paths.append(memory_path)
            argv += ["--append-system-prompt-file", str(memory_path)]
        argv += passthrough
        env = dict(environ)
        if hook_env:                                   # hooks read env + stdin, never argv
            env.update({k: str(v) for k, v in hook_env.items()})
        if profile_dir is not None:                    # propagate a prompted profile to claude itself
            env[self.config_env] = str(profile_dir)
        return LaunchPlan(argv=argv, env=env, tmp_paths=tmp_paths, servers=tuple(curated))

    @staticmethod
    def _settings(kept_ids: set[str], all_items: Sequence[Item],
                  hooks: dict[str, str] | None) -> dict:
        """The --settings overlay: dropped plugins and skills off, plus the session hooks.

        Key order (enabledPlugins, hooks, skillOverrides) is part of the file Claude reads, so it
        stays fixed.
        """
        dropped_plugins = {i.id: False for i in all_items
                           if i.kind == "plugin" and i.id not in kept_ids}
        # Standalone user skills are pruned per-session with the native skillOverrides lever: "off"
        # removes both the skill and its description from context (verified CC 2.1.238). It rides
        # the same --settings overlay, so no --setting-sources / CLAUDE_CONFIG_DIR games and no
        # auth risk.
        dropped_skills = {i.id: "off" for i in all_items
                          if i.kind == "skill" and i.id not in kept_ids}
        settings: dict = {"enabledPlugins": dropped_plugins}
        if hooks:
            # Hooks in this overlay merge with the user's own rather than replacing them
            # (verified), so adding one cannot silence an installed plugin's capture. Each is a
            # module run by the interpreter that is running us, addressed absolutely — never a
            # bare name on PATH. The command string is shell-parsed by the harness, so an
            # interpreter path containing a space has to survive quoting; the timeout is explicit
            # because the default is 60 s and these hooks are meant to be imperceptible.
            settings["hooks"] = {event: [{"hooks": [
                {"type": "command", "command": f"{shlex.quote(sys.executable)} -m {module}",
                 "timeout": 5}]}]
                for event, module in hooks.items()}
        if dropped_skills:
            settings["skillOverrides"] = dropped_skills
        return settings

class CodexHarness:
    """OpenAI Codex CLI (experimental): config.toml under CODEX_HOME, launched as `codex`.

    A run gets a temp CODEX_HOME whose config.toml is the user's with only the kept MCP servers,
    dropped skills off, the memory text in developer_instructions and trusted session hooks;
    every other entry links back to the real home. Plugins are not scoped.
    """
    binary = "codex"
    config_env = "CODEX_HOME"
    label = "Codex"
    profile_glob = None
    events = frozenset(_codex.EVENT_LABELS)

    def config_paths(self, environ: Mapping[str, str],
                     root: Path | None = None) -> tuple[Path, Path]:
        """(CODEX_HOME, its config.toml): root when given, else $CODEX_HOME, else ~/.codex."""
        if root is None:
            raw = environ.get(self.config_env)
            root = Path(raw).expanduser() if raw else Path.home() / ".codex"
        return root, root / _codex.CONFIG_FILE

    def inventory(self, config_root: Path, cwd: Path | None = None,
                  config_file: Path | None = None) -> list[Item]:
        """MCP servers (user and trusted project) and skills Codex would load for cwd."""
        return _codex.inventory(config_root, cwd, config_file)

    def compose(self, kept: Sequence[Item], all_items: Sequence[Item], config_root: Path,
                passthrough: list[str],
                environ: Mapping[str, str] | None = None,
                cwd: Path | None = None,
                config_file: Path | None = None,
                profile_dir: Path | None = None,
                memory_payload: str | None = None,
                hooks: dict[str, str] | None = None,
                hook_env: dict | None = None) -> LaunchPlan:
        """The argv `codex *passthrough`, its env and the temp CODEX_HOME for one scoped run.

        Hook events Codex does not support are dropped; profile_dir is unused, since CODEX_HOME
        always points at the temp home.
        """
        hooks = {e: m for e, m in (hooks or {}).items() if e in self.events} or None
        return _codex.compose(self.binary, self.config_env, kept, all_items, config_root,
                              passthrough, os.environ if environ is None else environ, cwd,
                              config_file, memory_payload, hooks, hook_env if hooks else None)

_HARNESSES = {"claude": ClaudeHarness, "codex": CodexHarness}
HARNESS_NAMES = tuple(_HARNESSES)

def harness_for(name: str = "claude") -> Harness:
    """The harness loadout launches, by name: Claude Code by default, or Codex.

    Raises ValueError for an unknown name, so a typo never falls back to a different CLI.
    """
    try:
        return _HARNESSES[name]()
    except KeyError:
        raise ValueError(f"unknown harness {name!r}") from None
