from pathlib import Path
from ccloadout.decisions import corpus_dir, new_decision, supersede
from ccloadout.memory import read_store, slug_for

def test_writes_into_the_existing_corpus_directory(tmp_path: Path):
    repo, root, home = tmp_path / "repo", tmp_path / "root", tmp_path / "home"
    legacy = home / ".claude" / "debug-decisions" / slug_for(repo)
    legacy.mkdir(parents=True)                       # the skill's own path already holds a corpus
    assert corpus_dir(root, repo, home) == legacy

def test_falls_back_to_the_config_root_when_no_corpus_exists(tmp_path: Path):
    repo, root, home = tmp_path / "repo", tmp_path / "root", tmp_path / "home"
    assert corpus_dir(root, repo, home) == root / "debug-decisions" / slug_for(repo)

def test_new_decision_round_trips_through_the_store_reader(tmp_path: Path):
    repo, root, home = tmp_path / "repo", tmp_path / "root", tmp_path / "home"
    path = new_decision(corpus_dir(root, repo, home), slug_for(repo),
                        "Use a sidecar for usage counters", tags=["storage", "git"])
    assert path.name.endswith("-use-a-sidecar-for-usage-counters.md")
    entry, = read_store(repo, root, home=home).entries
    assert entry.kind == "decision" and entry.status == "active"
    assert entry.description == "Use a sidecar for usage counters"
    assert "## Context" in path.read_text() and "storage, git" in path.read_text()

def test_index_is_created_and_kept_newest_first(tmp_path: Path):
    repo, root, home = tmp_path / "repo", tmp_path / "root", tmp_path / "home"
    directory = corpus_dir(root, repo, home)
    new_decision(directory, slug_for(repo), "First call", tags=[])
    new_decision(directory, slug_for(repo), "Second call", tags=[])
    rows = [ln for ln in (directory / "INDEX.md").read_text().splitlines() if ln.startswith("| 2")]
    assert len(rows) == 2 and "Second call" in rows[0]

def test_superseding_marks_the_old_one_and_links_the_new(tmp_path: Path):
    repo, root, home = tmp_path / "repo", tmp_path / "root", tmp_path / "home"
    directory = corpus_dir(root, repo, home)
    old = new_decision(directory, slug_for(repo), "Store counters in the entry files", tags=[])
    new = new_decision(directory, slug_for(repo), "Store counters in a sidecar", tags=[])
    supersede(old, new.stem)
    assert "status: superseded" in old.read_text()
    assert new.stem in old.read_text()
    by_name = {e.name: e for e in read_store(repo, root, home=home).entries}
    assert by_name[old.stem].status == "superseded"
    index = (directory / "INDEX.md").read_text()
    assert "superseded" in index                     # the index follows the file, not the other way
