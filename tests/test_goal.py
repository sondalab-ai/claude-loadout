from pathlib import Path
from ccloadout.goal import detect_goal, write_goal_cache

def test_cache_takes_precedence(tmp_path: Path):
    write_goal_cache(tmp_path, "frontend work")
    r = detect_goal(tmp_path)
    assert r.goal == "frontend work" and r.source == "cache" and r.confidence == 1.0

def test_cache_write_self_ignores_but_spares_authored_files(tmp_path: Path):
    write_goal_cache(tmp_path, "frontend work")
    ignore = (tmp_path / ".loadout" / ".gitignore").read_text()
    assert "goal" in ignore                            # machine state is ignored
    assert "config.toml" not in ignore and "rules.toml" not in ignore   # authored files stay committable

def test_cache_write_preserves_existing_gitignore(tmp_path: Path):
    d = tmp_path / ".loadout"; d.mkdir()
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

def test_metadata_description_from_pyproject(tmp_path: Path):
    d = tmp_path / "daos"; d.mkdir()
    (d / "pyproject.toml").write_text('[project]\nname="daos"\ndescription="deterministic agentic system"\n')
    r = detect_goal(d)
    assert r.source == "docs"
    assert "deterministic agentic system" in r.goal and "python project" in r.goal
    assert r.confidence == 0.7                             # 0.3 python + 0.4 docs bonus

def test_metadata_description_from_package_json(tmp_path: Path):
    d = tmp_path / "introspect"; d.mkdir()
    (d / "package.json").write_text('{"name":"introspect","description":"inspect running React apps"}')
    r = detect_goal(d)
    assert r.source == "docs" and "inspect running React apps" in r.goal

def test_readme_blurb_strips_badges(tmp_path: Path):
    d = tmp_path / "cooltool"; d.mkdir()
    (d / "README.md").write_text("# CoolTool\n[![ci](a.svg)](b)\n\nDoes X and Y for Z.\n\nMore.\n")
    r = detect_goal(d)
    assert r.source == "docs"
    assert "CoolTool" in r.goal and "Does X and Y for Z" in r.goal
    assert "svg" not in r.goal and "ci" not in r.goal      # badge line dropped

def test_readme_without_prose_falls_back_to_signals(tmp_path: Path):
    d = tmp_path / "badgeonly"; d.mkdir()
    (d / "README.md").write_text("![badge](x.svg)\n")
    r = detect_goal(d)
    assert r.source == "signals" and r.goal == "badgeonly"  # README present but no blurb -> name
    assert abs(r.confidence - 0.1) < 1e-9                    # only the README-present bump

def test_metadata_wins_over_readme(tmp_path: Path):
    d = tmp_path / "proj"; d.mkdir()
    (d / "pyproject.toml").write_text('[project]\ndescription="curated one liner"\n')
    (d / "README.md").write_text("# Readme Title\n\nreadme paragraph\n")
    r = detect_goal(d)
    assert "curated one liner" in r.goal and "Readme Title" not in r.goal

def test_description_is_capped(tmp_path: Path):
    d = tmp_path / "wordy"; d.mkdir()
    (d / "pyproject.toml").write_text(f'[project]\ndescription="{"x " * 200}"\n')
    r = detect_goal(d)
    assert len(r.goal) <= 200 + len(" · python project")    # blurb capped, tech tag still appended

def test_readme_keeps_bold_tagline_drops_bullets(tmp_path: Path):
    d = tmp_path / "tool"; d.mkdir()
    (d / "README.md").write_text(
        "# Tool\n\n**Start sessions with only what you need.**\n\n"
        "- bullet one\n- bullet two\n")
    r = detect_goal(d)
    assert "Start sessions with only what you need" in r.goal   # bold tagline kept
    assert "bullet" not in r.goal and "*" not in r.goal          # bullets + markers stripped

def test_lowercase_readme_bump_is_consistent(tmp_path: Path):
    d = tmp_path / "low"; d.mkdir()
    (d / "readme.md").write_text("![badge](x)\n")     # lowercase, no prose -> blurb None
    r = detect_goal(d)
    assert r.source == "signals"                       # bump path agrees a README exists
    assert abs(r.confidence - 0.1) < 1e-9

def test_oversized_readme_is_skipped(tmp_path: Path):
    d = tmp_path / "big"; d.mkdir()
    (d / "pyproject.toml").write_text("[project]\nname='x'\n")   # no description
    (d / "README.md").write_text("# Title\n\n" + ("word " * 5000))   # >8KB body
    r = detect_goal(d)
    assert r.source == "docs" and r.goal.startswith("Title")     # reads only the head, still works
