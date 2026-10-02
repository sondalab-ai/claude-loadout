from pathlib import Path
from ccloadout.inventory import claude_code_inventory, Item, _frontmatter

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

def test_skill_folded_block_description_is_parsed(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir()
    skill = root / "skills" / "astro"; skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(                      # description as a YAML folded (>) block
        "---\nname: astro\ndescription: >\n  Computes which deep-sky objects are\n"
        "  visible from a location tonight.\nmetadata: 1\n---\nbody")
    skill_item = next(i for i in claude_code_inventory(root) if i.kind == "skill")
    assert skill_item.description == "Computes which deep-sky objects are visible from a location tonight."

def test_skill_literal_block_description_keeps_lines(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir()
    skill = root / "skills" / "s"; skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: s\ndescription: |\n  line one\n  line two\n---\nbody")
    skill_item = next(i for i in claude_code_inventory(root) if i.kind == "skill")
    assert skill_item.description == "line one\nline two"

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

def test_skill_footprint_is_its_listing_line(tmp_path: Path):
    root = tmp_path / "root"; (root / "skills" / "s").mkdir(parents=True)
    (root / "skills" / "s" / "SKILL.md").write_text("---\nname: s\ndescription: does a thing\n---\n")
    skill = next(i for i in claude_code_inventory(root) if i.kind == "skill")
    assert skill.footprint == len("- s: does a thing")

def test_plugin_footprint_sums_its_skills_commands_and_agents(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir()
    (root / "settings.json").write_text('{"enabledPlugins": {"kit@mkt": true}}')
    install = tmp_path / "install" / "kit"; (install / ".claude-plugin").mkdir(parents=True)
    (install / ".claude-plugin" / "plugin.json").write_text('{"description": "never shown", "skills": "./custom/"}')
    (install / "custom" / "plan").mkdir(parents=True)
    (install / "custom" / "plan" / "SKILL.md").write_text("---\nname: plan\ndescription: plans work\n---\n")
    (install / "commands").mkdir(); (install / "commands" / "go.md").write_text("---\ndescription: runs it\n---\n")
    (install / "agents").mkdir(); (install / "agents" / "rev.md").write_text("---\nname: rev\ndescription: reviews\n---\n")
    (root / "plugins").mkdir()
    (root / "plugins" / "installed_plugins.json").write_text(
        '{"plugins": {"kit@mkt": [{"installPath": "%s"}]}}' % install)
    plugin = next(i for i in claude_code_inventory(root) if i.kind == "plugin")
    assert plugin.footprint == sum(len(s) for s in
                                   ("- kit:plan: plans work", "- kit:go: runs it", "- kit:rev: reviews"))

def test_plugin_footprint_handles_a_single_skill_path_and_hooks_only_plugins(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir()
    (root / "settings.json").write_text('{"enabledPlugins": {"one@m": true, "hooks@m": true, "gone@m": true}}')
    one = tmp_path / "one"; (one / ".claude-plugin").mkdir(parents=True)
    (one / ".claude-plugin" / "plugin.json").write_text('{"skills": "./skills/solo"}')
    (one / "skills" / "solo").mkdir(parents=True)
    (one / "skills" / "solo" / "SKILL.md").write_text("---\nname: solo\ndescription: alone\n---\n")
    hooks = tmp_path / "hooks"; (hooks / "hooks").mkdir(parents=True)   # ships hooks, lists nothing
    (root / "plugins").mkdir()
    (root / "plugins" / "installed_plugins.json").write_text(
        '{"plugins": {"one@m": [{"installPath": "%s"}], "hooks@m": [{"installPath": "%s"}]}}'
        % (one, hooks))
    by_id = {i.id: i for i in claude_code_inventory(root) if i.kind == "plugin"}
    assert by_id["one@m"].footprint == len("- one:solo: alone")
    assert by_id["hooks@m"].footprint == 0                  # known to list nothing
    assert by_id["gone@m"].footprint is None                # not installed: unknown

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

# --- frontmatter: nested blocks and lists (memory/decision files) -------------

def test_frontmatter_parses_nested_mapping_one_level():
    text = ("---\nname: skill-scoping\ndescription: how it works\n"
            "metadata:\n  node_type: memory\n  type: project\n  uses: 3\n---\nbody")
    fm = _frontmatter(text)
    assert fm["name"] == "skill-scoping"
    assert fm["metadata"] == {"node_type": "memory", "type": "project", "uses": "3"}

def test_frontmatter_parses_inline_list():
    text = "---\nid: 2026-06-08-routing\ntags: [agents, routing, multi-user]\n---\nbody"
    fm = _frontmatter(text)
    assert fm["tags"] == ["agents", "routing", "multi-user"]

def test_frontmatter_parses_quoted_and_empty_inline_list():
    fm = _frontmatter('---\ntags: ["a", \'b\']\nanchors: []\n---\nbody')
    assert fm["tags"] == ["a", "b"]
    assert fm["anchors"] == []

def test_frontmatter_parses_block_sequence():
    text = "---\nanchors:\n  - src/cli.py\n  - src/goal.py\n---\nbody"
    assert _frontmatter(text)["anchors"] == ["src/cli.py", "src/goal.py"]

def test_frontmatter_nested_block_ignores_deeper_levels():
    text = "---\nmetadata:\n  type: project\n  extra:\n    deep: 1\n---\nbody"
    assert _frontmatter(text)["metadata"] == {"type": "project", "extra": ""}

def test_frontmatter_scalar_and_block_scalar_behaviour_unchanged():
    assert _frontmatter("---\nmetadata: 1\n---\n")["metadata"] == "1"
    assert _frontmatter("---\nd: >\n  a\n  b\n---\n")["d"] == "a b"
    assert _frontmatter("---\nd: |\n  a\n  b\n---\n")["d"] == "a\nb"

def test_frontmatter_keeps_a_plain_multiline_scalar_as_text():
    # Legal YAML that is not a mapping: an indented block of prose. Treating it as one produced a
    # dict, which then reached Item.description and was embedded by the ranker.
    text = ("---\nname: astro\ndescription:\n"
            "  Use when the user asks for X. Triggers on: \"do X\", \"make X\".\n"
            "  Also handles Y.\n---\nbody")
    d = _frontmatter(text)["description"]
    assert isinstance(d, str)
    assert d == 'Use when the user asks for X. Triggers on: "do X", "make X". Also handles Y.'

def test_frontmatter_still_reads_a_real_nested_mapping():
    text = "---\nname: n\nmetadata:\n  node_type: memory\n  uses: 2\n---\nbody"
    assert _frontmatter(text)["metadata"] == {"node_type": "memory", "uses": "2"}

def test_inventory_never_hands_the_ranker_a_non_string_description(tmp_path: Path):
    root = tmp_path / "root"; skill = root / "skills" / "s"; skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: s\ndescription:\n  plain prose with no colon at all\n  over two lines\n---\nx")
    item = next(i for i in claude_code_inventory(root) if i.kind == "skill")
    assert isinstance(item.description, str) and "plain prose" in item.description
