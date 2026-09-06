import json, os, subprocess, sys, time
from pathlib import Path

from ccloadout.stop_hook import build_reminder, parse_transcript, signals

HOOK = [sys.executable, "-m", "ccloadout.stop_hook"]
# Not tmp_path: the hook stays quiet in a scratch directory, which is where pytest runs.
_REPO = "/Users/x/repo"

def _run(payload: dict, env: dict, timeout=20):
    return subprocess.run(HOOK, input=json.dumps(payload), capture_output=True, text=True,
                          env={**os.environ, **env}, timeout=timeout)

def _event(**fields):
    return json.dumps(fields)

def _said(text: str):
    return _event(type="user", message={"content": text})

def _used(name: str, **tool_input):
    return _event(type="assistant",
                  message={"content": [{"type": "tool_use", "name": name, "input": tool_input}]})

def _transcript(tmp_path: Path, lines, name="transcript.jsonl") -> Path:
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n")
    return path

def _long_session(extra=()):
    return [_said(f"turn {i}") for i in range(6)] + list(extra)

def test_a_designing_session_is_reminded(tmp_path):
    transcript = _transcript(tmp_path, _long_session([_used("EnterPlanMode")]))
    out = _run({"session_id": "s1", "cwd": _REPO,
                "transcript_path": str(transcript)},
               {"LOADOUT_CONFIG_ROOT": str(tmp_path / "root"), "LOADOUT_EXE": "cld"})
    assert out.returncode == 0
    context = json.loads(out.stdout)["hookSpecificOutput"]
    assert context["hookEventName"] == "Stop"
    assert "cld decision new" in context["additionalContext"]

def test_a_short_session_says_nothing(tmp_path):
    transcript = _transcript(tmp_path, [_said("only one turn"), _used("EnterPlanMode")])
    out = _run({"session_id": "s1", "cwd": _REPO,
                "transcript_path": str(transcript)},
               {"LOADOUT_CONFIG_ROOT": str(tmp_path / "root")})
    assert out.returncode == 0 and out.stdout == ""

def test_a_long_session_with_no_signals_says_nothing(tmp_path):
    transcript = _transcript(tmp_path, _long_session())
    out = _run({"session_id": "s1", "cwd": _REPO,
                "transcript_path": str(transcript)},
               {"LOADOUT_CONFIG_ROOT": str(tmp_path / "root")})
    assert out.returncode == 0 and out.stdout == ""

def test_it_reminds_once_per_session(tmp_path):
    transcript = _transcript(tmp_path, _long_session([_used("EnterPlanMode")]))
    payload = {"session_id": "s1", "cwd": _REPO,
               "transcript_path": str(transcript)}
    env = {"LOADOUT_CONFIG_ROOT": str(tmp_path / "root")}
    assert _run(payload, env).stdout != ""
    assert _run(payload, env).stdout == ""          # the marker survives the first run

def test_a_session_that_already_registered_is_not_asked_again(tmp_path):
    transcript = _transcript(tmp_path,
                             _long_session([_used("EnterPlanMode"),
                                            _said("cld decision new we picked file-per-entry")]))
    out = _run({"session_id": "s1", "cwd": _REPO,
                "transcript_path": str(transcript)},
               {"LOADOUT_CONFIG_ROOT": str(tmp_path / "root")})
    assert out.returncode == 0 and out.stdout == ""

def test_a_scratch_directory_files_nothing(tmp_path):
    transcript = _transcript(tmp_path, _long_session([_used("EnterPlanMode")]))
    out = _run({"session_id": "s1", "cwd": "/private/var/folders/xy/T/scratch",
                "transcript_path": str(transcript)},
               {"LOADOUT_CONFIG_ROOT": str(tmp_path / "root")})
    assert out.returncode == 0 and out.stdout == ""

def test_exits_zero_with_no_transcript_no_config_and_no_input(tmp_path):
    for payload, env in (({"session_id": "s", "transcript_path": str(tmp_path / "gone.jsonl")},
                          {"LOADOUT_CONFIG_ROOT": str(tmp_path / "root")}),
                         ({"session_id": "s", "cwd": _REPO}, {}),
                         ({}, {"LOADOUT_CONFIG_ROOT": str(tmp_path)})):
        out = _run(payload, env)
        assert out.returncode == 0 and out.stdout == ""

def test_exits_zero_on_a_corrupt_transcript(tmp_path):
    transcript = _transcript(tmp_path, ["{not json", "", "null", _said("a real turn")])
    out = _run({"session_id": "s1", "cwd": _REPO,
                "transcript_path": str(transcript)},
               {"LOADOUT_CONFIG_ROOT": str(tmp_path / "root")})
    assert out.returncode == 0 and out.stdout == ""

def test_a_truncated_transcript_still_counts_as_a_long_session():
    # The tail is all the hook reads; a file big enough to be cut is a long session by definition.
    parsed = parse_transcript(_said("only one visible turn") + "\n" + _used("EnterPlanMode"))
    assert parsed["turns"] < 5
    assert signals(parsed, "-Users-x-repo", truncated=True) == ["plan-mode"]
    assert signals(parsed, "-Users-x-repo", truncated=False) == []

def test_signals_name_every_reason_they_fired():
    parsed = parse_transcript("\n".join(
        [_said(f"turn {i}") for i in range(6)]
        + [_said("which approach do we take?"), _used("EnterPlanMode"),
           _used("Skill", skill="superpowers:brainstorming")]
        + [_used("Edit", file_path=f"/repo/f{i}.py") for i in range(3)]))
    assert signals(parsed, "-Users-x-repo") == ["plan-mode", "files-edited", "design-skill",
                                                "keyword"]

def test_the_same_file_edited_twice_is_one_file():
    parsed = parse_transcript("\n".join(
        [_said(f"turn {i}") for i in range(6)]
        + [_used("Edit", file_path="/repo/a.py")] * 3))
    assert parsed["files"] == ["/repo/a.py"] and signals(parsed, "-Users-x-repo") == []

def test_the_reminder_names_both_ways_to_record(tmp_path):
    reminder = build_reminder("/usr/local/bin/claude-loadout")
    assert "/usr/local/bin/claude-loadout decision new" in reminder
    assert "/usr/local/bin/claude-loadout memory add" in reminder

def test_old_state_markers_are_cleaned_up(tmp_path):
    root = tmp_path / "root"
    state = root / "loadout" / ".state"
    state.mkdir(parents=True)
    stale, fresh = state / "old.json", state / "new.json"
    stale.write_text("true"); fresh.write_text("true")
    os.utime(stale, (0, 0))
    transcript = _transcript(tmp_path, _long_session())
    _run({"session_id": "s2", "cwd": _REPO, "transcript_path": str(transcript)},
         {"LOADOUT_CONFIG_ROOT": str(root)})
    assert not stale.exists() and fresh.exists()

def test_only_the_tail_of_a_huge_transcript_is_read(tmp_path):
    # compose gives every injected hook a 5 s ceiling; parsing a whole long session would spend it.
    filler = [_said("noise " + "x" * 400) for _ in range(4000)]
    transcript = _transcript(tmp_path, filler + _long_session([_used("EnterPlanMode")]))
    assert transcript.stat().st_size > 1_000_000
    start = time.monotonic()
    out = _run({"session_id": "s1", "cwd": _REPO, "transcript_path": str(transcript)},
               {"LOADOUT_CONFIG_ROOT": str(tmp_path / "root")})
    assert out.returncode == 0 and out.stdout != ""
    assert time.monotonic() - start < 2.0

def _hooks_for(tmp_path: Path, toml: str):
    from ccloadout.cli import _session_hooks
    from ccloadout.config import load_config
    repo = tmp_path / "repo"
    (repo / ".loadout").mkdir(parents=True)
    (repo / ".loadout" / "config.toml").write_text(toml)
    cfg = load_config(repo, {"CLAUDE_CONFIG_DIR": str(tmp_path / "root")})
    return _session_hooks(cfg, repo, None)

def test_the_reminder_is_installed_even_with_recall_off(tmp_path):
    # It asks the session to write, so an empty store is exactly when it is worth most.
    hooks, env = _hooks_for(tmp_path, "[memory]\nenabled = false\nstop_prompt = true\n")
    assert hooks == {"Stop": "ccloadout.stop_hook"}
    assert env["LOADOUT_EXE"] and env["LOADOUT_CONFIG_ROOT"]

def test_no_hooks_without_the_opt_in(tmp_path):
    assert _hooks_for(tmp_path, "[memory]\nenabled = false\n") == (None, None)
