from pathlib import Path
from ccloadout.memory import read_store, slug_for

def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p

def _entry(name: str, desc: str, **meta) -> str:
    block = "".join("  %s: %s\n" % kv for kv in (meta or {"node_type": "memory"}).items())
    return "---\nname: %s\ndescription: %s\nmetadata:\n%s---\nbody\n" % (name, desc, block)

def _decision(did: str, title: str, status: str = "active") -> str:
    return (f"---\nid: {did}\ndate: 2026-06-08T11:01+02:00\nstatus: {status}\n"
            f"tags: [a, b]\n---\n\n# {title}\n\n## Context\nwhy\n")

def test_reads_all_memory_locations_with_scope(tmp_path: Path):
    repo, root = tmp_path / "repo", tmp_path / "root"
    _write(repo / "docs" / "memory" / "a.md", _entry("a", "repo tracked memory"))
    _write(root / "projects" / slug_for(repo) / "memory" / "b.md", _entry("b", "harness memory"))
    _write(root / "loadout" / "memory" / "c.md", _entry("c", "cross project memory"))
    store = read_store(repo, root, home=tmp_path / "home")
    by_name = {e.name: e for e in store.entries}
    assert set(by_name) == {"a", "b", "c"}
    assert by_name["a"].scope == "repo" and by_name["b"].scope == "repo"
    assert by_name["c"].scope == "global"
    assert by_name["a"].kind == "memory"
    assert by_name["a"].id == "memory:a"

def test_index_files_are_not_entries(tmp_path: Path):
    repo, root = tmp_path / "repo", tmp_path / "root"
    _write(repo / "docs" / "memory" / "MEMORY.md", "# Memory index\n- [a](a.md) — hook\n")
    _write(repo / "docs" / "memory" / "a.md", _entry("a", "real one"))
    assert [e.name for e in read_store(repo, root, home=tmp_path / "h").entries] == ["a"]

def test_symlinked_location_is_deduplicated_by_realpath(tmp_path: Path):
    repo, root = tmp_path / "repo", tmp_path / "root"
    tracked = repo / "docs" / "memory"
    _write(tracked / "a.md", _entry("a", "only once"))
    linked = root / "projects" / slug_for(repo) / "memory"
    linked.parent.mkdir(parents=True)
    linked.symlink_to(tracked)                      # the memory-org convention
    store = read_store(repo, root, home=tmp_path / "h")
    assert [e.name for e in store.entries] == ["a"]
    assert store.shadowed == ()

def test_same_slug_in_two_stores_shadows_the_later_one(tmp_path: Path):
    repo, root = tmp_path / "repo", tmp_path / "root"
    _write(repo / "docs" / "memory" / "a.md", _entry("dup", "wins — earlier location"))
    _write(root / "loadout" / "memory" / "a.md", _entry("dup", "loses"))
    store = read_store(repo, root, home=tmp_path / "h")
    assert [e.description for e in store.entries] == ["wins — earlier location"]
    (kept, dropped), = store.shadowed
    assert kept.scope == "repo" and dropped.scope == "global"

def test_metadata_block_populates_lifecycle_fields(tmp_path: Path):
    repo, root = tmp_path / "repo", tmp_path / "root"
    _write(repo / "docs" / "memory" / "d.md",
           "---\nname: d\ndescription: a debt entry\nmetadata:\n  node_type: memory\n"
           "  loadout_kind: debt\n  status: open\n  anchors: [src/cli.py, src/goal.py]\n"
           "  content_sha: abc123\n---\nbody\n")
    e, = read_store(repo, root, home=tmp_path / "h").entries
    assert e.kind == "debt" and e.status == "open"
    assert e.anchors == ("src/cli.py", "src/goal.py")
    assert e.content_sha == "abc123"
    assert e.id == "debt:d"

def test_decisions_are_read_from_both_corpus_paths(tmp_path: Path):
    repo, root, home = tmp_path / "repo", tmp_path / "root", tmp_path / "home"
    slug = slug_for(repo)
    _write(root / "debug-decisions" / slug / "2026-01-01-0900-x.md",
           _decision("2026-01-01-0900-x", "Config-root decision"))
    _write(home / ".claude" / "debug-decisions" / slug / "2026-02-02-1000-y.md",
           _decision("2026-02-02-1000-y", "Legacy path decision"))
    store = read_store(repo, root, home=home)
    got = {e.description: e for e in store.entries}
    assert set(got) == {"Config-root decision", "Legacy path decision"}
    d = got["Config-root decision"]
    assert d.kind == "decision"
    assert d.name == "2026-01-01-0900-x"
    assert d.id == f"decision:{slug}/2026-01-01-0900-x"

def test_superseded_decision_carries_its_status(tmp_path: Path):
    repo, root, home = tmp_path / "repo", tmp_path / "root", tmp_path / "home"
    _write(root / "debug-decisions" / slug_for(repo) / "2026-01-01-0900-x.md",
           _decision("2026-01-01-0900-x", "Old call", status="superseded"))
    e, = read_store(repo, root, home=home).entries
    assert e.status == "superseded"

def test_files_without_usable_frontmatter_are_skipped(tmp_path: Path):
    repo, root = tmp_path / "repo", tmp_path / "root"
    _write(repo / "docs" / "memory" / "junk.md", "no frontmatter here\n")
    _write(repo / "docs" / "memory" / "nameless.md", "---\ndescription: no name\n---\n")
    assert read_store(repo, root, home=tmp_path / "h").entries == ()

def test_missing_directories_are_not_an_error(tmp_path: Path):
    store = read_store(tmp_path / "nope", tmp_path / "neither", home=tmp_path / "h")
    assert store.entries == () and store.shadowed == ()

def test_both_project_slug_conventions_are_read(tmp_path: Path):
    # Claude Code flattens dots in projects/<slug>; debug-decisions does not. A path with a dot
    # in it lands in two differently named directories, and both hold real entries.
    from ccloadout.memory import harness_slug
    repo = tmp_path / "jane.doe" / "repo"; repo.mkdir(parents=True)
    root, home = tmp_path / "root", tmp_path / "home"
    assert harness_slug(repo) != slug_for(repo)
    _write(root / "projects" / harness_slug(repo) / "memory" / "a.md", _entry("a", "harness slug"))
    _write(home / ".claude" / "debug-decisions" / slug_for(repo) / "d.md",
           _decision("2026-01-01-0900-d", "Decision slug"))
    names = {e.name for e in read_store(repo, root, home=home).entries}
    assert names == {"a", "2026-01-01-0900-d"}
