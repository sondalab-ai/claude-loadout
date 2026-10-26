
import pytest

# Every variable the Term spec reads (docs/tui-patterns.md, Robustness > Environment), plus the
# locale. Cleared so a developer's shell or a CI runner (CI=true) cannot change what a test sees.
_TERM_ENV = ("SONDALAB_REDUCED_MOTION", "SONDALAB_ACCESSIBLE", "ACCESSIBLE", "NO_COLOR",
             "FORCE_COLOR", "CI", "TERM", "COLORTERM", "SONDALAB_ASCII", "LOADOUT_NO_COLOR",
             "LC_CTYPE", "LANG")


@pytest.fixture(autouse=True)
def _term_env(monkeypatch):
    """Start every test from a UTF-8 locale with no colour, motion or ASCII switches set."""
    for name in _TERM_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
