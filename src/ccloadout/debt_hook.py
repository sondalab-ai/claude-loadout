"""PostToolUse hook: notice a debt marker being written into a file.

Deterministic by design — a pattern the user configured matched, or it did not. No inference and
no model call, and what it records is a *candidate*, never a debt entry: only `memory consolidate`
turns a signal into something the store carries. Exits 0 on every path; a hook that fails here
should cost nothing.
"""
from __future__ import annotations
import json, os, re, sys
from pathlib import Path

# Only tools that write: a marker seen in a file we merely read is not debt this session created.
# Bash counts, because a shell heredoc is how an agent most often writes a file — but only when the
# command actually writes. `grep "TODO(loadout)"` matches nothing here, `cat > f <<EOF` does.
_WRITERS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "str_replace_editor"}
_TEXT_FIELDS = ("content", "new_string", "new_str", "replace_all_string", "text")
# A redirect that writes a file, not `2>&1` or `2>/dev/null` — those are error plumbing, and
# `grep pattern > out.txt` used to be recorded as debt the session had created.
_WRITING_SHELL = re.compile(r"(?<![0-9&])>{1,2}\s*[^&\s]|<<|\btee\b|\bsed\s+-i|\bpatch\b")
LIST_SEP = "\x1f"                                    # a unit separator cannot occur in a pattern

def _written_text(tool_input: dict) -> str:
    parts = [str(tool_input.get(field)) for field in _TEXT_FIELDS if tool_input.get(field)]
    for edit in tool_input.get("edits") or []:      # MultiEdit carries a list of replacements
        if isinstance(edit, dict):
            parts += [str(edit.get(field)) for field in _TEXT_FIELDS if edit.get(field)]
    return "\n".join(parts)

def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        tool = payload.get("tool_name")
        if tool not in _WRITERS and tool != "Bash":
            return 0
        raw = os.environ.get("LOADOUT_DEBT_PATTERNS") or ""
        patterns = [p for p in raw.split(LIST_SEP) if p]
        if not patterns:
            return 0
        tool_input = payload.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            return 0
        if tool == "Bash":
            command = str(tool_input.get("command") or "")
            text = command if _WRITING_SHELL.search(command) else ""
        else:
            text = _written_text(tool_input)
        hit = next((p for p in patterns if p in text), None)
        if hit is None:
            return 0
        line = next((ln.strip() for ln in text.splitlines() if hit in ln), hit)
        from ccloadout.candidates import record_signal
        from ccloadout.repo import repo_root
        record_signal(Path(os.environ["LOADOUT_CONFIG_ROOT"]),
                      repo_root(Path(payload.get("cwd") or os.environ.get("LOADOUT_REPO") or ".")),
                      pattern=hit,
                      file=str(tool_input.get("file_path") or ("(shell)" if tool == "Bash" else "")),
                      excerpt=line)
    except BaseException:                           # bookkeeping must never break a tool call
        return 0
    return 0

if __name__ == "__main__":
    sys.exit(main())
