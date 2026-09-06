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
                   prompt_recall={"LOADOUT_CONFIG_ROOT": "/x", "LOADOUT_PROMPT_MAX": 2})
    hook = _settings_of(plan)["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    assert hook["type"] == "command" and "ccloadout.prompt_hook" in hook["command"]
    assert plan.env["LOADOUT_CONFIG_ROOT"] == "/x" and plan.env["LOADOUT_PROMPT_MAX"] == "2"

def test_no_prompt_recall_means_no_hooks_key(tmp_path: Path):
    plan = compose([], [], tmp_path, [], environ={}, cwd=tmp_path,
                   global_config_path=tmp_path / ".claude.json")
    assert "hooks" not in _settings_of(plan)
