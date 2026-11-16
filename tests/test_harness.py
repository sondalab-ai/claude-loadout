import json, shlex, sys
from pathlib import Path
import pytest
from ccloadout.harness import ClaudeHarness, harness_for
from ccloadout.inventory import Item, claude_code_inventory

def _sample(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir()
    (root / ".claude.json").write_text(json.dumps(
        {"mcpServers": {"Gmail": {"command": "g"}, "Spotify": {"command": "s"}}}))
    items = [Item("Gmail", "mcp", "Gmail", ""), Item("Spotify", "mcp", "Spotify", ""),
             Item("figma@x", "plugin", "figma", ""), Item("sp@x", "plugin", "sp", ""),
             Item("pdf", "skill", "pdf", ""), Item("tdd", "skill", "tdd", "")]
    kept = [items[0], items[3], items[5]]
    return root, items, kept

def _run(compose_fn, tmp_path: Path):
    root, items, kept = _sample(tmp_path)
    plan = compose_fn(kept, items, root, ["-c", "--model", "x"], environ={"A": "1"},
                      cwd=tmp_path, launch_config_dir=root, memory_payload="remember",
                      hooks={"Stop": "ccloadout.stop_hook"}, hook_env={"LOADOUT_N": 3})
    try:
        return plan, [p.read_text() for p in plan.tmp_paths]
    finally:
        for p in plan.tmp_paths:
            p.unlink()

def test_claude_compose_pins_the_exact_argv_and_overlays(tmp_path: Path):
    plan, (mcp, settings, memory) = _run(ClaudeHarness().compose, tmp_path)
    mcp_path, settings_path, memory_path = plan.tmp_paths
    assert plan.argv == ["claude", "--strict-mcp-config", "--mcp-config", str(mcp_path),
                         "--settings", str(settings_path),
                         "--append-system-prompt-file", str(memory_path),
                         "-c", "--model", "x"]
    assert mcp == '{"mcpServers": {"Gmail": {"command": "g"}}}'
    hook = f"{shlex.quote(sys.executable)} -m ccloadout.stop_hook"
    assert settings == json.dumps(
        {"enabledPlugins": {"figma@x": False},
         "hooks": {"Stop": [{"hooks": [{"type": "command", "command": hook, "timeout": 5}]}]},
         "skillOverrides": {"pdf": "off"}})
    assert memory == "remember"
    assert plan.env == {"A": "1", "LOADOUT_N": "3", "CLAUDE_CONFIG_DIR": str(tmp_path / "root")}
    assert plan.servers == ("Gmail",)

def test_harness_for_defaults_to_claude_code():
    harness = harness_for()
    assert isinstance(harness, ClaudeHarness) and isinstance(harness_for("claude"), ClaudeHarness)
    assert (harness.binary, harness.config_env) == ("claude", "CLAUDE_CONFIG_DIR")

def test_harness_for_rejects_an_unknown_name():
    with pytest.raises(ValueError, match="codex"):
        harness_for("codex")

def test_claude_inventory_matches_the_claude_code_reader(tmp_path: Path):
    root, _, _ = _sample(tmp_path)
    (root / "skills" / "pdf").mkdir(parents=True)
    (root / "skills" / "pdf" / "SKILL.md").write_text("---\nname: pdf\ndescription: PDFs\n---\n")
    got = ClaudeHarness().inventory(root, tmp_path, root / ".claude.json")
    assert got == claude_code_inventory(root, tmp_path, root / ".claude.json")
    assert {(i.id, i.kind) for i in got} == {("Gmail", "mcp"), ("Spotify", "mcp"), ("pdf", "skill")}
