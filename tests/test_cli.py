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
    out = capsys.readouterr().out.lower()
    assert "kept" in out and "goal:" in out and "threshold:" in out   # --explain surfaces the goal

def test_fail_open_launches_full_claude_on_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "root"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "claude_code_inventory",
                        lambda root, cwd=None: (_ for _ in ()).throw(RuntimeError("boom")))
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
    monkeypatch.setattr(cli, "_interactive", lambda p: True)   # pytest stdin is non-TTY
    monkeypatch.setattr(cli, "_build_compiler",
        lambda cfg: (lambda prompt: '{"action":"always_keep","match":[],"match_mode":"any"}'))
    replies = iter(["keep this plugin", "keep this server"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(replies, ""))
    rc = cli.main(["rules"])
    assert rc == 0
    assert "always_keep" in (root / "smartctx" / "rules.toml").read_text()

def test_explain_shows_rule_forced_drop(tmp_path, monkeypatch, capsys):
    root = _drop_rule_root(tmp_path)                       # Gmail carries an always_drop rule
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_model2vec_embed", lambda name: cli.keyword_embed)
    rc = cli.main(["--explain"])
    assert rc == 0
    out = capsys.readouterr().out
    dropped_line = next(l for l in out.splitlines() if l.startswith("dropped:"))
    assert "Gmail" in dropped_line and "rule" in dropped_line   # forced drop surfaced with sentinel

def test_rules_subcommand_noop_without_tty(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "root"))
    monkeypatch.chdir(tmp_path)                            # pytest stdin is non-TTY
    rc = cli.main(["rules"])
    assert rc == 0
    assert "interactive" in capsys.readouterr().err.lower()

def test_launch_elicitation_survives_eof_on_piped_stdin(tmp_path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.setenv("SMARTCTX_THRESHOLD", "0.99")      # force everything into dropped
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_model2vec_embed", lambda name: cli.keyword_embed)
    monkeypatch.setattr(cli, "_interactive", lambda p: True)
    def _eof(*a, **k):
        raise EOFError
    monkeypatch.setattr("builtins.input", _eof)            # piped stdin exhausted
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: _RC(0))
    rc = cli.main([])                                      # must not raise EOFError
    assert rc == 0

def test_doctor_prints_guidance_and_does_not_launch(tmp_path, monkeypatch, capsys):
    root = _root(tmp_path)                             # one mcp + one plugin
    monkeypatch.setenv("HOME", str(tmp_path))          # hermetic profile discovery
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_model2vec_embed", lambda name: cli.keyword_embed)
    launched = {"ran": False}
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: launched.__setitem__("ran", True))
    rc = cli.main(["doctor"])
    assert rc == 0 and launched["ran"] is False
    out = capsys.readouterr().out
    assert "claude profiles: 1 profile" in out and f"{root} (active)" in out
    assert "inventory: 1 mcp, 1 plugin, 0 skills" in out     # singular/plural
    assert "--explain" in out and "alias claude=" in out    # init guidance present
    assert "keyword fallback" in out                        # model state reported

def test_doctor_enumerates_multiple_profiles(tmp_path, monkeypatch, capsys):
    for name, plugin in ((".claude", "a@x"), (".claude-perso", "b@x")):
        prof = tmp_path / name; prof.mkdir()
        prof.joinpath("settings.json").write_text(f'{{"enabledPlugins": {{"{plugin}": true}}}}')
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude-perso"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_model2vec_embed", lambda name: cli.keyword_embed)
    rc = cli.main(["doctor"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "claude profiles: 2 profiles" in out
    assert f"{tmp_path / '.claude-perso'} (active)" in out   # env-selected profile marked active
    assert f"{tmp_path / '.claude'}\n" in out                # sibling listed, not marked active

def test_doctor_reports_external_model_over_bundled_dir(tmp_path, monkeypatch, capsys):
    root = _root(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    repo_cfg = tmp_path / ".smartctx"; repo_cfg.mkdir()
    repo_cfg.joinpath("config.toml").write_text('model_name = "minishlab/potion-base-32M"')  # not bundled id
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_model2vec_embed", lambda name: (lambda texts: None))  # loads OK
    rc = cli.main(["doctor"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "external" in out and "bundled copy" not in out  # vendored dir exists but model isn't it

def test_doctor_survives_inventory_error(tmp_path, monkeypatch, capsys):
    root = _root(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "claude_code_inventory",
                        lambda root, cwd=None: (_ for _ in ()).throw(RuntimeError("boom")))
    rc = cli.main(["doctor"])
    assert rc == 0
    assert "inventory: unavailable" in capsys.readouterr().out

def _skill_root(tmp_path):
    root = _root(tmp_path)
    skill = root / "skills" / "astro"; skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: astro\ndescription: sky imaging\n---\nbody")
    return root

def test_dropped_skill_does_not_elicit(tmp_path, monkeypatch):
    root = _skill_root(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.setenv("SMARTCTX_THRESHOLD", "0.99")      # force everything into dropped
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_model2vec_embed", lambda name: cli.keyword_embed)
    monkeypatch.setattr(cli, "_interactive", lambda p: True)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "")
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: _RC(0))
    rc = cli.main([])
    assert rc == 0
    joined = " ".join(prompts)
    assert "astro" not in joined                          # un-prunable skill never elicited
    assert "Gmail" in joined and "figma@x" in joined      # prunable kinds are elicited

def test_mcp_json_server_kept_appears_in_overlay(tmp_path, monkeypatch):
    root = _root(tmp_path)
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {"Proj": {"command": "p"}}}')
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.setenv("SMARTCTX_ALWAYS_KEEP", "Proj")   # pin the project server so it survives
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_model2vec_embed", lambda name: cli.keyword_embed)
    captured = _capture_mcp_overlay(monkeypatch)
    rc = cli.main([])
    assert rc == 0
    assert captured["mcp"]["mcpServers"]["Proj"] == {"command": "p"}   # real def from ./.mcp.json

def test_launch_elicitation_keeps_item_end_to_end(tmp_path, monkeypatch):
    root = _root(tmp_path)                                # Gmail mcp, figma@x plugin, no rules
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.setenv("SMARTCTX_THRESHOLD", "0.99")     # force everything into dropped
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_model2vec_embed", lambda name: cli.keyword_embed)
    monkeypatch.setattr(cli, "_interactive", lambda p: True)
    monkeypatch.setattr(cli, "_build_compiler",
        lambda cfg: (lambda prompt: '{"action":"always_keep","match":[],"match_mode":"any"}'))
    monkeypatch.setattr("builtins.input", lambda prompt="": "keep it")
    captured = _capture_mcp_overlay(monkeypatch)
    rc = cli.main([])
    assert rc == 0
    assert "Gmail" in captured["mcp"]["mcpServers"]       # elicited always_keep re-kept the server
    assert "always_keep" in (root / "smartctx" / "rules.toml").read_text()
