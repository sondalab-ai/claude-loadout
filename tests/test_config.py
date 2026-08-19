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
    assert cfg.threshold == 0.20
    assert cfg.model_name == "minishlab/potion-base-8M"
