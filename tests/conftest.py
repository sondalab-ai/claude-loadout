
import os
import pytest

# Every variable the Term spec reads (docs/tui-patterns.md, Robustness > Environment), plus the
# locale. Cleared so a developer's shell or a CI runner (CI=true) cannot change what a test sees.
_TERM_ENV = ("SONDALAB_REDUCED_MOTION", "SONDALAB_ACCESSIBLE", "ACCESSIBLE", "NO_COLOR",
             "FORCE_COLOR", "CI", "TERM", "COLORTERM", "SONDALAB_ASCII", "LOADOUT_NO_COLOR",
             "LC_CTYPE", "LANG", "LOADOUT_DEBUG", "CLAUDE_CONFIG_DIR")


@pytest.fixture(autouse=True)
def _term_env(monkeypatch):
    """Start every test from a UTF-8 locale with no colour, motion or ASCII switches set.

    Also clear every LOADOUT_* variable: a test run inside a cld session inherits the real
    LOADOUT_CONFIG_ROOT and LOADOUT_REPO, and a hook started by a test then writes to the real store.
    """
    for name in _TERM_ENV:
        monkeypatch.delenv(name, raising=False)
    for name in [n for n in os.environ if n.startswith("LOADOUT_")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    monkeypatch.setenv("COLUMNS", "100")                 # prose wraps at width(); pin it
