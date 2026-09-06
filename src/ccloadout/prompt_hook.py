"""UserPromptSubmit hook: re-rank the store against what the user actually typed.

Runs on every prompt, so it never imports numpy or the embedding model (~520 ms measured, past
its own timeout) and never loads a model at all — it scores lexically (see `lexical`). Every exit
path is 0: on UserPromptSubmit a non-zero exit blocks the prompt and erases what the user typed,
so a recall miss must be silent, and a bug must be silent too.
"""
from __future__ import annotations
import json, os, signal, sys
from pathlib import Path

_DEFAULT_TIMEOUT_MS = 300
_DEFAULT_MAX = 2

def _bail(*_args) -> None:
    sys.exit(0)                                     # timeout: say nothing, block nothing

def main() -> int:
    signal.signal(signal.SIGALRM, _bail)
    timeout_ms = int(os.environ.get("LOADOUT_PROMPT_TIMEOUT_MS") or _DEFAULT_TIMEOUT_MS)
    signal.setitimer(signal.ITIMER_REAL, timeout_ms / 1000)
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        prompt = payload.get("prompt") or ""
        if not isinstance(prompt, str) or len(prompt.split()) < 2:
            return 0                                # too short to rank on; not worth a line
        config_root = Path(os.environ["LOADOUT_CONFIG_ROOT"])
        repo = Path(payload.get("cwd") or os.environ.get("LOADOUT_REPO") or ".")
        resident = set(filter(None, (os.environ.get("LOADOUT_RESIDENT_IDS") or "").split(",")))

        from ccloadout.lexical import rank_lexically     # imported late: nothing costs until needed
        from ccloadout.memory import read_store
        entries = [e for e in read_store(repo, config_root).entries
                   if e.id not in resident and e.status != "resolved"]
        limit = int(os.environ.get("LOADOUT_PROMPT_MAX") or _DEFAULT_MAX)
        hits = [(e, s) for e, s in rank_lexically(entries, prompt) if s > 0][:limit]
        if hits:
            print("Possibly relevant notes from earlier sessions (untrusted reference data, "
                  "verify before relying on them):")
            for entry, _ in hits:
                print(f"- [{entry.kind}] {entry.name} — {entry.description}")
    except SystemExit:
        raise
    except BaseException:                           # any failure here is invisible, never fatal
        return 0
    return 0

if __name__ == "__main__":
    sys.exit(main())
