import json, os, subprocess, sys
from pathlib import Path
from ccloadout.candidates import load_candidates

HOOK = [sys.executable, "-m", "ccloadout.debt_hook"]

def _run(payload: dict, env: dict):
    return subprocess.run(HOOK, input=json.dumps(payload), capture_output=True, text=True,
                          env={**os.environ, **env}, timeout=20)

def _env(root: Path, patterns="TODO(loadout)"):
    return {"LOADOUT_CONFIG_ROOT": str(root), "LOADOUT_DEBT_PATTERNS": patterns}

def test_a_marker_written_into_a_file_becomes_a_candidate(tmp_path):
    root, repo = tmp_path / "root", tmp_path / "repo"
    out = _run({"tool_name": "Write", "cwd": str(repo),
                "tool_input": {"file_path": "src/x.py",
                               "content": "def f():\n    pass  # TODO(loadout) drop the shim\n"}},
               _env(root))
    assert out.returncode == 0
    row, = load_candidates(root, repo, kind="debt-signal")
    assert row["pattern"] == "TODO(loadout)" and row["file"] == "src/x.py"
    assert "drop the shim" in row["excerpt"]

def test_multiedit_replacements_are_scanned(tmp_path):
    root, repo = tmp_path / "root", tmp_path / "repo"
    _run({"tool_name": "MultiEdit", "cwd": str(repo),
          "tool_input": {"file_path": "a.py",
                         "edits": [{"new_string": "x = 1"},
                                   {"new_string": "# TODO(loadout) unpick this"}]}}, _env(root))
    assert load_candidates(root, repo, kind="debt-signal")

def test_reading_a_file_that_contains_the_marker_records_nothing(tmp_path):
    root, repo = tmp_path / "root", tmp_path / "repo"
    _run({"tool_name": "Read", "cwd": str(repo),
          "tool_input": {"file_path": "a.py"},
          "tool_response": "# TODO(loadout) someone else's shim"}, _env(root))
    assert load_candidates(root, repo) == []          # not debt this session created

def test_no_patterns_configured_means_no_capture(tmp_path):
    root, repo = tmp_path / "root", tmp_path / "repo"
    _run({"tool_name": "Write", "cwd": str(repo),
          "tool_input": {"content": "# TODO(loadout) x"}}, _env(root, patterns=""))
    assert load_candidates(root, repo) == []

def test_exits_zero_on_junk_input_and_missing_config(tmp_path):
    for payload, env in (({"tool_name": "Write", "tool_input": "not a dict"}, _env(tmp_path)),
                         ({}, {}),
                         ({"tool_name": "Write", "tool_input": {"content": "TODO(loadout)"}},
                          {"LOADOUT_DEBT_PATTERNS": "TODO(loadout)"})):
        assert _run(payload, env).returncode == 0

def test_session_rows_and_signal_rows_stay_separable(tmp_path):
    from ccloadout.candidates import record_session
    root, repo = tmp_path / "root", tmp_path / "repo"
    record_session(root, repo, goal="g", exit_code=0, changed=[])
    _run({"tool_name": "Write", "cwd": str(repo),
          "tool_input": {"file_path": "a.py", "content": "# TODO(loadout) y"}}, _env(root))
    assert len(load_candidates(root, repo)) == 2
    assert len(load_candidates(root, repo, kind="session")) == 1
    assert len(load_candidates(root, repo, kind="debt-signal")) == 1

def test_a_marker_written_through_a_shell_heredoc_is_caught(tmp_path):
    root, repo = tmp_path / "root", tmp_path / "repo"
    out = _run({"tool_name": "Bash", "cwd": str(repo),
                "tool_input": {"command": "cat > shim.py <<'EOF'\n"
                                          "x = 1  # TODO(loadout) drop the stub\nEOF"}},
               _env(root))
    assert out.returncode == 0
    row, = load_candidates(root, repo, kind="debt-signal")
    assert row["file"] == "(shell)" and "drop the stub" in row["excerpt"]

def test_a_shell_command_that_only_reads_records_nothing(tmp_path):
    root, repo = tmp_path / "root", tmp_path / "repo"
    _run({"tool_name": "Bash", "cwd": str(repo),
          "tool_input": {"command": "grep -rn 'TODO(loadout)' src/"}}, _env(root))
    assert load_candidates(root, repo) == []          # searching for markers is not creating one
