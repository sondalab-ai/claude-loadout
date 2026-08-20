import json
from pathlib import Path
from smartctx.inventory import Item
from smartctx.compose import compose

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

def test_compose_session_header_injects_start_hook(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir()
    (root / ".claude.json").write_text('{"mcpServers": {}}')
    item = Item("figma@x", "plugin", "figma", "")
    plan = compose([], [item], root, passthrough=[], session_header="smartctx: scoped out 1 of 1")
    settings = json.loads(Path(plan.argv[plan.argv.index("--settings") + 1]).read_text())
    cmd = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    import shlex
    header_file = Path(shlex.split(cmd)[1])                 # `cat <header_file>`
    assert cmd.startswith("cat ")
    assert header_file.read_text() == "smartctx: scoped out 1 of 1"   # summary carried into the session
    assert header_file in plan.tmp_paths                    # cleaned up after launch

def test_compose_without_header_has_no_hooks(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir()
    (root / ".claude.json").write_text('{"mcpServers": {}}')
    item = Item("figma@x", "plugin", "figma", "")
    plan = compose([], [item], root, passthrough=[])
    settings = json.loads(Path(plan.argv[plan.argv.index("--settings") + 1]).read_text())
    assert "hooks" not in settings                          # no header -> no injected hook
