"""Contract for the Sondalab palette integration in cli._paint / cli._sgr.

The named-ANSI floor must stay byte-identical to the pre-adoption static codes
(so non-truecolor terminals see no change); truecolor upgrades to brand hues;
NO_COLOR / non-tty still disables color entirely (handled by _supports_color).
"""
import importlib

import pytest

cli = importlib.import_module("ccloadout.cli")


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


def test_no_color_disables(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert cli._paint("x", "green") == "x"
