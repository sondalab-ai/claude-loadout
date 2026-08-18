from pathlib import Path
from smartctx.inventory import claude_code_inventory, Item

def _root(tmp_path: Path) -> Path:
    root = tmp_path / "root"; root.mkdir()
    (root / "settings.json").write_text(
        '{"enabledPlugins": {"superpowers@x": true, "figma@x": false}}')
    (root / ".claude.json").write_text(
        '{"mcpServers": {"Gmail": {"command": "x"}, "Spotify": {"command": "y"}}}')
    skill = root / "skills" / "astro-visibility"; skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: astro-visibility\ndescription: compute visible sky objects\n---\nbody")
    return root

def test_inventory_collects_enabled_plugins_mcp_and_skills(tmp_path: Path):
    items = claude_code_inventory(_root(tmp_path))
    by_id = {(i.kind, i.id) for i in items}
    assert ("plugin", "superpowers@x") in by_id
    assert ("plugin", "figma@x") not in by_id          # disabled → excluded
    assert ("mcp", "Gmail") in by_id and ("mcp", "Spotify") in by_id
    skill = next(i for i in items if i.kind == "skill")
    assert skill.id == "astro-visibility"
    assert "visible sky" in skill.description

def test_inventory_missing_files_returns_empty(tmp_path: Path):
    assert claude_code_inventory(tmp_path) == []
