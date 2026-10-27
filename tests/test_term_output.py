"""Term spec output (docs/tui-patterns.md in sondalab-ui): env precedence, glyphs, the menu
fallback, and machine output that must stay byte-stable across restyles."""
import hashlib
import importlib
import io
import sondalab_palette as sl
from pathlib import Path

import pytest

cli = importlib.import_module("ccloadout.cli")
from test_cli import _memory_repo  # noqa: E402


def _digest(text: str, tmp_path) -> str:
    return hashlib.sha256(text.replace(str(tmp_path), "<tmp>").encode()).hexdigest()


# --- machine output ------------------------------------------------------------

def test_audit_json_bytes_are_unchanged(tmp_path, monkeypatch, capsys):
    _memory_repo(tmp_path, n=2)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "root"))
    monkeypatch.setenv("FORCE_COLOR", "1")                 # colour forced on: JSON still has none
    monkeypatch.chdir(tmp_path)
    cli.main(["memory", "audit", "--json"])
    out = capsys.readouterr().out
    assert "\033" not in out
    assert _digest(out, tmp_path) == "f8e1bd0439a6d6a12c7008426258429ebe1f4077318571aa4ec3dacff4db145f"


def test_audit_context_json_bytes_are_unchanged(tmp_path, monkeypatch, capsys):
    _memory_repo(tmp_path, n=2)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "root"))
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_model2vec_embed", lambda name: cli.keyword_embed)
    cli.main(["memory", "audit", "--context", "note number 0 about scoping sessions", "--json"])
    captured = capsys.readouterr()
    assert "\033" not in captured.out
    assert _digest(captured.out, tmp_path) == "e0f54f80fe60945e2eb8bebff1c1f688a07f065058fb7cf6651ba22e543fe457"


class _Tty(io.StringIO):
    def isatty(self):
        return True


def _keys(*seq):
    it = iter(seq)
    return lambda: next(it)


def _replies(monkeypatch, *answers):
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(it))


# --- glyphs, step lines, errors ---------------------------------------------------

def test_step_line_holds_the_label_grid():
    line = cli._t.step("done", "Wrote memory entry", "docs/memory/x.md")
    assert line == "  ✓ Wrote memory entry          docs/memory/x.md"
    assert line.index("docs") == 2 + 2 + 28                # indent, glyph + space, labelWidth


def test_ascii_mode_swaps_every_glyph(monkeypatch):
    monkeypatch.setenv("SONDALAB_ASCII", "1")
    assert cli._t.step("failed", "x").startswith("  x x")
    assert cli._t.step("skipped", "y").startswith("  - y")
    assert cli._t.sep() == " | " and cli._t.box("seam") == ">" and cli._t.arrows() == "up/dn"
    assert cli._t.ellipsis() == "..."


def test_non_utf8_locale_is_ascii(monkeypatch):
    monkeypatch.setenv("LC_ALL", "C")
    assert cli._t.glyph("done") == "+"


def test_term_dumb_is_ascii_and_colourless(monkeypatch):
    monkeypatch.setattr("sys.stdout", _Tty())
    monkeypatch.setenv("TERM", "dumb")
    assert cli._t.glyph("done") == "+"


def test_error_is_what_why_try(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "root"))
    monkeypatch.chdir(tmp_path)
    assert cli.main(["debt", "resolve", "nope"]) == 1
    err = capsys.readouterr().err.splitlines()
    assert err == ["  ✗ No debt entry named 'nope'",
                   "    no open or resolved debt entry has that exact name",
                   "    Try: claude-loadout debt list --all"]


def test_global_write_keeps_the_label_whole(tmp_path, monkeypatch, capsys):
    _memory_repo(tmp_path, n=0)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "root"))
    monkeypatch.chdir(tmp_path)
    assert cli.main(["memory", "add", "--global", "a note for everywhere"]) == 0
    line = capsys.readouterr().out.splitlines()[0]
    assert line.startswith("  ✓ Wrote memory entry ") and line.endswith("  (every project)")


def test_every_literal_step_label_fits_the_grid():
    # A label longer than labelWidth - 1 is cut mid-word by step_line; keep them short.
    import re
    src = Path(cli.__file__).read_text()
    labels = re.findall(r'(?:_say|tally\.check|_t\.step)\("[a-z]+", "([^"]*)"', src)
    assert labels and all(len(lab) <= 27 for lab in labels), labels


def test_error_block_has_three_lines(capsys):
    cli._fail("Missing the memory text", "memory add needs a description of the note",
              "claude-loadout memory add <text>")
    assert capsys.readouterr().err.splitlines() == [
        "  ✗ Missing the memory text",
        "    memory add needs a description of the note",
        "    Try: claude-loadout memory add <text>"]


def test_no_color_keeps_glyphs_and_drops_escapes(monkeypatch):
    monkeypatch.setattr("sys.stdout", _Tty())
    monkeypatch.setenv("NO_COLOR", "1")
    assert cli._t.step("done", "Resolved debt", "x") == "  ✓ Resolved debt               x"


def test_force_color_paints_the_glyph_only(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    line = cli._t.step("failed", "Could not link", "detail")
    assert line == "  \033[31m✗\033[0m Could not link              detail"   # label grid intact


def test_voice_has_no_banned_words():
    src = (Path(cli.__file__)).read_text()
    for banned in ("Successfully", "Oops", "'claude-loadout:'"):   # no program-name prefix
        assert banned not in src


def test_prompts_go_to_stderr_as_questions(tmp_path, monkeypatch, capsys):
    _replies(monkeypatch, "")
    assert cli._confirm("Link now?") is False             # Enter takes the default, no
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "? Link now? [y/N] "


# --- motion -------------------------------------------------------------------------

def _wait(monkeypatch, *, tty: bool, **env):
    """Run one Waiting step synchronously past its delay (delayMs patched to 0, no sleeps)."""
    err = _Tty() if tty else io.StringIO()
    monkeypatch.setattr("sys.stderr", err)
    monkeypatch.setitem(sl.SPINNER, "delayMs", 0)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    w = cli._t.Waiting("Measuring 2 MCP servers…", total=2)
    w.advance(1)
    if w._motion:                                          # two frames, drawn by hand
        w._draw(); w._tick += 1; w._draw()
    else:
        w._run()                                           # motion off: returns after one line
    return err.getvalue()


def test_spinner_draws_frames_and_the_bar_on_a_tty(monkeypatch):
    out = _wait(monkeypatch, tty=True)
    assert out.count("\033[?2026h") == 2 and "Measuring 2 MCP servers…" in out
    assert "\033[2F" in out                                # 2nd frame rewinds inside the sync block
    assert "░" in out and " 1/2" in out and "50%" in out   # frame 1 of the burst; the bar's count


def test_emit_prints_above_the_live_lines(monkeypatch, capsys):
    err = _Tty()
    monkeypatch.setattr("sys.stderr", err)
    w = cli._t.Waiting("Measuring 1 MCP server…", total=1)
    w._draw()                                              # two live lines on stderr
    w.emit("  ✓ Context7")
    assert capsys.readouterr().out == "  ✓ Context7\n"     # result on stdout
    tail = err.getvalue().split("\033[2F\033[J", 1)[1]     # cleared, then redrawn below it
    assert "Measuring 1 MCP server…" in tail and w._drawn == 2


def test_step_cuts_a_long_server_id_at_the_label_width():
    line = cli._t.step("done", "plugin:playwright:playwright", "~1k tok")
    assert line == "  ✓ plugin:playwright:playwrigh ~1k tok"   # cut at labelWidth - 1 = 27


def test_recall_bytes_ignore_ascii_mode(tmp_path, monkeypatch, capsys):
    _memory_repo(tmp_path, n=1)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "root"))
    monkeypatch.setenv("SONDALAB_ASCII", "1")
    monkeypatch.chdir(tmp_path)
    cli.main(["recall"])
    assert capsys.readouterr().out == ("  [memory · repo] m0 — note number 0 about scoping "
                                       "sessions\n")


def _fake_termios(monkeypatch, read, ready=lambda: False):
    """A raw terminal on fd 0: os.read() yields `read()`, select() reports `ready()`."""
    import select, termios, tty
    calls = []
    monkeypatch.setattr(termios, "tcgetattr", lambda fd: "saved")
    monkeypatch.setattr(termios, "tcsetattr", lambda fd, when, old: calls.append(old))
    monkeypatch.setattr(tty, "setraw", lambda fd: None)
    monkeypatch.setattr(cli.sys, "stdin", type("S", (), {"fileno": lambda self: 0})())
    monkeypatch.setattr(cli.os, "read", lambda fd, n: read())
    monkeypatch.setattr(select, "select", lambda r, w, x, t: (r if ready() else [], [], []))
    return calls


def test_read_key_restores_the_terminal_on_ctrl_c(monkeypatch):
    calls = _fake_termios(monkeypatch, lambda: b"\x03")
    assert cli._read_key() == "\x03" and calls == ["saved"]


def test_read_key_restores_the_terminal_on_an_exception(monkeypatch):
    calls = _fake_termios(monkeypatch, lambda: (_ for _ in ()).throw(OSError("gone")))
    with pytest.raises(OSError):
        cli._read_key()
    assert calls == ["saved"]


def test_lone_esc_returns_without_waiting_for_more_bytes(monkeypatch):
    _fake_termios(monkeypatch, iter([b"\x1b"]).__next__)  # a second read would raise
    assert cli._read_key() == "esc"


def test_arrow_sequence_reads_as_a_key(monkeypatch):
    data = iter([b"\x1b", b"[", b"A"])
    pending = [2]
    def ready():
        pending[0] -= 1
        return pending[0] >= 0
    _fake_termios(monkeypatch, data.__next__, ready)
    assert cli._read_key() == "up"


def test_end_of_input_reads_as_esc(monkeypatch):
    _fake_termios(monkeypatch, lambda: b"")              # a hung-up terminal ends the menu
    assert cli._read_key() == "esc"


@pytest.mark.parametrize("env", [{"TERM": "dumb"}, {"CI": ""}])
def test_no_key_reader_under_dumb_terminal_or_ci(monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert cli._can_raw() is False


@pytest.mark.parametrize("env", [{"SONDALAB_REDUCED_MOTION": "1"}, {"CI": "true"},
                                 {"SONDALAB_ACCESSIBLE": "1"}, {"ACCESSIBLE": ""},
                                 {"TERM": "dumb"}])
def test_motion_off_prints_the_running_line_once(monkeypatch, env):
    out = _wait(monkeypatch, tty=True, **env)
    import re
    assert not re.search(r"\033\[(\?2026|\d*[FJK])", out)    # no redraw, no cursor movement
    plain = re.sub(r"\033\[[\d;]*m", "", out)
    assert plain in ("  ● Measuring 2 MCP servers…\n", "  * Measuring 2 MCP servers...\n")


def test_non_tty_stderr_has_no_motion(monkeypatch):
    out = _wait(monkeypatch, tty=False)
    assert out == "  ● Measuring 2 MCP servers…\n"


def test_quick_step_prints_no_running_line(monkeypatch):
    err = io.StringIO()
    monkeypatch.setattr("sys.stderr", err)
    with cli._t.Waiting("Quick…"):
        pass
    assert err.getvalue() == ""


# --- the menu -------------------------------------------------------------------------

def test_menu_marks_the_cursor_row_with_the_seam():
    out = io.StringIO()
    cli._render_checklist("t", ["a", "b"], [True, False], 1, out, 0)
    rows = [ln.replace("\033[K", "") for ln in out.getvalue().split("\n")]
    assert " ".join(rows).count("▌") == 1 and any(r.startswith("▌  [ ] b") for r in rows)
    assert "↑↓ move · space toggle" in out.getvalue() and "ctrl-c quit" in out.getvalue()
    assert "q back" in out.getvalue()                      # the shared verb, not "cancel"


def test_menu_hint_paints_keys_accent_and_verbs_dim(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    footer = cli._hint_line(((cli._MOVE, "move"),))
    assert footer.startswith("\033[36m↑↓\033[0m \033[90mmove\033[0m")


def test_accessible_mode_uses_the_numbered_fallback(monkeypatch, capsys):
    monkeypatch.setenv("SONDALAB_ACCESSIBLE", "1")
    monkeypatch.setattr(cli, "_can_raw", lambda: True)    # a raw terminal is there; not used
    monkeypatch.setattr(cli, "_read_key", lambda: pytest.fail("no key reader in accessible mode"))
    _replies(monkeypatch, "2", "")                         # toggle row 2 off, then confirm
    assert cli._checkbox_select("Pick", ["a", "b", "c"]) == [0, 2]
    err = capsys.readouterr().err
    assert "\033[" not in err                              # no redraw
    assert "  1  [x] on   a" in err and "  2  [ ] off  b" in err   # the state as a word
    assert "? Toggle which?" in err


def test_numbered_fallback_without_a_raw_terminal(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_can_raw", lambda: False)
    _replies(monkeypatch, "x", "all", "")                  # invalid, then flip all off, confirm
    assert cli._checkbox_select("Pick", ["a", "b"], allow_empty=True) == []
    assert "Not a valid answer: 'x'" in capsys.readouterr().err


def test_numbered_fallback_cancels_on_q_and_fails_on_eof(monkeypatch):
    monkeypatch.setattr(cli, "_can_raw", lambda: False)
    _replies(monkeypatch, "q")
    assert cli._checkbox_select("Pick", ["a"]) is None
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(EOFError()))
    with pytest.raises(cli._slt.NoValidAnswer):            # end of input is not an answer
        cli._checkbox_select("Pick", ["a"])


def test_accessible_review_offers_the_list_instead_of_accepting(monkeypatch):
    from ccloadout.inventory import Item
    monkeypatch.setenv("SONDALAB_ACCESSIBLE", "1")
    monkeypatch.setattr(cli, "_can_raw", lambda: False)   # before: silently took the auto pick
    _replies(monkeypatch, "1", "")
    items = [Item("Gmail", "mcp", "Gmail", "email"), Item("figma@x", "plugin", "figma", "d")]
    assert cli._review_keep_drop(Path("/r"), items, {"Gmail", "figma@x"}) == {"figma@x"}


def test_accessible_repo_picker_is_the_numbered_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESSIBLE", "")                   # Charm's name: set means on
    monkeypatch.setattr(cli, "_can_raw", lambda: True)
    monkeypatch.setattr(cli, "_checkbox_select", lambda *a, **k: pytest.fail("no arrow menu"))
    monkeypatch.setattr("builtins.input", lambda *a, **k: "1")
    assert cli._select_repos([tmp_path], 0) == [tmp_path]


def test_non_tty_init_asks_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("no prompt without a TTY"))
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init"]) == 2
    assert "Try: claude-loadout init --yes" in capsys.readouterr().err


# --- the header -------------------------------------------------------------------------

def _tty_stdout(monkeypatch):
    """stdout as a 120-column TTY, colour off, so the header's text is compared byte for byte."""
    out = _Tty()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setenv("COLUMNS", "120")
    monkeypatch.setenv("NO_COLOR", "1")
    return out


def test_help_on_a_tty_opens_with_the_full_header(monkeypatch):
    out = _tty_stdout(monkeypatch)
    cli.main(["--help"])
    lines = out.getvalue().splitlines()
    assert lines[0] == sl.HEADER["mark"][0] + "  sonda/lab"
    assert lines[1].endswith(f"  cld {cli._loadout_version()}")
    assert lines[2].endswith("  " + cli._TAGLINE)
    assert "USAGE" in out.getvalue()                       # the help itself is still printed


def test_one_shot_command_on_a_tty_opens_with_the_compact_header(tmp_path, monkeypatch):
    _memory_repo(tmp_path, n=1)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "root"))
    monkeypatch.chdir(tmp_path)
    out = _tty_stdout(monkeypatch)
    cli.main(["debt", "list"])
    assert out.getvalue().startswith(sl.HEADER["compact"] + " sonda/lab cld\n\n")


def test_json_output_has_no_header_even_on_a_tty(tmp_path, monkeypatch):
    _memory_repo(tmp_path, n=2)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "root"))
    monkeypatch.chdir(tmp_path)
    out = _tty_stdout(monkeypatch)
    cli.main(["memory", "audit", "--json"])
    assert "sonda" not in out.getvalue()
    assert out.getvalue().lstrip().startswith("[")


@pytest.mark.parametrize("argv", [["--help"], ["debt", "list"]])
def test_no_header_when_stdout_is_not_a_tty(tmp_path, monkeypatch, capsys, argv):
    _memory_repo(tmp_path, n=1)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "root"))
    monkeypatch.chdir(tmp_path)
    cli.main(argv)
    out = capsys.readouterr().out
    assert "sonda/lab" not in out and sl.HEADER["compact"] not in out


def test_accessible_mode_has_no_header(monkeypatch):
    out = _tty_stdout(monkeypatch)
    monkeypatch.setenv("SONDALAB_ACCESSIBLE", "1")
    cli.main(["--help"])
    assert "sonda/lab" not in out.getvalue()


# --- no VT (a Windows console that refuses it): plain text ------------------------------------

def _vt(monkeypatch, *, out: bool, err: bool, out_tty=True, err_tty=True):
    """enable_vt() fails on the streams marked False; each stream a TTY or not."""
    stdout, stderr = (_Tty() if out_tty else io.StringIO()), (_Tty() if err_tty else io.StringIO())
    monkeypatch.setattr("sys.stdout", stdout)
    monkeypatch.setattr("sys.stderr", stderr)
    monkeypatch.setenv("FORCE_COLOR", "1")                 # colour would be on everywhere
    monkeypatch.setattr(cli._t, "_vt", {"out": True, "err": True})   # restored after the test
    monkeypatch.setattr(cli._slt, "enable_vt",
                        lambda stream=None: {id(stdout): out, id(stderr): err}[id(stream)])
    return stdout, stderr


def test_without_vt_output_is_plain_ascii(tmp_path, monkeypatch):
    _memory_repo(tmp_path, n=0)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "root"))
    monkeypatch.chdir(tmp_path)
    stdout, stderr = _vt(monkeypatch, out=False, err=False)
    cli.main(["debt", "add", "--name", "m0", "a shim left behind"])
    cli.main(["debt", "list"])
    out = stdout.getvalue()
    assert "\033" not in out and out.isascii() and "m0" in out
    w = cli._t.Waiting("Measuring 1 MCP server...", total=1)
    w._draw()                                              # even a forced frame: no sync, no colour
    assert "\033[?2026" not in stderr.getvalue() and "\033[3" not in stderr.getvalue()
    assert cli._can_raw() is False and w._motion is False


def test_only_stderr_without_vt_leaves_stdout_coloured(monkeypatch):
    _vt(monkeypatch, out=True, err=False)
    cli._t.enable_vt()
    assert cli._paint("x", "green") == "\033[32mx\033[0m"                 # stdout keeps colour
    assert cli._paint("x", "green", err=True) == "x"                       # stderr is plain
    assert cli._t.sync("f") == "f" and cli._can_raw() is False and not cli._t.motion_on()
    assert cli._t.ascii_on()                               # glyphs are one choice per process


def test_a_redirected_stream_never_counts_as_a_vt_failure(monkeypatch):
    # Off a console (piped, redirected) enable_vt has nothing to enable: no plain-text fallout.
    _vt(monkeypatch, out=False, err=True, out_tty=False)
    assert cli._t.enable_vt() is True
    assert cli._paint("x", "green") == "\033[32mx\033[0m"                 # FORCE_COLOR still wins
    assert not cli._t.ascii_on() and cli._t.sync("f") != "f"


# --- the scoping spinner ----------------------------------------------------------------------

def test_slow_scoping_shows_a_running_line_and_ends_in_a_step(tmp_path, monkeypatch, capsys):
    import time

    from test_cli import _root
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(_root(tmp_path)))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sl.SPINNER, "delayMs", 0)
    def slow(name):
        time.sleep(0.05)
        return cli.keyword_embed
    monkeypatch.setattr(cli, "make_model2vec_embed", slow)
    cli.main(["--explain"])
    err = capsys.readouterr().err.splitlines()
    assert err[0].startswith("  ● Ranking ") and err[-1].startswith("  ✓ Ranked ")
