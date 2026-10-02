from datetime import datetime
from pathlib import Path
import pytest
import ccloadout.cli as cli
from ccloadout.link import apply_link, harness_dir, is_linked, plan_link

NOW = datetime(2026, 10, 2, 12, 0, 0)

def _note(directory: Path, name: str, desc: str = "a fact") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(f"---\nname: {name}\ndescription: {desc}\n---\n")
    return path

@pytest.fixture
def env(tmp_path):
    root, repo = tmp_path / "profile", (tmp_path / "repo")
    repo.mkdir(); repo = repo.resolve()
    harness = harness_dir(root, repo)
    harness.parent.mkdir(parents=True)
    (harness.parent / "session.jsonl").write_text("{}\n")   # Claude Code has seen this repo
    return root, repo, harness, repo / "docs" / "memory"

def test_missing_folder_is_linked_and_unindexed_notes_get_lines(env):
    root, repo, harness, store = env
    _note(store, "alpha"); _note(store, "beta")            # docs/memory with no MEMORY.md at all
    plan = plan_link(root, repo, NOW)
    assert plan.state == "missing" and not plan.blocked
    assert [ln.split("](")[0] for ln in plan.added] == ["- [alpha", "- [beta"]
    apply_link(plan)
    assert harness.is_symlink() and is_linked(root, repo)
    index = (store / "MEMORY.md").read_text()
    assert "- [alpha](alpha.md) — a fact" in index and "(beta.md)" in index

def test_real_folder_is_merged_backed_up_and_replaced_by_the_link(env):
    root, repo, harness, store = env
    _note(harness, "from-claude", "kept by the harness")
    (harness / "MEMORY.md").write_text("# Index\n- [from-claude](from-claude.md) — kept by the harness\n")
    _note(store, "from-repo")
    (store / "MEMORY.md").write_text("# Repo notes\n- [from-repo](from-repo.md) — a fact\n")
    plan = plan_link(root, repo, NOW)
    assert plan.state == "merge"
    assert [p.name for p in plan.copies] == ["from-claude.md"]
    apply_link(plan)
    backup = harness.with_name("memory.bak-20261002-120000")
    assert (backup / "from-claude.md").is_file()           # nothing lost
    assert harness.is_symlink() and harness.resolve() == store.resolve()
    index = (store / "MEMORY.md").read_text()
    assert index.startswith("# Repo notes")                 # the repo's index stays the base
    assert "(from-repo.md)" in index and "(from-claude.md)" in index
    assert index.count("(from-claude.md)") == 1

def test_identical_file_on_both_sides_is_not_a_conflict(env):
    root, repo, harness, store = env
    _note(harness, "same"); _note(store, "same")
    plan = plan_link(root, repo, NOW)
    assert plan.copies == () and plan.conflicts == ()

def test_different_file_with_the_same_name_blocks_the_link(env):
    root, repo, harness, store = env
    _note(harness, "clash", "one version"); _note(store, "clash", "another version")
    plan = plan_link(root, repo, NOW)
    assert plan.conflicts == ("clash.md",) and plan.blocked
    with pytest.raises(ValueError):
        apply_link(plan)
    assert not harness.is_symlink()                         # untouched

def test_already_linked_only_adds_missing_index_lines(env):
    root, repo, harness, store = env
    _note(store, "indexed"); _note(store, "orphan")
    (store / "MEMORY.md").write_text("- [indexed](indexed.md) — a fact\n")
    harness.symlink_to(store.resolve(), target_is_directory=True)
    plan = plan_link(root, repo, NOW)
    assert plan.state == "linked" and plan.changes
    assert len(plan.added) == 1 and "(orphan.md)" in plan.added[0]
    apply_link(plan)
    assert "(orphan.md)" in (store / "MEMORY.md").read_text()

def test_link_pointing_elsewhere_is_left_alone(env, tmp_path):
    root, repo, harness, store = env
    other = tmp_path / "elsewhere"; other.mkdir()
    harness.symlink_to(other, target_is_directory=True)
    assert plan_link(root, repo, NOW).state == "elsewhere"

def test_dotfiles_are_ignored_and_never_block(env):
    root, repo, harness, store = env
    harness.mkdir(); (harness / ".DS_Store").write_bytes(b"one")
    store.mkdir(parents=True); (store / ".DS_Store").write_bytes(b"two")
    plan = plan_link(root, repo, NOW)
    assert plan.conflicts == () and plan.copies == ()

def test_a_symlink_in_the_harness_folder_is_not_followed(env, tmp_path):
    root, repo, harness, store = env
    secret = tmp_path / "secret.md"; secret.write_text("private\n")
    harness.mkdir(); (harness / "leak.md").symlink_to(secret)
    plan = plan_link(root, repo, NOW)
    assert plan.blocked and "leak.md" in plan.conflicts[0]   # never copied into the tracked repo

def test_a_directory_on_the_repo_side_is_a_conflict_not_a_crash(env):
    root, repo, harness, store = env
    _note(harness, "clash")
    (store / "clash.md").mkdir(parents=True)
    assert plan_link(root, repo, NOW).conflicts == ("clash.md",)

def test_empty_repo_index_takes_the_harness_lines(env):
    root, repo, harness, store = env
    _note(harness, "kept")
    (harness / "MEMORY.md").write_text("- [kept](kept.md) — a fact\n")
    store.mkdir(parents=True); (store / "MEMORY.md").write_text("")
    apply_link(plan_link(root, repo, NOW))
    assert "(kept.md)" in (store / "MEMORY.md").read_text()

def test_a_failed_symlink_restores_the_original_folder(env, monkeypatch):
    root, repo, harness, store = env
    _note(harness, "precious")
    plan = plan_link(root, repo, NOW)
    def fail(self, *a, **k): raise OSError("no symlinks here")
    monkeypatch.setattr(Path, "symlink_to", fail)
    with pytest.raises(OSError):
        apply_link(plan)
    assert (harness / "precious.md").is_file() and not harness.is_symlink()   # as before
    assert not plan.backup.exists()

def test_backup_name_does_not_collide(env):
    root, repo, harness, store = env
    _note(harness, "x")
    harness.with_name("memory.bak-20261002-120000").mkdir()   # left by a run in the same second
    plan = plan_link(root, repo, NOW)
    assert plan.backup.name == "memory.bak-20261002-120000-2"

def test_refuses_when_claude_code_has_no_sessions_for_the_repo(tmp_path):
    root, repo = tmp_path / "profile", tmp_path / "repo"
    repo.mkdir(); root.mkdir()
    plan = plan_link(root, repo.resolve(), NOW)             # a wrong slug must not "succeed"
    assert plan.state == "unknown-project" and plan.blocked

def _cli_env(monkeypatch, tmp_path, root, repo):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(repo)

def test_cli_link_needs_yes_when_not_interactive(env, tmp_path, monkeypatch, capsys):
    root, repo, harness, store = env
    _note(store, "alpha")
    _cli_env(monkeypatch, tmp_path, root, repo)
    assert cli.main(["memory", "link"]) == 1
    assert not harness.exists()                             # nothing changed without consent
    assert "--yes" in capsys.readouterr().err
    assert cli.main(["memory", "link", "--yes"]) == 0
    assert is_linked(root, repo)

def test_cli_link_refuses_when_notes_are_untracked(env, tmp_path, monkeypatch, capsys):
    root, repo, harness, store = env
    (root / "loadout").mkdir(parents=True)
    (root / "loadout" / "config.toml").write_text("[memory]\ngit_tracked = false\n")
    _cli_env(monkeypatch, tmp_path, root, repo)
    assert cli.main(["memory", "link", "--yes"]) == 0
    assert "nothing to link" in capsys.readouterr().out
    assert not harness.exists()

def test_memory_add_hints_only_when_docs_memory_is_unread(env, tmp_path, monkeypatch, capsys):
    root, repo, harness, store = env
    _cli_env(monkeypatch, tmp_path, root, repo)
    assert cli.main(["memory", "add", "--name", "first", "a first fact"]) == 0
    assert "memory link" in capsys.readouterr().err         # unlinked: the note reaches no session
    assert cli.main(["memory", "link", "--yes"]) == 0
    capsys.readouterr()
    assert cli.main(["memory", "add", "--name", "second", "a second fact"]) == 0
    assert "memory link" not in capsys.readouterr().err     # linked: no nagging
    assert "(second.md)" in (store / "MEMORY.md").read_text()

def test_memory_add_does_not_hint_when_untracked(env, tmp_path, monkeypatch, capsys):
    root, repo, harness, store = env
    (root / "loadout").mkdir(parents=True)
    (root / "loadout" / "config.toml").write_text("[memory]\ngit_tracked = false\n")
    _cli_env(monkeypatch, tmp_path, root, repo)
    assert cli.main(["memory", "add", "--name", "x", "a fact"]) == 0
    assert "memory link" not in capsys.readouterr().err
