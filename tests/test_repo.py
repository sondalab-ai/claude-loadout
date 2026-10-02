import subprocess
from pathlib import Path
import pytest
import ccloadout.cli as cli
from ccloadout.repo import repo_root

def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                   cwd=cwd, check=True, capture_output=True)

@pytest.fixture
def checkout(tmp_path):
    main = tmp_path / "main"; main.mkdir()
    _git(main, "init", "-q")
    (main / "README.md").write_text("# demo\n")
    _git(main, "add", "README.md")
    _git(main, "commit", "-q", "-m", "init")         # `worktree add` needs a commit to branch from
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", str(wt), "-b", "feature")
    return main, wt

def test_worktree_resolves_to_the_main_checkout(checkout):
    main, wt = checkout
    assert repo_root(wt) == main.resolve()
    assert repo_root(main) == main.resolve()
    (wt / "pkg").mkdir()
    assert repo_root(wt / "pkg") == main.resolve()   # a subdirectory is the same repository

def test_separate_git_dir_resolves_to_the_checkout_top_level(tmp_path):
    top = tmp_path / "top"; top.mkdir()
    _git(tmp_path, "init", "-q", "--separate-git-dir", str(tmp_path / "store.git"), str(top))
    (top / "sub").mkdir()
    assert repo_root(top / "sub") == top.resolve()   # not the subdirectory it was asked from

def test_submodule_resolves_to_its_own_top_level(checkout, tmp_path):
    main, _wt = checkout
    lib = tmp_path / "lib"; lib.mkdir()
    _git(lib, "init", "-q"); (lib / "f").write_text("x\n"); _git(lib, "add", "f")
    _git(lib, "commit", "-q", "-m", "lib")
    _git(main, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(lib), "vendor/lib")
    (main / "vendor" / "lib" / "deep").mkdir()
    assert repo_root(main / "vendor" / "lib" / "deep") == (main / "vendor" / "lib").resolve()

def test_outside_git_is_the_path_itself(tmp_path):
    lone = tmp_path / "lone"; lone.mkdir()
    assert repo_root(lone) == lone.resolve()

def test_git_failure_falls_back_to_the_path(tmp_path, monkeypatch):
    class _Empty:                                   # what the CLI tests' subprocess stub returns
        returncode = 0
        stdout = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Empty())
    assert repo_root(tmp_path) == tmp_path.resolve()

def test_note_written_from_a_worktree_lands_in_the_main_checkout(checkout, tmp_path, monkeypatch):
    main, wt = checkout
    root = tmp_path / "profile"; root.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(wt)
    rc = cli.main(["memory", "add", "--name", "shared-fact", "worktrees share one store"])
    assert rc == 0
    assert (main / "docs" / "memory" / "shared-fact.md").is_file()
    assert not (wt / "docs" / "memory").exists()     # nothing orphaned in the worktree

def test_usage_from_a_worktree_is_keyed_by_the_main_checkout(checkout, tmp_path):
    from ccloadout.usage import load_usage, record_delivery
    main, wt = checkout
    root = tmp_path / "profile"; root.mkdir()
    record_delivery(root, repo_root(wt), ["memory:a"])
    assert "memory:a" in load_usage(root, main)
