from pathlib import Path
from smartctx.config import load_config

def test_env_overrides_repo_and_defaults(tmp_path: Path):
    root = tmp_path / "cfgroot"; root.mkdir()
    (root / "smartctx").mkdir()
    (root / "smartctx" / "config.toml").write_text(
        'always_keep = ["a"]\nthreshold = 0.5\n')
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / ".smartctx").mkdir()
    (repo / ".smartctx" / "config.toml").write_text('always_keep = ["b"]\n')
    env = {"CLAUDE_CONFIG_DIR": str(root), "SMARTCTX_ALWAYS_KEEP": "c,d",
           "SMARTCTX_THRESHOLD": "0.9"}
    cfg = load_config(cwd=repo, environ=env)
    assert cfg.config_root == root
    assert cfg.always_keep == ("c", "d")   # env wins
    assert cfg.threshold == 0.9            # env wins

def test_defaults_when_nothing_set(tmp_path: Path):
    cfg = load_config(cwd=tmp_path, environ={"CLAUDE_CONFIG_DIR": str(tmp_path)})
    assert cfg.always_keep == ()
    assert cfg.threshold == 0.24
    assert cfg.model_name == "minishlab/potion-base-8M"

def test_config_root_override_wins_over_env(tmp_path: Path):
    picked = tmp_path / "perso"; picked.mkdir()
    (picked / "smartctx").mkdir()
    (picked / "smartctx" / "config.toml").write_text('always_keep = ["p"]\n')
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "other")}   # env points elsewhere
    cfg = load_config(cwd=tmp_path, environ=env, config_root_override=picked)
    assert cfg.config_root == picked
    assert cfg.always_keep == ("p",)                       # config read from override root
    assert cfg.global_config_path == picked / ".claude.json"

def test_config_root_override_default_profile_uses_home_json(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    default_root = tmp_path / ".claude"; default_root.mkdir()
    cfg = load_config(cwd=tmp_path, environ={}, config_root_override=default_root)
    assert cfg.config_root == default_root
    assert cfg.global_config_path == tmp_path / ".claude.json"   # HOME-root, not <dir>/.claude.json
