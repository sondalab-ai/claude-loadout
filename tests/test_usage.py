import json
from datetime import date, timedelta
from pathlib import Path
from ccloadout.usage import load_usage, record_delivery, usage_path

def test_records_uses_and_last_used_per_repo(tmp_path: Path):
    root, repo = tmp_path / "root", tmp_path / "repo"
    record_delivery(root, repo, ["memory:a", "memory:b"])
    record_delivery(root, repo, ["memory:a"])
    usage = load_usage(root, repo)
    assert usage["memory:a"].uses == 2 and usage["memory:b"].uses == 1
    assert usage["memory:a"].last_used == date.today().isoformat()

def test_repos_do_not_share_counters(tmp_path: Path):
    root = tmp_path / "root"
    record_delivery(root, tmp_path / "one", ["memory:a"])
    record_delivery(root, tmp_path / "two", ["memory:a"])
    assert load_usage(root, tmp_path / "one")["memory:a"].uses == 1

def test_nothing_is_written_outside_the_config_root(tmp_path: Path):
    root, repo = tmp_path / "root", tmp_path / "repo"
    repo.mkdir()
    record_delivery(root, repo, ["memory:a"])
    assert usage_path(root).parent == root / "loadout"
    assert list(repo.iterdir()) == []                 # the repository is never touched

def test_empty_delivery_writes_nothing(tmp_path: Path):
    root = tmp_path / "root"
    record_delivery(root, tmp_path / "repo", [])
    assert not usage_path(root).exists()

def test_concurrent_writers_do_not_lose_an_increment(tmp_path: Path):
    from concurrent.futures import ThreadPoolExecutor
    root, repo = tmp_path / "root", tmp_path / "repo"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: record_delivery(root, repo, ["memory:a"]), range(24)))
    assert load_usage(root, repo)["memory:a"].uses == 24

def test_a_corrupt_sidecar_is_not_fatal(tmp_path: Path):
    root, repo = tmp_path / "root", tmp_path / "repo"
    usage_path(root).parent.mkdir(parents=True)
    usage_path(root).write_text("{not json")
    assert load_usage(root, repo) == {}
    record_delivery(root, repo, ["memory:a"])         # recovers by rewriting
    assert load_usage(root, repo)["memory:a"].uses == 1

def test_stale_records_report_their_age(tmp_path: Path):
    from ccloadout.usage import Usage
    old = Usage(uses=5, last_used=(date.today() - timedelta(days=100)).isoformat())
    assert old.days_since(date.today()) == 100
    assert Usage(uses=1, last_used="not-a-date").days_since(date.today()) is None
