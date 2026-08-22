# GENERATED from src/tokens/sondalab.tokens.json — do not edit.
"""Sondalab terminal palette: semantic roles, named-ANSI floor + truecolor opt-in."""
import os

# role -> (named_ansi_sgr_code, (r, g, b))
ROLES = {
    "prompt": (93, (245, 168, 60)),
    "ok": (32, (98, 208, 148)),
    "warn": (33, (245, 168, 60)),
    "err": (31, (248, 113, 113)),
    "dim": (90, (110, 128, 136)),
    "accent": (36, (53, 160, 180)),
}


def _no_color() -> bool:
    return bool(os.environ.get("NO_COLOR"))


def supports_truecolor() -> bool:
    return os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")


def paint(text: str, role: str, *, err: bool = False) -> str:
    if _no_color() or role not in ROLES:
        return text
    code, (r, g, b) = ROLES[role]
    sgr = f"38;2;{r};{g};{b}" if supports_truecolor() else str(code)
    return f"\033[{sgr}m{text}\033[0m"
