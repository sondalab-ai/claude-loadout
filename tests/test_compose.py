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
