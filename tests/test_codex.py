import datetime as dt, json, shlex, sys, tomllib
from pathlib import Path
import pytest
import ccloadout.cli as cli
from ccloadout import codex, tomlw
from ccloadout.compose import remove_tmp
from ccloadout.harness import CodexHarness, harness_for
from ccloadout.inventory import Item

_HOOK = f"{shlex.quote(sys.executable)} -P -m ccloadout.prompt_hook"

def _home(tmp_path: Path, config: str) -> Path:
    home = tmp_path / "codex-home"; home.mkdir()
    (home / "config.toml").write_text(config)
    (home / "auth.json").write_text('{"token": "secret"}')
    (home / "sessions").mkdir()
    (home / "sessions" / "rollout.jsonl").write_text("turn\n")
    return home

def _skill(folder: Path, name: str, desc: str) -> None:
    (folder / name).mkdir(parents=True)
    (folder / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n")

_SAMPLE = '''
model = "gpt-5"
developer_instructions = "Be terse."

[mcp_servers.github]
command = "gh-mcp"
args = ["--stdio"]

[mcp_servers.figma]
url = "https://figma.example/mcp"

[mcp_servers.old]
command = "old"
enabled = false

[[skills.config]]
name = "legacy"
enabled = false
'''

def test_inventory_reads_servers_skills_and_a_trusted_project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    repo = tmp_path / "repo"; (repo / ".git").mkdir(parents=True)
    home = _home(tmp_path, _SAMPLE + f'\n[projects."{repo}"]\ntrust_level = "trusted"\n')
    (repo / ".codex").mkdir()
    (repo / ".codex" / "config.toml").write_text('[mcp_servers.db]\ncommand = "db-mcp"\n')
    _skill(repo / ".agents" / "skills", "pdf", "Read PDFs")
    _skill(tmp_path / "user" / ".agents" / "skills", "tdd", "Test first")
    got = CodexHarness().inventory(home, repo)
    assert [(i.id, i.kind) for i in got] == [("github", "mcp"), ("figma", "mcp"), ("db", "mcp"),
                                             ("pdf", "skill"), ("tdd", "skill")]
    assert got[3].description == "Read PDFs"

def test_inventory_ignores_an_untrusted_project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    repo = tmp_path / "repo"; (repo / ".codex").mkdir(parents=True)
    (repo / ".codex" / "config.toml").write_text('[mcp_servers.db]\ncommand = "db-mcp"\n')
    home = _home(tmp_path, _SAMPLE + f'\n[projects."{repo}"]\ntrust_level = "untrusted"\n')
    assert "db" not in {i.id for i in CodexHarness().inventory(home, repo)}

def _compose(tmp_path, config=_SAMPLE, **kw):
    home = _home(tmp_path, config)
    items = [Item("github", "mcp", "github", ""), Item("figma", "mcp", "figma", ""),
             Item("pdf", "skill", "pdf", ""), Item("tdd", "skill", "tdd", "")]
    kept = [items[0], items[3]]
    plan = CodexHarness().compose(kept, items, home, ["--model", "o3"], environ={"A": "1"},
                                  cwd=tmp_path, **kw)
    return home, plan

def test_compose_writes_the_exact_temp_config(tmp_path):
    home, plan = _compose(tmp_path, memory_payload="remember this",
                          hooks={"UserPromptSubmit": "ccloadout.prompt_hook",
                                 "Stop": "ccloadout.stop_hook"},
                          hook_env={"LOADOUT_N": 3})
    tmp, = plan.tmp_dirs
    key = f"{tmp}/config.toml:user_prompt_submit:0:0"
    assert (tmp / "config.toml").read_text() == f'''"model" = "gpt-5"
"developer_instructions" = "Be terse.\\n\\nremember this"

["mcp_servers"]

["mcp_servers"."github"]
"command" = "gh-mcp"
"args" = ["--stdio"]

["skills"]

[["skills"."config"]]
"name" = "legacy"
"enabled" = false

[["skills"."config"]]
"name" = "pdf"
"enabled" = false

["hooks"]

[["hooks"."UserPromptSubmit"]]

[["hooks"."UserPromptSubmit"."hooks"]]
"type" = "command"
"command" = {json.dumps(_HOOK)}
"timeout" = 5

["hooks"."state"]

["hooks"."state".{json.dumps(key)}]
"trusted_hash" = "{codex.hook_hash("UserPromptSubmit", _HOOK, 5)}"
'''
    remove_tmp(plan)

def test_compose_argv_env_and_servers(tmp_path):
    home, plan = _compose(tmp_path, hooks={"Stop": "ccloadout.stop_hook"},
                          hook_env={"LOADOUT_N": 3}, profile_dir=tmp_path / "ignored")
    tmp, = plan.tmp_dirs
    assert plan.argv == ["codex", "--model", "o3"]
    assert plan.env == {"A": "1", "CODEX_HOME": str(tmp)}   # Stop is Claude-only: no hook, no env
    assert plan.servers == ("github",) and plan.tmp_paths == []
    assert "hooks" not in tomllib.loads((tmp / "config.toml").read_text())
    remove_tmp(plan)

def test_temp_home_links_every_real_entry_but_the_config(tmp_path):
    home, plan = _compose(tmp_path)
    tmp, = plan.tmp_dirs
    assert tmp == tmp.resolve() and not (tmp / "config.toml").is_symlink()
    assert sorted(p.name for p in tmp.iterdir()) == ["auth.json", "config.toml", "log", "sessions"]
    for name in ("auth.json", "log", "sessions"):
        assert (tmp / name).is_symlink() and (tmp / name).resolve() == (home / name).resolve()
    assert (home / "log").is_dir()                  # created in the real home, so logs persist
    remove_tmp(plan)

def test_cleanup_removes_the_temp_home_and_keeps_the_real_state(tmp_path):
    home, plan = _compose(tmp_path)
    tmp, = plan.tmp_dirs
    (tmp / "sessions" / "new.jsonl").write_text("written during the run\n")
    remove_tmp(plan)
    assert not tmp.exists()
    assert (home / "auth.json").read_text() == '{"token": "secret"}'
    assert (home / "sessions" / "rollout.jsonl").read_text() == "turn\n"
    assert (home / "sessions" / "new.jsonl").read_text() == "written during the run\n"
    assert (home / "config.toml").read_text() == _SAMPLE

def test_hooks_append_after_the_users_and_keep_their_trust(tmp_path):
    real = (tmp_path / "codex-home").resolve()
    user = (_SAMPLE + '\n[[hooks.UserPromptSubmit]]\n[[hooks.UserPromptSubmit.hooks]]\n'
            'type = "command"\ncommand = "mine"\n\n[hooks.state]\n'
            f'"{real}/config.toml:user_prompt_submit:0:0" = {{ trusted_hash = "sha256:mine" }}\n'
            '"/elsewhere/hooks.json:stop:0:0" = { enabled = false }\n')
    home, plan = _compose(tmp_path, user, hooks={"UserPromptSubmit": "ccloadout.prompt_hook"},
                          hook_env={})
    tmp, = plan.tmp_dirs
    hooks = tomllib.loads((tmp / "config.toml").read_text())["hooks"]
    assert [g["hooks"][0]["command"] for g in hooks["UserPromptSubmit"]] == ["mine", _HOOK]
    assert hooks["state"] == {
        f"{tmp}/config.toml:user_prompt_submit:0:0": {"trusted_hash": "sha256:mine"},
        "/elsewhere/hooks.json:stop:0:0": {"enabled": False},
        f"{tmp}/config.toml:user_prompt_submit:1:0":
            {"trusted_hash": codex.hook_hash("UserPromptSubmit", _HOOK, 5)}}
    remove_tmp(plan)

def test_a_dropped_project_server_is_turned_off_with_its_definition(tmp_path):
    repo = tmp_path / "repo"; (repo / ".codex").mkdir(parents=True)
    (repo / ".codex" / "config.toml").write_text('[mcp_servers.db]\ncommand = "db-mcp"\n')
    config = {"projects": {str(repo): {"trust_level": "trusted"}},
              "mcp_servers": {"github": {"command": "gh"}}}
    out = codex.scoped_config(config, {"github"}, [], codex.project_servers(repo, config), None,
                              None, tmp_path, tmp_path)
    assert out["mcp_servers"] == {"github": {"command": "gh"},
                                  "db": {"command": "db-mcp", "enabled": False}}

def test_fingerprint_matches_the_codex_fixture():
    value = {"rows": [{"z": 2, "a": 1}, {"z": 4, "a": 3}], "nested": {"z": "text", "a": True}}
    assert codex.fingerprint(value) == \
        "sha256:a5e4c8fbdbc75d185b0f2b5291bfb2de10e8c523d6c89248876fd78a97c83176"

def test_hook_hash_pins_the_identity():
    assert codex.hook_hash("PostToolUse", "py -m x", 5) == codex.fingerprint(
        {"event_name": "post_tool_use",
         "hooks": [{"type": "command", "command": "py -m x", "timeout": 5, "async": False}]})

def test_toml_writer_round_trips():
    data = {"a": "line\nbreak \"q\" \x7f", "n": 3, "f": 1.5, "b": True,
            "when": dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc),
            "day": dt.date(2026, 1, 2), "inline": [1, "x", [2]], "mixed": [{"k": 1}, {"k": [2]}],
            "projects": {"/a b/c": {"trust_level": "trusted"}},
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "c"}]}],
                      "state": {"/x/config.toml:stop:0:0": {"trusted_hash": "sha256:1"}}},
            "empty": {}, "nested": {"deep": {"deeper": {"v": "w"}}}}
    assert tomllib.loads(tomlw.dumps(data)) == data

def test_codex_drops_stop_and_claude_keeps_every_event():
    assert CodexHarness.events == {"UserPromptSubmit", "PostToolUse"}
    assert harness_for("claude").events == {"UserPromptSubmit", "PostToolUse", "Stop"}

def test_codex_config_paths(tmp_path):
    h = CodexHarness()
    assert h.config_paths({"CODEX_HOME": str(tmp_path)}) == (tmp_path, tmp_path / "config.toml")
    assert h.config_paths({})[0] == Path.home() / ".codex"
    assert h.config_paths({}, tmp_path)[1] == tmp_path / "config.toml"

@pytest.mark.parametrize("argv, rest, name", [
    (["--harness", "codex", "-m", "x"], ["-m", "x"], "codex"),
    (["--harness=codex", "--explain"], ["--explain"], "codex"),
    (["rules", "--harness", "codex"], ["rules"], "codex"),
    (["-c"], ["-c"], None)])
def test_harness_switch_parses(argv, rest, name):
    assert cli._harness_switch(argv) == (rest, name)

@pytest.mark.parametrize("argv", [["--harness"], ["--harness", "codex", "init"],
                                  ["recall", "x", "--harness=codex"]])
def test_harness_switch_rejects(argv):
    with pytest.raises(ValueError):
        cli._harness_switch(argv)

def test_an_unknown_harness_fails_without_launching(monkeypatch, capsys):
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: pytest.fail("launched"))
    assert cli.main(["--harness", "aider"]) == 2
    assert "aider" in capsys.readouterr().err

def test_measure_skips_codex(capsys):
    assert cli._cmd_measure(Path("."), CodexHarness()) == 0
    assert "No measuring for Codex" in capsys.readouterr().out

def test_a_codex_launch_runs_codex_and_removes_the_temp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    home = _home(tmp_path, _SAMPLE)
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_model2vec_embed", lambda name: cli.keyword_embed)
    seen = {}
    def run(argv, **kwargs):
        if argv and argv[0] == "codex":
            seen["argv"], seen["home"] = argv, Path(kwargs["env"]["CODEX_HOME"])
            seen["config"] = tomllib.loads((seen["home"] / "config.toml").read_text())
        return type("RC", (), {"returncode": 0, "stdout": ""})()
    monkeypatch.setattr(cli.subprocess, "run", run)
    assert cli.main(["--harness", "codex", "--no-gate", "exec", "fix the github issue"]) == 0
    assert seen["argv"] == ["codex", "exec", "fix the github issue"]
    assert seen["home"] != home and not seen["home"].exists()
    assert seen["config"]["model"] == "gpt-5"
    assert (home / "auth.json").exists() and (home / "sessions" / "rollout.jsonl").exists()

@pytest.mark.parametrize("hooks", [None, {"Stop": "ccloadout.stop_hook"}])
def test_the_users_hook_trust_moves_even_without_loadout_hooks(tmp_path, hooks):
    real = (tmp_path / "codex-home").resolve()
    user = (_SAMPLE + '\n[hooks.state]\n'
            f'"{real}/hooks.json:stop:0:0" = {{ trusted_hash = "sha256:mine" }}\n')
    home, plan = _compose(tmp_path, user, hooks=hooks, hook_env={})
    tmp, = plan.tmp_dirs
    state = tomllib.loads((tmp / "config.toml").read_text())["hooks"]["state"]
    assert state == {f"{tmp}/hooks.json:stop:0:0": {"trusted_hash": "sha256:mine"}}
    remove_tmp(plan)

def test_a_planted_package_in_the_session_cwd_never_runs_as_a_hook(tmp_path):
    import subprocess
    home, plan = _compose(tmp_path, hooks={"UserPromptSubmit": "ccloadout.prompt_hook"},
                          hook_env={})
    tmp, = plan.tmp_dirs
    command = tomllib.loads((tmp / "config.toml").read_text())[
        "hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    remove_tmp(plan)
    project = tmp_path / "project"; (project / "ccloadout").mkdir(parents=True)
    marker = tmp_path / "planted-ran"
    (project / "ccloadout" / "__init__.py").write_text(
        f"open({str(marker)!r}, 'w').write('x')\n")
    out = subprocess.run(command, shell=True, cwd=project, input="{}", text=True,
                         capture_output=True, timeout=30)
    assert out.returncode == 0 and not marker.exists()

def test_a_case_variant_config_in_the_real_home_is_never_linked_or_written(tmp_path):
    home = _home(tmp_path, _SAMPLE)
    (home / "config.toml").unlink()
    (home / "Config.toml").write_text(_SAMPLE)
    if not (home / "config.toml").exists():
        pytest.skip("case-sensitive file system: Config.toml is a different file")
    plan = CodexHarness().compose([], [], home, [], environ={}, cwd=tmp_path)
    tmp, = plan.tmp_dirs
    assert not any(p.is_symlink() and p.name.casefold() == "config.toml" for p in tmp.iterdir())
    remove_tmp(plan)
    assert (home / "Config.toml").read_text() == _SAMPLE

def test_a_config_toml_symlink_in_the_temp_home_is_refused(tmp_path):
    target = tmp_path / "victim"; target.write_text("keep")
    link = tmp_path / "config.toml"; link.symlink_to(target)
    with pytest.raises(OSError):
        codex._write_new(link, "overwrite")
    assert target.read_text() == "keep"

def test_the_temp_home_lives_in_the_real_home_not_in_tmpdir(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmpdir"))
    (tmp_path / "tmpdir").mkdir()
    tempfile.tempdir = None
    try:
        home, plan = _compose(tmp_path)
        tmp, = plan.tmp_dirs
        assert not tmp.is_relative_to(Path(tempfile.gettempdir()).resolve())
        assert tmp.parent == (home / "loadout" / "run").resolve()
        assert not (tmp / "loadout").exists()
        assert ((home / "loadout" / "run").stat().st_mode & 0o777) == 0o700
        remove_tmp(plan)
        assert not tmp.exists() and (home / "auth.json").exists()
    finally:
        tempfile.tempdir = None

def test_rekey_moves_only_entries_of_the_real_home_itself(tmp_path):
    real, new = tmp_path / "home", tmp_path / "home" / "loadout" / "run" / "codex-x"
    state = {f"{real}/config.toml:stop:0:0": 1, f"{real}/hooks.json:stop:0:0": 2,
             f"{real}/loadout/run/codex-old/config.toml:stop:0:0": 3,
             "/other/config.toml:stop:0:0": 4}
    assert codex._rekey(state, (real,), new) == {
        f"{new}/config.toml:stop:0:0": 1, f"{new}/hooks.json:stop:0:0": 2,
        f"{real}/loadout/run/codex-old/config.toml:stop:0:0": 3, "/other/config.toml:stop:0:0": 4}

def test_a_project_server_forced_on_warns_that_it_cannot_be_dropped(tmp_path, capsys):
    repo = tmp_path / "repo"; (repo / ".git").mkdir(parents=True); (repo / ".codex").mkdir()
    (repo / ".codex" / "config.toml").write_text(
        '[mcp_servers.db]\ncommand = "db-mcp"\nenabled = true\n')
    home = _home(tmp_path, _SAMPLE + f'\n[projects."{repo}"]\ntrust_level = "trusted"\n')
    plan = CodexHarness().compose([], [Item("db", "mcp", "db", "")], home, [], environ={},
                                  cwd=repo)
    remove_tmp(plan)
    assert "can't drop MCP server db" in capsys.readouterr().err

def test_project_trust_matches_through_a_symlinked_path(tmp_path):
    real = tmp_path / "real-repo"; (real / ".codex").mkdir(parents=True)
    (real / ".codex" / "config.toml").write_text('[mcp_servers.db]\ncommand = "db-mcp"\n')
    alias = tmp_path / "alias"; alias.symlink_to(real, target_is_directory=True)
    config = {"projects": {str(real): {"trust_level": "trusted"}}}
    assert "db" in codex.project_servers(alias, config)

def test_a_failed_config_write_removes_the_temp_home(tmp_path, monkeypatch):
    home = _home(tmp_path, _SAMPLE)
    monkeypatch.setattr(codex, "_write_new", lambda *a: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(OSError):
        CodexHarness().compose([], [], home, [], environ={}, cwd=tmp_path)
    assert list((home / "loadout" / "run").iterdir()) == []
