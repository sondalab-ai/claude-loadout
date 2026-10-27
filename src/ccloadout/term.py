"""Terminal output on the Sondalab Term spec (docs/tui-patterns.md in sondalab-ui).

Thin bindings over `sondalab_palette.term` (0.9): colour, glyphs, step and report lines, the
error block, the summary, key hints and prose wrapping all come from the palette, called with
cld's environment (LOADOUT_NO_COLOR folded into NO_COLOR, plain text when VT is unavailable).
The waiting spinner stays here: the palette has no runner by design.
"""
from __future__ import annotations

import os
import sys
import threading
import time

import sondalab_palette as sl
from sondalab_palette import term as slt

# `bold` is a text style, not a palette role. `dim` is the palette's `dim` role (bright black on
# the ANSI floor), never SGR 2 faint.
_BOLD = "\033[1m"
# The colour names cli.py has always used, kept as aliases of the palette roles.
_ALIAS = {"green": "ok", "red": "err", "yellow": "warn", "cyan": "accent"}
SYNC_ON, SYNC_OFF = "\033[?2026h", "\033[?2026l"     # DEC 2026 synchronized output

_vt = True                                           # False once enable_vt() fails: plain text


def enable_vt() -> bool:
    """Turn on VT processing for stdout and stderr (Windows); False means plain text from now on.

    Both streams are tried. When either fails, every later call writes no escape sequences, no
    motion and ASCII glyphs (TP Windows). A no-op returning True off Windows.
    """
    global _vt
    out_ok = slt.enable_vt(sys.stdout)
    err_ok = slt.enable_vt(sys.stderr)
    _vt = out_ok and err_ok
    return _vt


def vt_ok() -> bool:
    """Whether escape sequences may be written (enable_vt() has not failed)."""
    return _vt


def _env() -> dict:
    """os.environ with LOADOUT_NO_COLOR folded into NO_COLOR, and plain text forced without VT."""
    env = dict(os.environ)
    if env.get("LOADOUT_NO_COLOR") and not env.get("NO_COLOR"):
        env["NO_COLOR"] = "1"
    if not _vt:
        env.update(NO_COLOR="1", SONDALAB_ASCII="1", SONDALAB_REDUCED_MOTION="1")
    return env


def color_mode(err: bool = False) -> str:
    """'none', 'ansi16' or 'truecolor' for stdout (or stderr), by the spec's colour order.

    NO_COLOR, then FORCE_COLOR, then the stream's TTY state and TERM=dumb. cld opts in to
    truecolor, so COLORTERM=truecolor|24bit upgrades the roles to the brand hues. Read on every
    call, never cached, so pytest's capsys stream swap is honoured.
    """
    stream = sys.stderr if err else sys.stdout
    return slt.color_mode(_env(), isatty=stream.isatty(), truecolor=True)


def paint(text: str, *codes: str, err: bool = False) -> str:
    """`text` in a palette role (or its legacy alias), optionally `bold`, when colour is on.

    The role goes through the palette's painter; at most one role is used. `bold` wraps the
    result in SGR 1. With colour off for that stream, `text` comes back unchanged.
    """
    mode = color_mode(err)
    if mode == "none" or not text:
        return text
    roles = [_ALIAS.get(c, c) for c in codes if c != "bold"]
    out = slt.paint(text, roles[0], mode) if roles else text
    return f"{_BOLD}{out}\033[0m" if "bold" in codes else out


def ascii_on() -> bool:
    """True when every glyph takes its ASCII form (non-UTF-8 locale, TERM=dumb, SONDALAB_ASCII=1)."""
    return slt.ascii_mode(_env())


def accessible() -> bool:
    """Accessible mode: SONDALAB_ACCESSIBLE=1 or ACCESSIBLE set. Plain prompts, no redraw."""
    return slt.accessible(os.environ)


def header(name: str, version: str, description: str, *, compact: bool = False,
           machine: bool = False) -> str:
    """The Sondalab header for stdout, or "" when nothing should print.

    The palette returns "" for machine output, accessible mode and a stdout that is not a TTY,
    and the lockup on TERM=dumb, in ASCII mode or on a terminal too narrow for the mark.
    """
    return slt.header(name, version, description, compact=compact, machine=machine, env=_env())


def lockup(name: str, version: str) -> str:
    """The one-line `sonda/lab <name> <version>` for --version, slash painted for stdout."""
    return slt.lockup(name, version, color_mode())


def no_key_reader() -> bool:
    """TERM=dumb, CI set or no VT: no raw-mode key reader, so menus take the numbered form."""
    return (not _vt or any(os.environ.get(n) == "dumb" for n in sl.ENV["term"])
            or slt.switch_on("ci", os.environ))


def motion_on() -> bool:
    """Whether the spinner and progress bar may draw on stderr."""
    return slt.motion_enabled(_env(), isatty=sys.stderr.isatty())


def glyph(name: str, err: bool = False) -> str:
    """A GLYPHS entry in its Unicode or ASCII form, painted with its role."""
    uni, asc, role = sl.GLYPHS[name]
    return paint(asc if ascii_on() else uni, role, err=err)


def mark(name: str, word: str, err: bool = False) -> str:
    """A state glyph; in accessible mode followed by its word, so state is never glyph-only."""
    return f"{glyph(name, err)} {word}" if accessible() else glyph(name, err)


def box(name: str) -> str:
    """A BOX entry (box drawing or the selection seam), Unicode or ASCII, unpainted."""
    uni, asc = sl.BOX[name]
    return asc if ascii_on() else uni


def sep(err: bool = False) -> str:
    """The separator glyph with a space each side: ` · `, or ` | ` in ASCII."""
    return f" {glyph('separator', err)} "


def ellipsis() -> str:
    """The running-label ellipsis, `…` or `...`."""
    return "..." if ascii_on() else "…"


def step(name: str, label: str, detail: str = "", *, err: bool = False, dim: bool = False) -> str:
    """One step-list line from the palette's step_line(); with `dim`, the detail in dim.

    The detail is painted after layout, so escape bytes never count toward the label width.
    """
    line = slt.step_line(name, label, detail, ascii=ascii_on(), mode=color_mode(err))
    if dim and detail:
        line = line[: len(line) - len(detail)] + paint(detail, "dim", err=err)
    return line


def note(name: str, text: str, *, err: bool = False) -> str:
    """A glyph and a sentence on the step grid; prose, so it is never cut at labelWidth."""
    return " " * sl.LAYOUT["indent"] + glyph(name, err) + " " + text


def cont(text: str) -> str:
    """A continuation line: starts in the label column, indent + 2 from the left."""
    return " " * (sl.LAYOUT["indent"] + 2) + text


def kv(key: str, value: str = "", *, err: bool = False, dim: bool = False) -> str:
    """A report key-value line (palette kv_line): a fact, no glyph; with `dim`, the value dim."""
    line = slt.kv_line(key, value)
    if dim and value:
        line = line[: len(line) - len(value)] + paint(value, "dim", err=err)
    return line


def heading(text: str, err: bool = False) -> str:
    """A section heading (and a table header): the micro label, dim uppercase, column 0."""
    return slt.heading(text, mode=color_mode(err))


def summary(ok: int, fixed: int, warn: int, failed: int) -> str:
    """The Summary line for stdout: four counts, each in its role, zero in dim."""
    return slt.summary(ok, fixed, warn, failed, mode=color_mode(), ascii=ascii_on())


def error_block(what: str, why: str = "", fix: str = "") -> str:
    """The three-line error for stderr: `failed` + what, why in dim (wrapped), `Try:` + fix."""
    return slt.error_block(what, why, fix, env=_env(), ascii=ascii_on(), mode=color_mode(True))


def term_error(exc: slt.TermError) -> str:
    """A palette TermError (MissingFlag, NoValidAnswer) as the error block for stderr."""
    return exc.block(env=_env(), ascii=ascii_on(), mode=color_mode(True))


def next_line(command: str, *, err: bool = False) -> str:
    """`Next:` in dim, then the follow-up command, on a continuation line."""
    return slt.next_line(command, mode=color_mode(err))


def warn(msg: str) -> None:
    """A warning on stderr: the `warn` glyph and one sentence; the caller carries on."""
    print(note("warn", msg, err=True), file=sys.stderr)


def question(text: str) -> str:
    """A prompt line, `? <question>`, for stderr; the `?` is the question glyph in `prompt`."""
    return f"{glyph('question', err=True)} {text}"


def hints(pairs) -> str:
    """The key-hint footer for stderr (palette key_hints): keys accent, verbs dim."""
    return slt.key_hints(pairs, ascii=ascii_on(), mode=color_mode(True))


def arrows() -> str:
    """The movement keys for a hint: `↑↓`, or `up/dn` in ASCII."""
    return slt.arrows(ascii=ascii_on())


def width() -> int:
    """The prose width: the terminal's, capped at LAYOUT maxWidth (COLUMNS honoured)."""
    return slt.width(_env())


def prose(text: str, indent: int = 4, *, dim: bool = False, err: bool = False) -> str:
    """`text` wrapped at width() from column `indent`; with `dim`, each line painted after wrap."""
    lines = slt.wrap(text, _env(), indent=indent).split("\n")
    if dim:
        lines = [ln[:indent] + paint(ln[indent:], "dim", err=err) for ln in lines]
    return "\n".join(lines)


def sync(frame: str) -> str:
    """`frame` wrapped in synchronized output (DEC 2026); bare when VT is unavailable."""
    return SYNC_ON + frame + SYNC_OFF if _vt else frame


class Waiting:
    """A step that may wait: spinner (and an optional progress bar) on stderr after delayMs.

    With motion on, the glyph column cycles the burst spinner every intervalMs and, when
    `total` is given, a bar is drawn on the continuation line. With motion off, a step that
    outlives delayMs prints its running line once with the `active` glyph. `emit()` writes a
    result line above the live lines. `shown` says whether anything was drawn, so the caller
    knows to print the final step line after the `with` block; nothing animated stays on screen.
    """

    def __init__(self, label: str, total: int | None = None):
        self.label, self.total, self.done = label, total, 0
        self.shown = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._drawn = 0                               # live lines currently on stderr
        self._tick = 0
        self._motion = motion_on()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        with self._lock:
            self._clear()
        return False

    def advance(self, done: int) -> None:
        """Record progress; the next frame redraws the bar."""
        with self._lock:
            self.done = done

    def emit(self, line: str, *, err: bool = False) -> None:
        """Print a result line (stdout, or stderr with `err`), keeping the live lines below it."""
        with self._lock:
            drawn = self._drawn
            self._clear()
            print(line, file=sys.stderr if err else sys.stdout, flush=True)
            if drawn:
                self._draw()

    def _run(self) -> None:
        if self._stop.wait(sl.SPINNER["delayMs"] / 1000):
            return
        if not self._motion:
            with self._lock:
                print(step("active", self.label, err=True), file=sys.stderr, flush=True)
                self.shown = True
            return
        while not self._stop.is_set():
            with self._lock:
                self._draw()                          # rewinds over the last frame itself
                self._tick += 1
            self._stop.wait(sl.SPINNER["intervalMs"] / 1000)

    def _draw(self) -> None:
        frame = slt.spinner_frame(self._tick, ascii=ascii_on())
        lines = [step(frame, self.label, err=True)]
        if self.total:
            lines.append(_bar_line(self.done, self.total))
        rewind = f"\033[{self._drawn}F" if self._drawn else ""
        sys.stderr.write(sync(rewind + "".join(f"\r\033[K{ln}\n" for ln in lines)))
        sys.stderr.flush()
        self._drawn = len(lines)
        self.shown = True

    def _clear(self) -> None:
        if self._drawn:
            sys.stderr.write(f"\033[{self._drawn}F\033[J")
            sys.stderr.flush()
            self._drawn = 0


def _bar_line(done: int, total: int) -> str:
    """progress_bar() painted: the cells in PROGRESS role, the count plain, the percent dim."""
    plain = slt.progress_bar(done, total, ascii=ascii_on())
    end = sl.LAYOUT["indent"] + 2 + sl.PROGRESS["cells"]
    width = sl.PROGRESS["percentWidth"]
    bar, count, pct = plain[:end], plain[end:-width], plain[-width:]
    return paint(bar, sl.PROGRESS["role"], err=True) + count + paint(pct, "dim", err=True)


def elapsed(start: float) -> str:
    """A measured duration for a step's detail column: `0.4s`, `2.1s`."""
    return f"{time.monotonic() - start:.1f}s"


def sep_plain() -> str:
    """The separator unpainted, for text that is painted as a whole: ` · ` or ` | `."""
    uni, asc, _role = sl.GLYPHS["separator"]
    return f" {asc if ascii_on() else uni} "


def blank_before_summary() -> int:
    """Blank lines before a Summary line (LAYOUT blankBeforeSummary)."""
    return sl.LAYOUT["blankBeforeSummary"]
