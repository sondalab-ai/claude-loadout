import json, os, subprocess, sys
from pathlib import Path

HOOK = [sys.executable, "-m", "ccloadout.prompt_hook"]

def _run(payload: dict, env: dict, timeout=20):
    return subprocess.run(HOOK, input=json.dumps(payload), capture_output=True, text=True,
                          env={**os.environ, **env}, timeout=timeout)

def _store(tmp_path: Path, n=2):
    root, repo = tmp_path / "root", tmp_path / "repo"
    mem = repo / "docs" / "memory"; mem.mkdir(parents=True)
    for i in range(n):
        (mem / f"m{i}.md").write_text(
            f"---\nname: m{i}\ndescription: per-session skill pruning through the overlay {i}\n"
            "metadata:\n  node_type: memory\n---\nbody\n")
    return root, repo

def test_a_matching_prompt_gets_notes(tmp_path):
    root, repo = _store(tmp_path)
    out = _run({"prompt": "how are skills pruned", "cwd": str(repo)},
               {"LOADOUT_CONFIG_ROOT": str(root)})
    assert out.returncode == 0 and "m0" in out.stdout
    assert "untrusted" in out.stdout

def test_resident_entries_are_not_repeated(tmp_path):
    root, repo = _store(tmp_path)
    out = _run({"prompt": "how are skills pruned", "cwd": str(repo)},
               {"LOADOUT_CONFIG_ROOT": str(root), "LOADOUT_RESIDENT_IDS": "memory:m0,memory:m1"})
    assert out.returncode == 0 and out.stdout == ""

def test_an_unrelated_prompt_says_nothing(tmp_path):
    root, repo = _store(tmp_path)
    out = _run({"prompt": "what is the tide table for tomorrow", "cwd": str(repo)},
               {"LOADOUT_CONFIG_ROOT": str(root)})
    assert out.returncode == 0 and out.stdout == ""

def test_exits_zero_with_no_store_no_config_and_no_input(tmp_path):
    for payload, env in (({"prompt": "anything at all", "cwd": str(tmp_path)},
                          {"LOADOUT_CONFIG_ROOT": str(tmp_path / "nope")}),
                         ({"prompt": "anything at all"}, {}),
                         ({}, {"LOADOUT_CONFIG_ROOT": str(tmp_path)})):
        out = _run(payload, env)
        assert out.returncode == 0 and out.stdout == ""

def test_exits_zero_on_a_corrupt_store(tmp_path):
    root, repo = _store(tmp_path)
    (repo / "docs" / "memory" / "broken.md").write_bytes(b"\xff\xfe not text at all")
    out = _run({"prompt": "how are skills pruned", "cwd": str(repo)},
               {"LOADOUT_CONFIG_ROOT": str(root)})
    assert out.returncode == 0

def test_exits_zero_when_its_own_timeout_fires(tmp_path):
    root, repo = _store(tmp_path, n=50)
    out = _run({"prompt": "how are skills pruned", "cwd": str(repo)},
               {"LOADOUT_CONFIG_ROOT": str(root), "LOADOUT_PROMPT_TIMEOUT_MS": "1"})
    assert out.returncode == 0                      # a blocked prompt would erase the user's text

def test_stays_well_inside_its_latency_budget(tmp_path):
    import statistics, time
    root, repo = _store(tmp_path, n=40)
    times = []
    for _ in range(5):
        started = time.perf_counter()
        _run({"prompt": "how are skills pruned per session", "cwd": str(repo)},
             {"LOADOUT_CONFIG_ROOT": str(root)})
        times.append((time.perf_counter() - started) * 1000)
    assert statistics.median(times) < 300
