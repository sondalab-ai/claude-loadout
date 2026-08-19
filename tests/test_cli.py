import json, sys
from pathlib import Path
import smartctx.cli as cli

class _RC:
    def __init__(self, code): self.returncode = code

def _root(tmp_path):
    root = tmp_path / "root"; root.mkdir()
    (root / "settings.json").write_text('{"enabledPlugins": {"figma@x": true}}')
    (root / ".claude.json").write_text('{"mcpServers": {"Gmail": {"command": "g"}}}')
    return root

def test_explain_does_not_launch(tmp_path, monkeypatch, capsys):
    _root(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "root"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_model2vec_embed", lambda name: cli.keyword_embed)
    launched = {"ran": False}
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: launched.__setitem__("ran", True))
    rc = cli.main(["--explain"])
    assert rc == 0 and launched["ran"] is False
    assert "kept" in capsys.readouterr().out.lower()

def test_fail_open_launches_full_claude_on_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "root"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "claude_code_inventory",
                        lambda root: (_ for _ in ()).throw(RuntimeError("boom")))
    captured = {}
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda argv, **k: captured.__setitem__("argv", argv) or _RC(0))
    rc = cli.main(["-c"])
    assert captured["argv"] == ["claude", "-c"]

def _drop_rule_root(tmp_path):
    root = _root(tmp_path)
    (root / "smartctx").mkdir()
    (root / "smartctx" / "rules.toml").write_text(
        '[[rule]]\ntarget = "Gmail"\nnl = "never"\n'
        '[rule.predicate]\naction = "always_drop"\nmatch = []\nmatch_mode = "any"\n')
    return root

def _capture_mcp_overlay(monkeypatch):
    captured = {}
    def _fake_run(argv, **k):
        mcp_path = argv[argv.index("--mcp-config") + 1]
        captured["mcp"] = json.loads(Path(mcp_path).read_text())
        return _RC(0)
    monkeypatch.setattr(cli.subprocess, "run", _fake_run)
    return captured

def test_forced_drop_rule_excludes_item(tmp_path, monkeypatch):
    root = _drop_rule_root(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_model2vec_embed", lambda name: cli.keyword_embed)
    captured = _capture_mcp_overlay(monkeypatch)
    rc = cli.main([])
    assert rc == 0
    assert "Gmail" not in captured["mcp"]["mcpServers"]   # always_drop rule curated it out

def test_always_keep_config_overrides_drop_rule(tmp_path, monkeypatch):
    root = _drop_rule_root(tmp_path)                       # Gmail carries an always_drop rule
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.setenv("SMARTCTX_ALWAYS_KEEP", "Gmail")    # config always_keep must win (spec §12)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_model2vec_embed", lambda name: cli.keyword_embed)
    captured = _capture_mcp_overlay(monkeypatch)
    rc = cli.main([])
    assert rc == 0
    assert "Gmail" in captured["mcp"]["mcpServers"]        # config pin beats the drop rule

def test_rules_subcommand_authors_rule(tmp_path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_build_compiler",
        lambda cfg: (lambda prompt: '{"action":"always_keep","match":[],"match_mode":"any"}'))
    replies = iter(["keep this plugin", "keep this server"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(replies, ""))
    rc = cli.main(["rules"])
    assert rc == 0
    assert "always_keep" in (root / "smartctx" / "rules.toml").read_text()
