from pathlib import Path
from ccloadout.flags import clear_flag, flags_path, load_flags, set_flag

def test_a_flag_records_its_reason_and_date(tmp_path: Path):
    root, repo = tmp_path / "root", tmp_path / "repo"
    set_flag(root, repo, "memory:a", "says the fix is in cli.py:170, which moved")
    flag = load_flags(root, repo)["memory:a"]
    assert "cli.py:170" in flag.reason and flag.at
    assert flags_path(root).parent == root / "loadout"      # never inside the repository

def test_flagging_twice_keeps_the_latest_reason(tmp_path: Path):
    root, repo = tmp_path / "root", tmp_path / "repo"
    set_flag(root, repo, "memory:a", "first")
    set_flag(root, repo, "memory:a", "second")
    assert load_flags(root, repo)["memory:a"].reason == "second"

def test_clearing_removes_only_that_flag(tmp_path: Path):
    root, repo = tmp_path / "root", tmp_path / "repo"
    set_flag(root, repo, "memory:a", "x")
    set_flag(root, repo, "memory:b", "y")
    clear_flag(root, repo, "memory:a")
    assert set(load_flags(root, repo)) == {"memory:b"}

def test_repositories_do_not_share_flags(tmp_path: Path):
    root = tmp_path / "root"
    set_flag(root, tmp_path / "one", "memory:a", "x")
    assert load_flags(root, tmp_path / "two") == {}

def test_clearing_an_absent_flag_is_not_an_error(tmp_path: Path):
    clear_flag(tmp_path / "root", tmp_path / "repo", "memory:nope")
    assert load_flags(tmp_path / "root", tmp_path / "repo") == {}
