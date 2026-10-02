from datetime import datetime
from pathlib import Path
from ccloadout.decisions import supersede, supersede_entry, write_decision
from ccloadout.memory import read_store, slug_for

NOW = datetime(2026, 10, 2, 11, 30)

def _legacy(directory: Path, did: str, title: str) -> Path:
    # A file in the old `debug-decisions` corpus shape, with its INDEX.md row.
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{did}.md"
    path.write_text(f"---\nid: {did}\nstatus: active\ntags: []\n---\n\n# {title}\n")
    index = directory / "INDEX.md"
    rows = index.read_text() if index.exists() else "| ID | Date | Status | Tags | Title |\n|----|\n"
    index.write_text(rows + f"| {did} | 2026-09-01 | active |  | {title} |\n")
    return path

def test_decision_is_written_into_the_note_store_and_indexed(tmp_path: Path):
    repo, root, home = tmp_path / "repo", tmp_path / "root", tmp_path / "home"
    store = repo / "docs" / "memory"; store.mkdir(parents=True)
    (store / "MEMORY.md").write_text("# Memory index\n")
    path = write_decision(root, repo, "Use a sidecar for usage counters", ["storage", "git"],
                          git_tracked=True, now=NOW)
    assert path == store / "2026-10-02-1130-use-a-sidecar-for-usage-counters.md"
    entry, = read_store(repo, root, home=home).entries
    assert entry.kind == "decision" and entry.status == "active"
    assert entry.description == "Use a sidecar for usage counters"
    text = path.read_text()
    assert "## Context" in text and "tags: [storage, git]" in text
    assert f"({path.name}) — decision: Use a sidecar" in (store / "MEMORY.md").read_text()

def test_untracked_decisions_go_to_claude_codes_folder(tmp_path: Path):
    repo, root = tmp_path / "repo", tmp_path / "root"
    path = write_decision(root, repo, "Keep notes private", [], git_tracked=False, now=NOW)
    assert path.parent == root / "projects" / slug_for(repo).replace(".", "-") / "memory"
    assert not (repo / "docs").exists()

def test_two_decisions_in_the_same_minute_get_distinct_names(tmp_path: Path):
    repo, root = tmp_path / "repo", tmp_path / "root"
    first = write_decision(root, repo, "Same call", [], True, now=NOW)
    second = write_decision(root, repo, "Same call", [], True, now=NOW)
    assert first != second and second.stem.endswith("-2")

def test_superseding_a_store_decision_marks_it_and_unindexes_it(tmp_path: Path):
    repo, root, home = tmp_path / "repo", tmp_path / "root", tmp_path / "home"
    store = repo / "docs" / "memory"; store.mkdir(parents=True)
    (store / "MEMORY.md").write_text("# Memory index\n")
    old = write_decision(root, repo, "Store counters in the entry files", [], True, now=NOW)
    new = write_decision(root, repo, "Store counters in a sidecar", [], True, now=NOW)
    supersede_entry(old, new)
    assert "status: superseded" in old.read_text() and new.stem in old.read_text()
    index = (store / "MEMORY.md").read_text()
    assert old.name not in index and new.name in index
    by_name = {e.name: e for e in read_store(repo, root, home=home).entries}
    assert by_name[old.stem].status == "superseded"

def test_legacy_corpus_is_still_read_and_superseded_in_place(tmp_path: Path):
    repo, root, home = tmp_path / "repo", tmp_path / "root", tmp_path / "home"
    corpus = home / ".claude" / "debug-decisions" / slug_for(repo)
    old = _legacy(corpus, "2026-09-01-1200-old-call", "Old call")
    entry, = read_store(repo, root, home=home).entries
    assert entry.kind == "decision" and entry.description == "Old call"
    supersede(old, "2026-10-02-1130-new-call")
    assert "status: superseded" in old.read_text()
    assert "| superseded |" in (corpus / "INDEX.md").read_text()

def test_reading_a_corpus_never_rewrites_it(tmp_path: Path):
    # Acceptance criterion 12, second half: absorption is a reader, not a migration.
    repo, root, home = tmp_path / "repo", tmp_path / "root", tmp_path / "home"
    corpus = home / ".claude" / "debug-decisions" / slug_for(repo)
    for i in range(3):
        _legacy(corpus, f"2026-09-01-120{i}-call-{i}", f"Call {i}")
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in corpus.iterdir()}
    for _ in range(3):
        read_store(repo, root, home=home)
    after = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in corpus.iterdir()}
    assert before == after
