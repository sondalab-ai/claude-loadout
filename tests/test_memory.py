from pathlib import Path
from ccloadout.memory import read_store, slug_for

def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p

def _entry(name: str, desc: str, **meta) -> str:
    block = "".join("  %s: %s\n" % kv for kv in (meta or {"node_type": "memory"}).items())
    return "---\nname: %s\ndescription: %s\nmetadata:\n%s---\nbody\n" % (name, desc, block)

def _global_entry(name: str, desc: str) -> str:
    return (f"---\nname: {name}\ndescription: {desc}\nmetadata:\n  node_type: memory\n"
            "  scope: global\n---\nbody\n")

def _decision(did: str, title: str, status: str = "active") -> str:
    return (f"---\nid: {did}\ndate: 2026-06-08T11:01+02:00\nstatus: {status}\n"
            f"tags: [a, b]\n---\n\n# {title}\n\n## Context\nwhy\n")

def test_reads_all_memory_locations_with_scope(tmp_path: Path):
    repo, root = tmp_path / "repo", tmp_path / "root"
    _write(repo / "docs" / "memory" / "a.md", _entry("a", "repo tracked memory"))
    _write(root / "projects" / slug_for(repo) / "memory" / "b.md", _entry("b", "harness memory"))
    _write(root / "projects" / slug_for(repo) / "memory" / "c.md",
           _global_entry("c", "cross project memory"))
    store = read_store(repo, root, home=tmp_path / "home")
    by_name = {e.name: e for e in store.entries}
    assert set(by_name) == {"a", "b", "c"}
    assert by_name["a"].scope == "repo" and by_name["b"].scope == "repo"
    assert by_name["c"].scope == "global"           # the note says so, not the folder
    assert by_name["a"].kind == "memory"
    assert by_name["a"].id == "memory:a"

def test_index_files_are_not_entries(tmp_path: Path):
    repo, root = tmp_path / "repo", tmp_path / "root"
    # With frontmatter, so the filename is the only thing that can exclude it.
    _write(repo / "docs" / "memory" / "MEMORY.md",
           _entry("MEMORY", "an index that looks like an entry"))
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
    _write(root / "projects" / slug_for(repo) / "memory" / "a.md", _entry("dup", "loses"))
    store = read_store(repo, root, home=tmp_path / "h")
    assert [e.description for e in store.entries] == ["wins — earlier location"]
    (kept, dropped), = store.shadowed
    assert kept.path.parent == repo / "docs" / "memory"

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

# --- writing entries (Slice 2) ------------------------------------------------

def test_written_entry_round_trips_through_the_reader(tmp_path: Path):
    from ccloadout.memory import write_entry
    repo, root = tmp_path / "repo", tmp_path / "root"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "cli.py").write_text("x = 1\n")
    path = write_entry(root, repo, name="shim-in-launcher", description="a temporary shim",
                       kind="debt", git_tracked=True, anchors=["src/cli.py"], body="why it is here")
    assert path == repo / "docs" / "memory" / "shim-in-launcher.md"
    e, = read_store(repo, root, home=tmp_path / "h").entries
    assert e.kind == "debt" and e.status == "open" and e.anchors == ("src/cli.py",)
    assert e.content_sha                                   # recorded so staleness has a baseline
    assert "why it is here" in path.read_text()

def test_untracked_entries_land_outside_the_repository(tmp_path: Path):
    from ccloadout.memory import write_entry, harness_slug
    repo, root = tmp_path / "repo", tmp_path / "root"; repo.mkdir()
    path = write_entry(root, repo, name="n", description="d", kind="memory", git_tracked=False)
    assert path == root / "projects" / harness_slug(repo) / "memory" / "n.md"
    assert not (repo / "docs").exists()

def test_writing_the_same_name_twice_is_refused(tmp_path: Path):
    from ccloadout.memory import write_entry, EntryExists
    import pytest
    repo, root = tmp_path / "repo", tmp_path / "root"; repo.mkdir()
    write_entry(root, repo, name="n", description="d", kind="memory", git_tracked=True)
    with pytest.raises(EntryExists):
        write_entry(root, repo, name="n", description="other", kind="memory", git_tracked=True)

def test_resolving_a_debt_entry_rewrites_only_its_status(tmp_path: Path):
    from ccloadout.memory import write_entry, set_status
    repo, root = tmp_path / "repo", tmp_path / "root"; repo.mkdir()
    path = write_entry(root, repo, name="d", description="a shim", kind="debt",
                       git_tracked=True, body="the body stays")
    before = path.read_text()
    set_status(path, "resolved")
    e, = read_store(repo, root, home=tmp_path / "h").entries
    assert e.status == "resolved" and e.description == "a shim"
    assert "the body stays" in path.read_text()
    assert before.count("\n") == path.read_text().count("\n")   # no lines added or lost

# --- the harness's own MEMORY.md index ----------------------------------------

def test_indexed_files_are_recognised(tmp_path: Path):
    from ccloadout.memory import harness_slug, indexed_files
    repo, root = tmp_path / "repo", tmp_path / "root"
    mem = root / "projects" / harness_slug(repo) / "memory"; mem.mkdir(parents=True)
    (mem / "a.md").write_text(_entry("a", "one"))
    (mem / "b.md").write_text(_entry("b", "two"))
    (mem / "MEMORY.md").write_text(
        "# Memory index\n\n- [A note](a.md) — a hook\n- [missing](gone.md) — stale line\n")
    found = indexed_files(root, repo)
    assert (mem / "a.md").resolve() in found
    assert (mem / "b.md").resolve() not in found        # present on disk, absent from the index
    assert len(found) == 1                              # a line pointing nowhere indexes nothing

def test_no_index_means_nothing_is_resident(tmp_path: Path):
    from ccloadout.memory import indexed_files
    assert indexed_files(tmp_path / "root", tmp_path / "repo") == set()

def test_writing_beside_an_index_adds_its_line(tmp_path: Path):
    from ccloadout.memory import harness_slug, write_entry
    repo, root = tmp_path / "repo", tmp_path / "root"
    mem = root / "projects" / harness_slug(repo) / "memory"; mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("# Memory index\n\n- [Old](old.md) — hook\n")
    write_entry(root, repo, name="fresh", description="a new note", kind="memory",
                git_tracked=False)
    text = (mem / "MEMORY.md").read_text()
    assert "- [fresh](fresh.md) — a new note" in text
    assert "- [Old](old.md) — hook" in text             # the existing index is preserved

def test_writing_where_there_is_no_index_creates_none(tmp_path: Path):
    from ccloadout.memory import harness_slug, write_entry
    repo, root = tmp_path / "repo", tmp_path / "root"
    write_entry(root, repo, name="fresh", description="d", kind="memory", git_tracked=False)
    mem = root / "projects" / harness_slug(repo) / "memory"
    assert not (mem / "MEMORY.md").exists()             # we never invent an index the user lacks

def test_deleting_an_entry_removes_its_index_line(tmp_path: Path):
    from ccloadout.memory import harness_slug, forget_entry, write_entry
    repo, root = tmp_path / "repo", tmp_path / "root"
    mem = root / "projects" / harness_slug(repo) / "memory"; mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("# Memory index\n\n- [Keep](keep.md) — hook\n")
    path = write_entry(root, repo, name="doomed", description="d", kind="memory",
                       git_tracked=False)
    forget_entry(path)
    assert not path.exists()
    text = (mem / "MEMORY.md").read_text()
    assert "doomed" not in text and "- [Keep](keep.md) — hook" in text

# --- scope: a property of the note, not a folder of ours ----------------------

def test_a_global_note_reaches_another_project(tmp_path: Path):
    from ccloadout.memory import harness_slug
    here, there, root = tmp_path / "here", tmp_path / "there", tmp_path / "root"
    _write(root / "projects" / harness_slug(there) / "memory" / "g.md",
           _global_entry("harness-lever", "how the settings overlay merges hooks"))
    _write(root / "projects" / harness_slug(there) / "memory" / "local.md",
           _entry("their-local", "something only that repo cares about"))
    store = read_store(here, root, home=tmp_path / "h")
    assert [e.name for e in store.entries] == ["harness-lever"]
    assert store.entries[0].scope == "global"

def test_scope_is_read_from_the_note_not_from_its_folder(tmp_path: Path):
    repo, root = tmp_path / "repo", tmp_path / "root"
    _write(repo / "docs" / "memory" / "g.md", _global_entry("g", "cross-project"))
    _write(repo / "docs" / "memory" / "r.md", _entry("r", "repo-local"))
    scopes = {e.name: e.scope for e in read_store(repo, root, home=tmp_path / "h").entries}
    assert scopes == {"g": "global", "r": "repo"}

def test_the_scopes_filter_excludes_what_it_names(tmp_path: Path):
    repo, root = tmp_path / "repo", tmp_path / "root"
    _write(repo / "docs" / "memory" / "g.md", _global_entry("g", "cross-project"))
    _write(repo / "docs" / "memory" / "r.md", _entry("r", "repo-local"))
    only_repo = read_store(repo, root, home=tmp_path / "h", scopes=("repo",))
    only_global = read_store(repo, root, home=tmp_path / "h", scopes=("global",))
    assert [e.name for e in only_repo.entries] == ["r"]
    assert [e.name for e in only_global.entries] == ["g"]

def test_a_global_note_is_written_into_the_canonical_store(tmp_path: Path):
    from ccloadout.memory import harness_slug, write_entry
    repo, root = tmp_path / "repo", tmp_path / "root"; repo.mkdir()
    path = write_entry(root, repo, name="lever", description="d", kind="memory",
                       git_tracked=False, scope="global")
    assert path == root / "projects" / harness_slug(repo) / "memory" / "lever.md"
    assert "scope: global" in path.read_text()      # the note says so; no folder of ours exists
    assert not (root / "loadout").exists()

def test_links_are_read_from_frontmatter_and_from_the_body(tmp_path: Path):
    repo, root = tmp_path / "repo", tmp_path / "root"
    _write(repo / "docs" / "memory" / "a.md",
           "---\nname: a\ndescription: d\nmetadata:\n  node_type: memory\n"
           "  links: [b, c]\n---\nsee also [[d]] and [[b]]\n")
    e, = read_store(repo, root, home=tmp_path / "h").entries
    assert e.links == ("b", "c", "d")               # deduplicated, frontmatter first

def test_setting_a_key_that_is_absent_inserts_it(tmp_path: Path):
    from ccloadout.memory import set_meta
    path = _write(tmp_path / "n.md",
                  "---\nname: n\ndescription: d\nmetadata:\n  node_type: memory\n---\nbody\n")
    set_meta(path, "scope", "global")
    text = path.read_text()
    assert "  scope: global" in text and "node_type: memory" in text and text.endswith("body\n")

def test_setting_a_key_with_no_metadata_block_creates_one(tmp_path: Path):
    from ccloadout.memory import set_meta
    path = _write(tmp_path / "n.md", "---\nname: n\ndescription: d\n---\nbody\n")
    set_meta(path, "scope", "global")
    assert "metadata:\n  scope: global" in path.read_text()

def test_setting_a_key_on_a_file_without_frontmatter_is_refused(tmp_path: Path):
    from ccloadout.memory import NoStatus, set_meta
    import pytest
    path = _write(tmp_path / "n.md", "just a body\n")
    with pytest.raises(NoStatus):
        set_meta(path, "scope", "global")
