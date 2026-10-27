"""Prompts and exits on the Term spec (TP Confirmation, Destructive actions, Non-TTY: no prompts).

Unrecognised input is never read as a choice: it re-asks, at most promptRetries times in all,
then fails with "no valid answer". End of input fails at once. An irreversible delete needs the
exact name of what it deletes. Without a TTY, a command that would ask stops non-zero.
"""
from pathlib import Path

import pytest
import sondalab_palette as sl
from sondalab_palette import term as slt

import ccloadout.cli as cli
from test_cli import _RC, _fake_stdin, _memory_repo, _proj, _root, _two_profiles

RETRIES = sl.ROBUSTNESS["promptRetries"]


def _replies(monkeypatch, *answers):
    """Feed input() these answers in order; past the end, input() hits end of input."""
    it = iter(answers)

    def _input(*a, **k):
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None
    monkeypatch.setattr("builtins.input", _input)


# --- the [y/N] confirm ------------------------------------------------------------------

def test_confirm_reasks_unrecognised_input_then_takes_the_answer(monkeypatch, capsys):
    _replies(monkeypatch, "maybe", "y")
    assert cli._confirm("Link now?") is True
    assert capsys.readouterr().err.count("? Link now? [y/N] ") == 2


def test_confirm_fails_after_the_retry_cap(monkeypatch):
    _replies(monkeypatch, *["maybe"] * RETRIES, "y")
    with pytest.raises(slt.NoValidAnswer):
        cli._confirm("Link now?")


def test_confirm_end_of_input_never_takes_the_default(monkeypatch):
    _replies(monkeypatch)
    with pytest.raises(slt.NoValidAnswer):
        cli._confirm("Link now?")


def test_confirm_enter_takes_the_default(monkeypatch):
    _replies(monkeypatch, "")
    assert cli._confirm("Link now?") is False


# --- letter menus and numbered lists ------------------------------------------------------

def _gate_repo(tmp_path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.setenv("LOADOUT_THRESHOLD", "0.99")        # everything dropped: the gate shows
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_model2vec_embed", lambda name: cli.keyword_embed)
    monkeypatch.setattr(cli, "_interactive", lambda p: True)


def test_launch_gate_does_not_launch_on_unrecognised_input(tmp_path, monkeypatch, capsys):
    _gate_repo(tmp_path, monkeypatch)
    _replies(monkeypatch, *["yes please"] * RETRIES)
    launched = []
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **k: launched.append(argv) or _RC(0))
    assert cli.main([]) == 2
    assert not [a for a in launched if a and a[0] == "claude"]
    assert "No valid answer to: Launch now?" in capsys.readouterr().err


def test_launch_gate_launches_on_enter_after_a_bad_answer(tmp_path, monkeypatch):
    _gate_repo(tmp_path, monkeypatch)
    _replies(monkeypatch, "x", "")
    launched = []
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **k: launched.append(argv) or _RC(0))
    assert cli.main([]) == 0
    assert any(a and a[0] == "claude" for a in launched)


def test_launch_gate_end_of_input_does_not_launch(tmp_path, monkeypatch):
    _gate_repo(tmp_path, monkeypatch)
    _replies(monkeypatch)
    launched = []
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **k: launched.append(argv) or _RC(0))
    assert cli.main([]) == 2
    assert not [a for a in launched if a and a[0] == "claude"]


def test_maybe_persist_edit_is_a_capped_confirm(monkeypatch):
    seeded = []
    monkeypatch.setattr(cli, "_seed_repo", lambda *a: seeded.append(a))
    _replies(monkeypatch, "sure", "y")                    # "sure" is not yes
    gate = cli._EditGate([], [], set(), set(), None, Path("/r"), None, "g")
    cli._maybe_persist_edit(gate, {"x"})
    assert len(seeded) == 1


def test_keep_drop_skip_reasks_instead_of_skipping(monkeypatch):
    from ccloadout.inventory import Item
    _replies(monkeypatch, "keep it", "d")
    pred = cli._pick_keep_drop_skip(Item("Gmail", "mcp", "Gmail", "email"))
    assert pred is not None and pred.action == "always_drop"


def test_keep_drop_skip_enter_skips(monkeypatch):
    from ccloadout.inventory import Item
    _replies(monkeypatch, "")
    assert cli._pick_keep_drop_skip(Item("Gmail", "mcp", "Gmail", "email")) is None


def test_keep_drop_skip_fails_after_the_cap(monkeypatch):
    from ccloadout.inventory import Item
    _replies(monkeypatch, *["?"] * RETRIES)
    with pytest.raises(slt.NoValidAnswer):
        cli._pick_keep_drop_skip(Item("Gmail", "mcp", "Gmail", "email"))


def test_profile_prompt_is_capped(tmp_path, monkeypatch):
    _two_profiles(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_interactive", lambda p: True)
    _replies(monkeypatch, *["9"] * RETRIES, "1")
    with pytest.raises(slt.NoValidAnswer):
        cli._resolve_config_root({}, [])


def test_pick_profile_is_capped_and_enter_takes_the_default(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    _replies(monkeypatch, "")
    assert cli._pick_profile([a, b], b, tmp_path) == b
    _replies(monkeypatch, "zero", "1")
    assert cli._pick_profile([a, b], b, tmp_path) == a
    _replies(monkeypatch, *["7"] * RETRIES)
    with pytest.raises(slt.NoValidAnswer):
        cli._pick_profile([a, b], b, tmp_path)


def test_repo_list_prompt_is_capped(tmp_path, monkeypatch):
    _replies(monkeypatch, *["nine"] * RETRIES)
    with pytest.raises(slt.NoValidAnswer):
        cli._select_repos_line([tmp_path], 0)


def test_numbered_checklist_caps_consecutive_bad_answers(monkeypatch):
    monkeypatch.setattr(cli, "_can_raw", lambda: False)
    _replies(monkeypatch, "x", "1", *["x"] * RETRIES)      # a valid toggle resets the count
    with pytest.raises(slt.NoValidAnswer):
        cli._checkbox_select("Pick", ["a", "b"])


def test_consolidate_reasks_instead_of_keeping(tmp_path, monkeypatch, capsys):
    from ccloadout.candidates import record_session
    root = _memory_repo(tmp_path, n=0)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_interactive", lambda p: True)
    record_session(root, tmp_path, goal="g", exit_code=0, changed=[])
    _replies(monkeypatch, *["dunno"] * RETRIES)
    assert cli.main(["memory", "consolidate"]) == 2


# --- the irreversible delete --------------------------------------------------------------

def _audit(tmp_path, monkeypatch, keep):
    _memory_repo(tmp_path, n=2)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "root"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_interactive", lambda passthrough: True)
    monkeypatch.setattr(cli, "_checkbox_select", lambda *a, **k: keep)
    return tmp_path / "docs" / "memory"


def test_delete_needs_the_exact_name(tmp_path, monkeypatch, capsys):
    mem = _audit(tmp_path, monkeypatch, [0])               # keep m0, delete m1
    _replies(monkeypatch, "y", "delete")                  # the old magic word no longer works
    assert cli.main(["memory", "audit"]) == 0
    assert (mem / "m1.md").exists()
    _replies(monkeypatch, "y", "M1")                       # exact: case matters
    cli.main(["memory", "audit"])
    assert (mem / "m1.md").exists()
    _replies(monkeypatch, "y", "m1")
    cli.main(["memory", "audit"])
    assert not (mem / "m1.md").exists() and (mem / "m0.md").exists()
    assert "Type m1 to delete it permanently" in capsys.readouterr().err


def test_delete_of_several_needs_the_count_phrase(tmp_path, monkeypatch, capsys):
    mem = _audit(tmp_path, monkeypatch, [])                # delete both
    _replies(monkeypatch, "y", "deleted")
    cli.main(["memory", "audit"])
    assert (mem / "m0.md").exists() and (mem / "m1.md").exists()
    _replies(monkeypatch, "y", "2 entries")
    cli.main(["memory", "audit"])
    assert not (mem / "m0.md").exists() and not (mem / "m1.md").exists()


def test_delete_asks_yes_no_first_default_no(tmp_path, monkeypatch, capsys):
    mem = _audit(tmp_path, monkeypatch, [0])
    _replies(monkeypatch, "")                              # Enter: default no, nothing typed
    assert cli.main(["memory", "audit"]) == 0
    assert (mem / "m1.md").exists()
    assert "? Delete 1 entry? [y/N]" in capsys.readouterr().err


# --- no TTY: no prompt, non-zero ------------------------------------------------------------

@pytest.mark.parametrize("cmd", ["init", "update"])
def test_non_tty_without_yes_exits_non_zero(tmp_path, monkeypatch, capsys, cmd):
    root = _root(tmp_path); monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    repos = tmp_path / "repos"; r1 = _proj(repos, "proj1")
    if cmd == "update":
        (r1 / ".loadout").mkdir(); (r1 / ".loadout" / "config.toml").write_text("")
    _fake_stdin(monkeypatch, tty=False)
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("no prompt without a TTY"))
    assert cli.main([cmd, str(repos)]) == 2
    err = capsys.readouterr().err.splitlines()
    assert err[0].startswith("  ✗ ") and "--yes" in err[1]
    assert err[-1] == f"    Try: claude-loadout {cmd} {repos} --yes"


@pytest.mark.parametrize("cmd", ["init", "update"])
def test_root_that_is_not_a_directory_exits_non_zero(tmp_path, monkeypatch, capsys, cmd):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(_root(tmp_path)))
    assert cli.main([cmd, str(tmp_path / "nope"), "--yes"]) == 2
    assert "is not a directory" in " ".join(capsys.readouterr().err.split())


# --- unexpected failures: three lines, traceback only with --debug ---------------------------

def _boom(*a, **k):
    raise RuntimeError("boom")


def test_unexpected_error_is_the_three_line_block(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_cmd_doctor", _boom)
    assert cli.main(["doctor"]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert err.splitlines() == ["  ✗ Unexpected error in claude-loadout",
                                "    RuntimeError: boom",
                                "    Try: claude-loadout doctor --debug"]


def test_debug_on_a_subcommand_shows_the_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    seen = []
    monkeypatch.setattr(cli, "_cmd_recall", lambda cwd, args, override: seen.append(args) or _boom())
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(_root(tmp_path)))
    assert cli.main(["recall", "--debug", "q"]) == 1
    assert seen == [["q"]]                                 # --debug is cld's here, not the query's
    err = capsys.readouterr().err
    assert "Traceback" in err and "RuntimeError: boom" in err


def test_debug_on_the_launch_path_stays_claudes(tmp_path, monkeypatch, capsys):
    # claude has its own --debug: it passes through, and cld's switch is --loadout-debug.
    root = _root(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_model2vec_embed", lambda name: cli.keyword_embed)
    seen = {}
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **k: (seen.setdefault("argv", argv), _RC(0))[1])
    assert cli.main(["--debug", "--loadout-debug", "-p", "hi"]) == 0
    assert "--debug" in seen["argv"] and "--loadout-debug" not in seen["argv"]


def test_launch_path_fix_names_loadout_debug(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(_root(tmp_path)))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_launch_gate", _boom)
    monkeypatch.setattr(cli, "make_model2vec_embed", lambda name: cli.keyword_embed)
    assert cli.main(["-p", "hi there"]) == 1
    err = capsys.readouterr().err.splitlines()
    assert err[-1] == "    Try: claude-loadout -p 'hi there' --loadout-debug"


def test_loadout_debug_env_shows_the_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOADOUT_DEBUG", "1")
    monkeypatch.setattr(cli, "_cmd_doctor", _boom)
    assert cli.main(["doctor"]) == 1
    assert "Traceback" in capsys.readouterr().err


def test_ctrl_c_is_not_an_unexpected_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_cmd_doctor", lambda cwd: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert cli.main(["doctor"]) == 130
