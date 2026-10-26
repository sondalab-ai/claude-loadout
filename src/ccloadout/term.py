"""Terminal output on the Sondalab Term spec (docs/tui-patterns.md in sondalab-ui).

Colour, glyphs, step lines, error blocks, prompts, menu hints and the waiting spinner. Every
number and glyph comes from `sondalab_palette`; nothing here re-derives a token.
"""
from __future__ import annotations

import os
import sys
import threading
import time

import sondalab_palette as sl
from sondalab_palette import term as slt

# `bold` and `dim` are text styles, not palette roles; `dim` stays SGR 2 (faint), the code
# cld has always written, so a non-truecolor terminal sees the same bytes it did before.
_STYLE_SGR = {"dim": "2", "bold": "1"}
# The colour names cli.py has always used, kept as aliases of the palette roles.
_ALIAS = {"green": "ok", "red": "err", "yellow": "warn", "cyan": "accent"}
SYNC_ON, SYNC_OFF = "\033[?2026h", "\033[?2026l"     # DEC 2026 synchronized output


def _env() -> dict:
    """os.environ with cld's own LOADOUT_NO_COLOR folded into NO_COLOR."""
    env = dict(os.environ)
    if env.get("LOADOUT_NO_COLOR") and not env.get("NO_COLOR"):
        env["NO_COLOR"] = "1"
    return env


def color_mode(err: bool = False) -> str:
    """'none', 'ansi16' or 'truecolor' for stdout (or stderr), by the spec's D7 order.

    NO_COLOR, then FORCE_COLOR, then the stream's TTY state and TERM=dumb. cld opts in to
    truecolor, so COLORTERM=truecolor|24bit upgrades the roles to the brand hues. Read on every
    call, never cached, so pytest's capsys stream swap is honoured.
    """
    stream = sys.stderr if err else sys.stdout
    return slt.color_mode(_env(), isatty=stream.isatty(), truecolor=True)


def truecolor_ok() -> bool:
    """Whether COLORTERM allows truecolor, colour assumed on (cld opts in to truecolor)."""
    return slt.color_mode(_env(), flag=True, truecolor=True) == "truecolor"


def sgr(code: str, truecolor: bool = False) -> str:
    """The SGR parameter for a style (`bold`, `dim`) or a role (or its legacy colour alias)."""
    if code in _STYLE_SGR:
        return _STYLE_SGR[code]
    ansi, (r, g, b) = sl.ROLES[_ALIAS.get(code, code)]
    return f"38;2;{r};{g};{b}" if truecolor else str(ansi)


def paint(text: str, *codes: str, err: bool = False) -> str:
    """Wrap `text` in the SGR for `codes` when colour is on for that stream; else return it."""
    mode = color_mode(err)
    if mode == "none" or not text:
        return text
    return f"\033[{';'.join(sgr(c, mode == 'truecolor') for c in codes)}m{text}\033[0m"


def ascii_on() -> bool:
    """True when every glyph takes its ASCII form (non-UTF-8 locale, TERM=dumb, SONDALAB_ASCII=1)."""
    return slt.ascii_mode(os.environ)


def accessible() -> bool:
    """Accessible mode: SONDALAB_ACCESSIBLE=1 or ACCESSIBLE set. Plain prompts, no redraw.

    The "=1" versus "set" rule is the palette's (term.accessible, palette 0.7).
    """
    return slt.accessible(os.environ)


def no_key_reader() -> bool:
    """TERM=dumb or CI set: no raw-mode key reader, so menus take the numbered form."""
    return (any(os.environ.get(n) == "dumb" for n in sl.ENV["term"])
            or slt.switch_on("ci", os.environ))


def motion_on() -> bool:
    """Whether the spinner and progress bar may draw on stderr."""
    return slt.motion_enabled(os.environ, isatty=sys.stderr.isatty())


def glyph(name: str, err: bool = False) -> str:
    """A GLYPHS entry in its Unicode or ASCII form, painted with its role."""
    uni, asc, role = sl.GLYPHS[name]
    return paint(asc if ascii_on() else uni, role, err=err)


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
    """One step-list line: indent, painted glyph, label padded to labelWidth, then the detail.

    Layout comes from step_line() on plain text, then only the glyph (and, with `dim`, the
    detail) is painted, so escape bytes never count toward the label width.
    """
    plain = slt.step_line(name, label, detail, ascii=ascii_on())
    at = sl.LAYOUT["indent"]
    if name in sl.GLYPHS:
        mark = glyph(name, err)
    else:                                            # a spinner frame
        mark = paint(plain[at], sl.SPINNER["role"], err=err)
    line = plain[:at] + mark + plain[at + 1:]
    if dim and detail:
        line = line[: len(line) - len(detail)] + paint(detail, "dim", err=err)
    return line


def note(name: str, text: str, *, err: bool = False) -> str:
    """A glyph and a sentence on the step grid; prose, so it is never cut at labelWidth."""
    return " " * sl.LAYOUT["indent"] + glyph(name, err) + " " + text


def cont(text: str) -> str:
    """A continuation line: starts in the label column, indent + 2 from the left."""
    return " " * (sl.LAYOUT["indent"] + 2) + text


def info(label: str, detail: str = "", *, err: bool = False, dim: bool = False) -> str:
    """A step-grid row with no glyph: a fact, not a check, aligned with the step labels."""
    plain = slt.step_line("pending", label, detail, ascii=ascii_on())   # the palette's label cut
    at = sl.LAYOUT["indent"]
    line = plain[:at] + " " + plain[at + 1:]          # blank the glyph cell: a fact, not a check
    if dim and detail:
        line = line[: len(line) - len(detail)] + paint(detail, "dim", err=err)
    return line


def error_block(what: str, why: str = "", fix: str = "") -> list[str]:
    """The three-line error, for stderr: `failed` + what, why in dim, `Try:` + the fix."""
    lines = [note("failed", what, err=True)]
    if why:
        lines.append(cont(paint(why, "dim", err=True)))
    if fix:
        lines.append(cont(f"{paint('Try:', 'prompt', err=True)} {fix}"))
    return lines


def question(text: str) -> str:
    """A prompt line, `? <question>`, for stderr; the `?` is the question glyph in `prompt`."""
    return f"{glyph('question', err=True)} {text}"


def hints(pairs) -> str:
    """The key-hint footer: keys in accent, verbs in dim, pairs joined by the separator."""
    return sep(err=True).join(f"{paint(k, 'accent', err=True)} {paint(v, 'dim', err=True)}"
                              for k, v in pairs)


def arrows() -> str:
    """The movement keys for a hint: `↑↓`, or `up/dn` in ASCII."""
    up, down = sl.KEYS["up"], sl.KEYS["down"]
    return f"{up[1]}/{down[1]}" if ascii_on() else f"{up[0]}{down[0]}"


def micro(text: str, err: bool = False) -> str:
    """A table or group header: dim uppercase (the micro label)."""
    return paint(text.upper(), "dim", err=err)


class Waiting:
    """A step that may wait: spinner (and an optional progress bar) on stderr after delayMs.

    With motion on, the glyph column cycles the burst spinner every intervalMs and, when
    `total` is given, a bar is drawn on the continuation line. With motion off, a step that
    outlives delayMs prints its running line once with the `active` glyph. `emit()` writes a
    result line to stdout above the live lines. The caller prints the final step line after
    the `with` block; nothing animated is left on screen.
    """

    def __init__(self, label: str, total: int | None = None):
        self.label, self.total, self.done = label, total, 0
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

    def emit(self, line: str) -> None:
        """Print a result line on stdout, keeping the live lines below it."""
        with self._lock:
            drawn = self._drawn
            self._clear()
            print(line, flush=True)
            if drawn:
                self._draw()

    def _run(self) -> None:
        if self._stop.wait(sl.SPINNER["delayMs"] / 1000):
            return
        if not self._motion:
            with self._lock:
                print(step("active", self.label, err=True), file=sys.stderr, flush=True)
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
        sys.stderr.write(SYNC_ON + rewind + "".join(f"\r\033[K{ln}\n" for ln in lines)
                         + SYNC_OFF)
        sys.stderr.flush()
        self._drawn = len(lines)

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
