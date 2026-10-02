import json
from pathlib import Path
from ccloadout.inventory import Item
from ccloadout.compose import compose

def _settings_of(plan):
    return json.loads(Path(plan.argv[plan.argv.index("--settings") + 1]).read_text())

def test_compose_writes_overlays_and_argv(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir()
    (root / ".claude.json").write_text(json.dumps(
        {"mcpServers": {"Gmail": {"command": "g"}, "Spotify": {"command": "s"}}}))
    all_items = [Item("Gmail", "mcp", "Gmail", ""), Item("Spotify", "mcp", "Spotify", ""),
                 Item("figma@x", "plugin", "figma", ""), Item("superpowers@x", "plugin", "sp", "")]
    kept = [all_items[0], all_items[3]]  # keep Gmail + superpowers; drop Spotify + figma
    plan = compose(kept, all_items, root, passthrough=["-c"], environ={"CLAUDE_CONFIG_DIR": str(root)})
    assert plan.argv[0] == "claude"
    assert "--strict-mcp-config" in plan.argv and "-c" == plan.argv[-1]
    mcp_path = Path(plan.argv[plan.argv.index("--mcp-config") + 1])
    settings_path = Path(plan.argv[plan.argv.index("--settings") + 1])
    mcp = json.loads(mcp_path.read_text())
    assert set(mcp["mcpServers"]) == {"Gmail"} and mcp["mcpServers"]["Gmail"] == {"command": "g"}
    settings = json.loads(settings_path.read_text())
    assert settings["enabledPlugins"] == {"figma@x": False}   # only dropped plugin
    assert plan.env["CLAUDE_CONFIG_DIR"] == str(root)
    assert set(plan.tmp_paths) == {mcp_path, settings_path}

def _installed(root: Path, tmp_path: Path, pid: str) -> Path:
    install = tmp_path / "plugins" / pid.split("@")[0]
    (install / ".claude-plugin").mkdir(parents=True)
    reg = root / "plugins"; reg.mkdir(parents=True, exist_ok=True)
    path = reg / "installed_plugins.json"
    data = json.loads(path.read_text()) if path.exists() else {"plugins": {}}
    data["plugins"][pid] = [{"installPath": str(install)}]
    path.write_text(json.dumps(data))
    return install

def _mcp_of(plan):
    return json.loads(Path(plan.argv[plan.argv.index("--mcp-config") + 1]).read_text())["mcpServers"]

def test_kept_plugin_keeps_its_mcp_servers_in_every_shape(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir()
    bare = _installed(root, tmp_path, "playwright@o")
    (bare / ".mcp.json").write_text(json.dumps({"playwright": {"command": "npx", "args": ["@pw/mcp"]}}))
    wrapped = _installed(root, tmp_path, "ctx@o")
    (wrapped / ".mcp.json").write_text(json.dumps({"mcpServers": {"ctx": {"type": "http", "url": "u"}}}))
    inline = _installed(root, tmp_path, "ds@o")
    (inline / ".claude-plugin" / "plugin.json").write_text(json.dumps(
        {"mcpServers": {"playwright": {"command": "npx"},
                        "check": {"command": "node", "args": ["./scripts/check.mjs"],
                                  "env": {"ROOT": "${CLAUDE_PLUGIN_ROOT}/data"}}}}))
    items = [Item(p, "plugin", p, "") for p in ("playwright@o", "ctx@o", "ds@o")]
    plan = compose(items, items, root, passthrough=[])
    mcp = _mcp_of(plan)
    assert set(mcp) == {"plugin_playwright_playwright", "plugin_ctx_ctx",
                        "plugin_ds_playwright", "plugin_ds_check"}   # same server name, no clash
    assert mcp["plugin_ds_check"]["args"] == [str(inline / "scripts" / "check.mjs")]
    assert mcp["plugin_ds_check"]["env"] == {"ROOT": f"{inline}/data"}
    assert "plugin_playwright_playwright" in plan.servers

def test_dropped_plugin_brings_no_mcp_servers(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir()
    install = _installed(root, tmp_path, "playwright@o")
    (install / ".mcp.json").write_text(json.dumps({"playwright": {"command": "npx"}}))
    item = Item("playwright@o", "plugin", "playwright@o", "")
    plan = compose([], [item], root, passthrough=[])
    assert _mcp_of(plan) == {} and plan.servers == ()

def test_compose_emits_project_scoped_server_def(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir()
    cwd = tmp_path / "repo"; cwd.mkdir()
    (root / ".claude.json").write_text(json.dumps(   # server defined only under projects[cwd]
        {"projects": {str(cwd): {"mcpServers": {"Scoped": {"command": "z"}}}}}))
    item = Item("Scoped", "mcp", "Scoped", "")
    plan = compose([item], [item], root, passthrough=[], cwd=cwd)
    mcp_path = Path(plan.argv[plan.argv.index("--mcp-config") + 1])
    mcp = json.loads(mcp_path.read_text())
    assert mcp["mcpServers"] == {"Scoped": {"command": "z"}}   # kept server survives to launch
    mcp_path.unlink(); Path(plan.argv[plan.argv.index("--settings") + 1]).unlink()

def test_compose_marks_dropped_skills_off_via_skilloverrides(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir()
    items = [Item("astro", "skill", "astro", ""), Item("mind", "skill", "mind", "")]
    plan = compose([items[0]], items, root, passthrough=[])    # keep astro, drop mind
    settings = _settings_of(plan)
    assert settings["skillOverrides"] == {"mind": "off"}       # only the dropped skill turned off
    assert "--setting-sources" not in plan.argv and "--plugin-dir" not in plan.argv  # native lever only
    assert set(plan.tmp_paths) == {Path(plan.argv[plan.argv.index("--mcp-config") + 1]),
                                   Path(plan.argv[plan.argv.index("--settings") + 1])}

def test_compose_no_skilloverrides_when_no_skill_dropped(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir()
    items = [Item("astro", "skill", "astro", ""), Item("figma@x", "plugin", "figma", "")]
    plan = compose([items[0]], items, root, passthrough=[])    # keep the skill, drop only a plugin
    settings = _settings_of(plan)
    assert "skillOverrides" not in settings                    # nothing to override
    assert settings["enabledPlugins"] == {"figma@x": False}

# --- memory payload injection -------------------------------------------------

def test_memory_payload_is_passed_as_a_system_prompt_file(tmp_path: Path):
    plan = compose([], [], tmp_path, [], environ={}, cwd=tmp_path,
                   global_config_path=tmp_path / ".claude.json",
                   memory_payload="<claude-loadout-memory>\nnotes\n</claude-loadout-memory>")
    assert "--append-system-prompt-file" in plan.argv
    path = Path(plan.argv[plan.argv.index("--append-system-prompt-file") + 1])
    assert path.read_text().startswith("<claude-loadout-memory>")
    assert path in plan.tmp_paths                       # cleaned up with the rest of the launch

def test_no_memory_payload_leaves_the_argv_untouched(tmp_path: Path):
    plan = compose([], [], tmp_path, [], environ={}, cwd=tmp_path,
                   global_config_path=tmp_path / ".claude.json")
    assert "--append-system-prompt-file" not in plan.argv

def test_empty_memory_payload_injects_nothing(tmp_path: Path):
    plan = compose([], [], tmp_path, [], environ={}, cwd=tmp_path,
                   global_config_path=tmp_path / ".claude.json", memory_payload="")
    assert "--append-system-prompt-file" not in plan.argv

def test_passthrough_still_comes_last(tmp_path: Path):
    plan = compose([], [], tmp_path, ["--model", "opus"], environ={}, cwd=tmp_path,
                   global_config_path=tmp_path / ".claude.json", memory_payload="notes")
    assert plan.argv[-2:] == ["--model", "opus"]

def test_prompt_recall_adds_a_hook_and_its_environment(tmp_path: Path):
    plan = compose([], [], tmp_path, [], environ={}, cwd=tmp_path,
                   global_config_path=tmp_path / ".claude.json",
                   hooks={"UserPromptSubmit": "ccloadout.prompt_hook"},
                   hook_env={"LOADOUT_CONFIG_ROOT": "/x", "LOADOUT_PROMPT_MAX": 2})
    hook = _settings_of(plan)["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    assert hook["type"] == "command" and "ccloadout.prompt_hook" in hook["command"]
    assert plan.env["LOADOUT_CONFIG_ROOT"] == "/x" and plan.env["LOADOUT_PROMPT_MAX"] == "2"

def test_two_hooks_are_installed_side_by_side(tmp_path: Path):
    plan = compose([], [], tmp_path, [], environ={}, cwd=tmp_path,
                   global_config_path=tmp_path / ".claude.json",
                   hooks={"UserPromptSubmit": "ccloadout.prompt_hook",
                          "PostToolUse": "ccloadout.debt_hook"},
                   hook_env={"LOADOUT_DEBT_PATTERNS": "TODO(loadout)"})
    events = _settings_of(plan)["hooks"]
    assert set(events) == {"UserPromptSubmit", "PostToolUse"}
    assert "debt_hook" in events["PostToolUse"][0]["hooks"][0]["command"]
    assert plan.env["LOADOUT_DEBT_PATTERNS"] == "TODO(loadout)"

def test_no_prompt_recall_means_no_hooks_key(tmp_path: Path):
    plan = compose([], [], tmp_path, [], environ={}, cwd=tmp_path,
                   global_config_path=tmp_path / ".claude.json")
    assert "hooks" not in _settings_of(plan)

def test_hook_command_is_quoted_and_time_limited(tmp_path: Path):
    plan = compose([], [], tmp_path, [], environ={}, cwd=tmp_path,
                   global_config_path=tmp_path / ".claude.json",
                   hooks={"PostToolUse": "ccloadout.debt_hook"}, hook_env={})
    hook = _settings_of(plan)["hooks"]["PostToolUse"][0]["hooks"][0]
    assert hook["timeout"] == 5
    import shlex, sys
    assert shlex.split(hook["command"])[0] == sys.executable   # survives a path with spaces
