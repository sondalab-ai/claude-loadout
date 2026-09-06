import json
from pathlib import Path
from ccloadout.candidates import candidates_path, load_candidates, record_session

def test_records_one_line_per_session(tmp_path: Path):
    root, repo = tmp_path / "root", tmp_path / "repo"
    record_session(root, repo, goal="scoping sessions", exit_code=0, changed=["src/cli.py"])
    record_session(root, repo, goal="scoping sessions", exit_code=1, changed=[])
    rows = load_candidates(root, repo)
    assert len(rows) == 2
    assert rows[0]["goal"] == "scoping sessions" and rows[0]["changed"] == ["src/cli.py"]
    assert rows[1]["exit_code"] == 1

def test_other_repositories_are_not_returned(tmp_path: Path):
    root = tmp_path / "root"
    record_session(root, tmp_path / "one", goal="a", exit_code=0, changed=[])
    record_session(root, tmp_path / "two", goal="b", exit_code=0, changed=[])
    assert [r["goal"] for r in load_candidates(root, tmp_path / "one")] == ["a"]

def test_changed_file_list_is_bounded(tmp_path: Path):
    root, repo = tmp_path / "root", tmp_path / "repo"
    record_session(root, repo, goal="g", exit_code=0, changed=[f"f{i}.py" for i in range(200)])
    row, = load_candidates(root, repo)
    assert len(row["changed"]) < 200 and row["changed_total"] == 200

def test_file_rotates_instead_of_growing_forever(tmp_path: Path):
    root, repo = tmp_path / "root", tmp_path / "repo"
    for i in range(40):
        record_session(root, repo, goal="g" * 200, exit_code=0, changed=[], max_bytes=2000)
    assert candidates_path(root).stat().st_size <= 4000
    rotated = list(candidates_path(root).parent.glob("candidates-*.jsonl"))
    assert rotated                                    # nothing is deleted, it is moved aside

def test_a_corrupt_line_does_not_break_the_reader(tmp_path: Path):
    root, repo = tmp_path / "root", tmp_path / "repo"
    record_session(root, repo, goal="good", exit_code=0, changed=[])
    with candidates_path(root).open("a") as fh:
        fh.write("{not json\n")
    assert [r["goal"] for r in load_candidates(root, repo)] == ["good"]

def test_consolidating_one_repo_keeps_every_other_line(tmp_path: Path):
    from ccloadout.candidates import drop_rows
    root = tmp_path / "root"
    record_session(root, tmp_path / "a", goal="mine", exit_code=0, changed=[])
    record_session(root, tmp_path / "b", goal="theirs", exit_code=0, changed=[])
    with candidates_path(root).open("a") as fh:      # a writer crashed mid-line
        fh.write('{"kind": "session", "repo": "' + str(tmp_path / "b") + '", "goal": "trunc"\n')
    drop_rows(root, tmp_path / "a", keep=[])
    text = candidates_path(root).read_text()
    assert "theirs" in text and "trunc" in text      # unparsable lines are kept, not judged
    assert "mine" not in text
    assert load_candidates(root, tmp_path / "a") == []
