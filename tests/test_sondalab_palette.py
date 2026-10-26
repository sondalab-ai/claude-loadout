"""Contract for the Sondalab palette integration in cli._paint / cli._sgr.

The named-ANSI floor must stay byte-identical to the pre-adoption static codes (so
non-truecolor terminals see no change); truecolor upgrades to brand hues, because cld opts
in. Whether colour is on at all follows the Term spec's D7 order (palette 0.6
`term.color_mode`): NO_COLOR, then FORCE_COLOR, then the stream's TTY state and TERM=dumb.
Before palette 0.6 a non-TTY always meant no colour; FORCE_COLOR now overrides that.
"""
import importlib
import io

import pytest

cli = importlib.import_module("ccloadout.cli")


class _Tty(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize(
    "name,expected",
    [("green", "32"), ("yellow", "33"), ("cyan", "36"), ("red", "31"), ("bold", "1"), ("dim", "2")],
)
def test_named_floor_matches_legacy_codes(monkeypatch, name, expected):
    monkeypatch.delenv("COLORTERM", raising=False)
    assert cli._sgr(name) == expected


def test_truecolor_upgrades_color_roles(monkeypatch):
    monkeypatch.setenv("COLORTERM", "truecolor")
    assert cli._sgr("green") == "38;2;98;208;148"   # ok  -> #62D094
    assert cli._sgr("cyan") == "38;2;53;160;180"    # accent -> #35A0B4


def test_styles_never_truecolor(monkeypatch):
    monkeypatch.setenv("COLORTERM", "truecolor")
    assert cli._sgr("bold") == "1"
    assert cli._sgr("dim") == "2"


def test_prompt_role_is_on_the_floor(monkeypatch):
    assert cli._sgr("prompt") == "93"               # spinner, seam, `?` and `Try:`


def test_no_color_disables(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert cli._paint("x", "green") == "x"


def test_tty_paints(monkeypatch):
    monkeypatch.setattr("sys.stdout", _Tty())
    assert cli._paint("x", "green") == "\033[32mx\033[0m"


def test_non_tty_does_not_paint():
    assert cli._paint("x", "green") == "x"          # pytest's captured stdout is not a TTY


def test_force_color_paints_without_a_tty(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert cli._paint("x", "green") == "\033[32mx\033[0m"


@pytest.mark.parametrize("value", ["0", ""])
def test_force_color_zero_or_empty_does_not_apply(monkeypatch, value):
    monkeypatch.setenv("FORCE_COLOR", value)
    assert cli._paint("x", "green") == "x"


def test_no_color_beats_force_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert cli._paint("x", "green") == "x"


def test_term_dumb_disables_on_a_tty(monkeypatch):
    monkeypatch.setattr("sys.stdout", _Tty())
    monkeypatch.setenv("TERM", "dumb")
    assert cli._paint("x", "green") == "x"


def test_loadout_no_color_still_disables(monkeypatch):
    monkeypatch.setattr("sys.stdout", _Tty())
    monkeypatch.setenv("LOADOUT_NO_COLOR", "1")
    assert cli._paint("x", "green") == "x"


def test_truecolor_only_with_colorterm(monkeypatch):
    monkeypatch.setattr("sys.stdout", _Tty())
    monkeypatch.setenv("COLORTERM", "24bit")
    assert cli._paint("x", "green") == "\033[38;2;98;208;148mx\033[0m"
