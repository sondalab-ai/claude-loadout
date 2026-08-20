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

def test_plugin_description_read_from_manifest(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir()
    (root / "settings.json").write_text('{"enabledPlugins": {"mytool@mkt": true}}')
    install = tmp_path / "install" / "mytool"; (install / ".claude-plugin").mkdir(parents=True)
    (install / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "mytool", "description": "browser automation and e2e testing"}')
    (root / "plugins").mkdir()
    (root / "plugins" / "installed_plugins.json").write_text(
        '{"plugins": {"mytool@mkt": [{"installPath": "%s"}]}}' % install)
    plugin = next(i for i in claude_code_inventory(root) if i.kind == "plugin")
    assert plugin.id == "mytool@mkt"
    assert plugin.description == "browser automation and e2e testing"   # manifest, not id-noise

def test_plugin_description_falls_back_to_id_without_manifest(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir()
    (root / "settings.json").write_text('{"enabledPlugins": {"mytool@mkt": true}}')
    plugin = next(i for i in claude_code_inventory(root) if i.kind == "plugin")
    assert plugin.description == "mytool@mkt"               # no registry -> id fallback, never crashes

def test_inventory_missing_files_returns_empty(tmp_path: Path):
    assert claude_code_inventory(tmp_path) == []

def test_inventory_includes_project_mcp_json(tmp_path: Path):
    root = _root(tmp_path)
    cwd = tmp_path / "repo"; cwd.mkdir()
    (cwd / ".mcp.json").write_text('{"mcpServers": {"Proj": {"command": "p"}}}')
    items = claude_code_inventory(root, cwd)
    by_id = {(i.kind, i.id) for i in items}
    assert ("mcp", "Proj") in by_id                     # project server inventoried (spec §4.2)
    assert ("mcp", "Gmail") in by_id                    # user servers still present

def test_inventory_includes_project_scoped_mcp_in_claude_json(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir()
    cwd = tmp_path / "repo"; cwd.mkdir()
    (root / ".claude.json").write_text(  # real Claude Code layout: MCP scoped under projects[cwd]
        '{"mcpServers": {"Global": {"command": "g"}},'
        f' "projects": {{"{cwd}": {{"mcpServers": {{"Scoped": {{"command": "s"}}}}}}}}}}')
    by_id = {(i.kind, i.id) for i in claude_code_inventory(root, cwd)}
    assert ("mcp", "Scoped") in by_id                   # project-scoped server discovered
    assert ("mcp", "Global") in by_id                   # top-level server still present

def test_inventory_default_profile_reads_home_claude_json(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".claude"; root.mkdir()           # default profile dir
    (tmp_path / ".claude.json").write_text(             # global state sits beside it, not inside
        '{"mcpServers": {"HomeSrv": {"command": "h"}}}')
    by_id = {(i.kind, i.id) for i in claude_code_inventory(root)}
    assert ("mcp", "HomeSrv") in by_id
