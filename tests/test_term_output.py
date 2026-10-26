"""Term spec output (docs/tui-patterns.md in sondalab-ui): env precedence, glyphs, the menu
fallback, and machine output that must stay byte-stable across restyles."""
import hashlib
import importlib
import io
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
    for banned in ("Successfully", "Oops", "⚠", "'claude-loadout:'"):   # no program-name prefix
        assert banned not in src


def test_prompts_go_to_stderr_as_questions(tmp_path, monkeypatch, capsys):
    _replies(monkeypatch, "")
    assert cli._confirm("Link now?") is False             # Enter takes the default, no
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "? Link now? [y/N] "


# --- motion -------------------------------------------------------------------------

def _wait(monkeypatch, *, tty: bool, **env):
    err = _Tty() if tty else io.StringIO()
    monkeypatch.setattr("sys.stderr", err)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with cli._t.Waiting("Measuring 2 MCP servers…", total=2) as w:
        import time
        time.sleep(0.5)                                    # outlive spinner.delayMs (300ms)
        w.advance(1)
    return err.getvalue()


def test_spinner_draws_after_the_delay_on_a_tty(monkeypatch):
    out = _wait(monkeypatch, tty=True)
    assert "\033[?2026h" in out and "Measuring 2 MCP servers…" in out
    assert any(f in out for f in "░▒▓█") and "/2" in out    # a spinner frame and the bar's count


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


def test_menu_hint_paints_keys_accent_and_verbs_dim(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    footer = cli._hint_line(((cli._MOVE, "move"),))
    assert footer.startswith("\033[36m↑↓\033[0m \033[2mmove\033[0m")


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
    assert "Not a valid choice: 'x'" in capsys.readouterr().err


def test_numbered_fallback_cancels_on_q_and_eof(monkeypatch):
    monkeypatch.setattr(cli, "_can_raw", lambda: False)
    _replies(monkeypatch, "q")
    assert cli._checkbox_select("Pick", ["a"]) is None
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(EOFError()))
    assert cli._checkbox_select("Pick", ["a"]) is None


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
    monkeypatch.setattr(cli, "_ask", lambda prompt: "1")
    assert cli._select_repos([tmp_path], 0) == [tmp_path]


def test_non_tty_init_asks_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("no prompt without a TTY"))
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init"]) == 0
    assert "Try: claude-loadout init --yes" in capsys.readouterr().err
