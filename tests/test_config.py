from pathlib import Path
from ccloadout.config import load_config

def test_env_overrides_repo_and_defaults(tmp_path: Path):
    root = tmp_path / "cfgroot"; root.mkdir()
    (root / "loadout").mkdir()
    (root / "loadout" / "config.toml").write_text(
        'always_keep = ["a"]\nthreshold = 0.5\n')
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / ".loadout").mkdir()
    (repo / ".loadout" / "config.toml").write_text('always_keep = ["b"]\n')
    env = {"CLAUDE_CONFIG_DIR": str(root), "LOADOUT_ALWAYS_KEEP": "c,d",
           "LOADOUT_THRESHOLD": "0.9"}
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
    (picked / "loadout").mkdir()
    (picked / "loadout" / "config.toml").write_text('always_keep = ["p"]\n')
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

def test_memory_section_is_read_and_defaults_to_off(tmp_path: Path):
    root = tmp_path / "root"; (root / "loadout").mkdir(parents=True)
    assert load_config(tmp_path, environ={"CLAUDE_CONFIG_DIR": str(root)}).memory.enabled is False
    (root / "loadout" / "config.toml").write_text(
        "[memory]\nenabled = true\nbudget_tokens = 400\nmin_entries = 3\n")
    mem = load_config(tmp_path, environ={"CLAUDE_CONFIG_DIR": str(root)}).memory
    assert mem.enabled is True and mem.budget_tokens == 400 and mem.min_entries == 3
    assert mem.threshold == 0.24                     # untouched keys keep their default

def test_repo_layer_overrides_profile_memory_settings(tmp_path: Path):
    root, cwd = tmp_path / "root", tmp_path / "cwd"
    (root / "loadout").mkdir(parents=True); (cwd / ".loadout").mkdir(parents=True)
    (root / "loadout" / "config.toml").write_text("[memory]\nenabled = true\nbudget_tokens = 400\n")
    (cwd / ".loadout" / "config.toml").write_text("[memory]\nbudget_tokens = 100\n")
    mem = load_config(cwd, environ={"CLAUDE_CONFIG_DIR": str(root)}).memory
    assert mem.budget_tokens == 100 and mem.enabled is True

# --- turning recall on for a repository ---------------------------------------

def test_enabling_creates_the_repo_config(tmp_path: Path):
    from ccloadout.config import set_memory_enabled
    path = set_memory_enabled(tmp_path, True)
    assert path == tmp_path / ".loadout" / "config.toml"
    assert load_config(tmp_path, environ={"CLAUDE_CONFIG_DIR": str(tmp_path / "r")}).memory.enabled

def test_enabling_preserves_existing_settings(tmp_path: Path):
    from ccloadout.config import set_memory_enabled
    d = tmp_path / ".loadout"; d.mkdir()
    (d / "config.toml").write_text('# seeded\nthreshold = 0.3\nmodel_name = "x"\n')
    set_memory_enabled(tmp_path, True)
    cfg = load_config(tmp_path, environ={"CLAUDE_CONFIG_DIR": str(tmp_path / "r")})
    assert cfg.threshold == 0.3 and cfg.model_name == "x" and cfg.memory.enabled
    assert "# seeded" in (d / "config.toml").read_text()

def test_disabling_flips_the_existing_key_in_place(tmp_path: Path):
    from ccloadout.config import set_memory_enabled
    d = tmp_path / ".loadout"; d.mkdir()
    (d / "config.toml").write_text(
        "[memory]\nenabled = true\nbudget_tokens = 400\n\n[other]\nenabled = true\n")
    set_memory_enabled(tmp_path, False)
    text = (d / "config.toml").read_text()
    assert "[memory]\nenabled = false\nbudget_tokens = 400" in text
    assert text.endswith("[other]\nenabled = true\n")      # the other section is untouched
    cfg = load_config(tmp_path, environ={"CLAUDE_CONFIG_DIR": str(tmp_path / "r")})
    assert cfg.memory.enabled is False and cfg.memory.budget_tokens == 400

def test_enabling_when_memory_section_is_not_last(tmp_path: Path):
    from ccloadout.config import set_memory_enabled
    d = tmp_path / ".loadout"; d.mkdir()
    (d / "config.toml").write_text("[memory]\nbudget_tokens = 400\n\n[token_costs]\nmcp = 900\n")
    set_memory_enabled(tmp_path, True)
    cfg = load_config(tmp_path, environ={"CLAUDE_CONFIG_DIR": str(tmp_path / "r")})
    assert cfg.memory.enabled and cfg.memory.budget_tokens == 400
    assert cfg.token_costs["mcp"] == 900                   # the key landed in the right section
