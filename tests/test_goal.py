from pathlib import Path
from smartctx.goal import detect_goal, write_goal_cache

def test_cache_takes_precedence(tmp_path: Path):
    write_goal_cache(tmp_path, "frontend work")
    r = detect_goal(tmp_path)
    assert r.goal == "frontend work" and r.source == "cache" and r.confidence == 1.0

def test_cache_write_self_ignores_but_spares_authored_files(tmp_path: Path):
    write_goal_cache(tmp_path, "frontend work")
    ignore = (tmp_path / ".smartctx" / ".gitignore").read_text()
    assert "goal" in ignore                            # machine state is ignored
    assert "config.toml" not in ignore and "rules.toml" not in ignore   # authored files stay committable

def test_cache_write_preserves_existing_gitignore(tmp_path: Path):
    d = tmp_path / ".smartctx"; d.mkdir()
    (d / ".gitignore").write_text("custom\n")
    write_goal_cache(tmp_path, "x")
    assert (d / ".gitignore").read_text() == "custom\n"   # never clobber a user-authored ignore

def test_signals_from_markers(tmp_path: Path):
    d = tmp_path / "my-astro-tool"; d.mkdir()
    (d / "pyproject.toml").write_text("[project]\nname='x'")
    r = detect_goal(d)
    assert "my-astro-tool" in r.goal and "python" in r.goal.lower()
    assert r.confidence > 0.0 and r.source == "signals"

def test_empty_dir_low_confidence(tmp_path: Path):
    r = detect_goal(tmp_path)
    assert r.confidence == 0.0 and r.source == "none"
