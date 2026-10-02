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

def _pyproject(d: Path, desc: str) -> None:
    (d / "pyproject.toml").write_text(f'[project]\ndescription = "{desc}"\n')

def test_inferred_goal_refreshes_when_its_inputs_change(tmp_path: Path):
    _pyproject(tmp_path, "an old purpose")
    write_goal_cache(tmp_path, detect_goal(tmp_path, use_cache=False).goal)
    _pyproject(tmp_path, "a new purpose")
    r = detect_goal(tmp_path)
    assert "new purpose" in r.goal and r.source == "refreshed"
    assert "new purpose" in (tmp_path / ".loadout" / "goal").read_text()   # written back
    assert detect_goal(tmp_path).source == "cache"                         # and current again

def test_a_goal_the_user_chose_is_never_refreshed(tmp_path: Path):
    _pyproject(tmp_path, "an old purpose")
    write_goal_cache(tmp_path, "my own words")         # differs from inference: the user's
    _pyproject(tmp_path, "a new purpose")
    r = detect_goal(tmp_path)
    assert r.goal == "my own words" and r.source == "cache"

def test_legacy_cache_that_differs_is_kept_as_the_users(tmp_path: Path):
    # A pre-sidecar cache can't say who wrote it, and `init` let users override a confident
    # inference; replacing it on upgrade could destroy a goal someone typed. `update` re-infers.
    _pyproject(tmp_path, "the real purpose")
    d = tmp_path / ".loadout"; d.mkdir()
    (d / "goal").write_text("my own words from init\n")    # written by a release with no sidecar
    r = detect_goal(tmp_path)
    assert r.goal == "my own words from init" and r.source == "cache"
    assert "origin=user" in (d / "goal.meta").read_text()
    _pyproject(tmp_path, "yet another purpose")             # and it stays kept afterwards
    assert detect_goal(tmp_path).goal == "my own words from init"

def test_legacy_cache_that_matches_inference_is_adopted_as_inferred(tmp_path: Path):
    _pyproject(tmp_path, "the real purpose")
    d = tmp_path / ".loadout"; d.mkdir()
    (d / "goal").write_text(detect_goal(tmp_path, use_cache=False).goal + "\n")
    detect_goal(tmp_path)
    assert "origin=inferred" in (d / "goal.meta").read_text()
    _pyproject(tmp_path, "a new purpose")
    assert "new purpose" in detect_goal(tmp_path).goal      # from now on it refreshes

def test_weak_inference_keeps_the_cached_goal(tmp_path: Path):
    d = tmp_path / ".loadout"; d.mkdir()
    (d / "goal").write_text("typed at the prompt\n")   # nothing on disk to infer from
    assert detect_goal(tmp_path).goal == "typed at the prompt"

def test_goal_file_stays_one_line(tmp_path: Path):
    write_goal_cache(tmp_path, "frontend work")
    assert (tmp_path / ".loadout" / "goal").read_text() == "frontend work\n"   # older releases read it whole
    assert "origin=" in (tmp_path / ".loadout" / "goal.meta").read_text()

def test_persist_false_reports_the_refresh_but_writes_nothing(tmp_path: Path):
    _pyproject(tmp_path, "an old purpose")
    write_goal_cache(tmp_path, detect_goal(tmp_path, use_cache=False).goal)
    d = tmp_path / ".loadout"
    before = ((d / "goal").read_text(), (d / "goal.meta").read_text())
    _pyproject(tmp_path, "the real purpose")
    r = detect_goal(tmp_path, persist=False)
    assert r.source == "refreshed" and "real purpose" in r.goal
    assert ((d / "goal").read_text(), (d / "goal.meta").read_text()) == before

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
