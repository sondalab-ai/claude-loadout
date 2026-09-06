from __future__ import annotations
import json, os, tempfile
from datetime import date, datetime
from pathlib import Path
from ccloadout.jsonstore import _Lock

# End-of-session envelopes, appended by the launcher itself once the session exits — the parent
# survives `subprocess.run`, so this needs no hook (spec §5.3). Candidates are never memories:
# `cld memory consolidate` promotes them, with confirmation.
_MAX_CHANGED = 20                                   # a long file list is noise, not signal
DEFAULT_MAX_BYTES = 262_144

def candidates_path(config_root: Path) -> Path:
    return config_root / "loadout" / "candidates.jsonl"

def _rotate(path: Path, max_bytes: int) -> None:
    # Rotate rather than truncate: a user who never consolidates must not accumulate forever,
    # and must not lose what they had either.
    try:
        if path.stat().st_size < max_bytes:
            return
    except OSError:
        return
    stamp = date.today().isoformat()
    target = path.with_name(f"candidates-{stamp}.jsonl")
    suffix = 2
    while target.exists():
        target = path.with_name(f"candidates-{stamp}-{suffix}.jsonl")
        suffix += 1
    try:
        path.rename(target)
    except OSError:                                 # a peer rotated first; nothing left to do
        return

def record_session(config_root: Path, repo: Path, goal: str, exit_code: int,
                   changed: list[str], max_bytes: int = DEFAULT_MAX_BYTES) -> None:
    path = candidates_path(config_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate(path, max_bytes)
    _append(path, {"kind": "session", "repo": str(repo), "goal": goal, "exit_code": exit_code,
                   "changed": changed[:_MAX_CHANGED], "changed_total": len(changed)})

def record_signal(config_root: Path, repo: Path, pattern: str, file: str, excerpt: str,
                  max_bytes: int = DEFAULT_MAX_BYTES) -> None:
    # A debt marker the user configured, seen being written into a file. Deterministic: a pattern
    # matched or it did not. It becomes a candidate, never a debt entry — only `consolidate` (and
    # the user in it) turns a signal into something the store carries.
    path = candidates_path(config_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate(path, max_bytes)
    _append(path, {"kind": "debt-signal", "repo": str(repo), "pattern": pattern,
                   "file": file, "excerpt": excerpt[:200]})

def _append(path: Path, row: dict) -> None:
    row = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), **row}
    with _Lock(path), path.open("a") as fh:        # same lock `drop_rows` takes
        fh.write(json.dumps(row) + "\n")

def load_candidates(config_root: Path, repo: Path | None = None,
                    kind: str | None = None) -> list[dict]:
    try:
        lines = candidates_path(config_root).read_text(errors="replace").splitlines()
    except (OSError, ValueError):                   # UnicodeDecodeError is a ValueError
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:                # a half-written line is skipped, never fatal
            continue
        if not isinstance(row, dict) or (repo is not None and row.get("repo") != str(repo)):
            continue
        if kind is not None and (row.get("kind") or "session") != kind:
            continue                                # rows written before `kind` existed are sessions
        rows.append(row)
    return rows

def drop_rows(config_root: Path, repo: Path, keep: list[dict]) -> None:
    """Replace this repository's rows with `keep`, leaving every other line exactly as it was.

    Lines that do not parse are *kept*, not dropped: `load_candidates` skipping them on read is a
    tolerance, but rewriting the file from parsed rows would turn that tolerance into deletion —
    including of other repositories' data. Held under the same lock the appends take, or a session
    that ends while a consolidate is open loses its candidate.
    """
    path = candidates_path(config_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _Lock(path):
        try:
            lines = path.read_text(errors="replace").splitlines(keepends=True)
        except (OSError, ValueError):
            lines = []
        surviving = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:                      # unparsable: not ours to judge, keep it
                surviving.append(line)
                continue
            if not isinstance(row, dict) or row.get("repo") != str(repo):
                surviving.append(line)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".candidates-", suffix=".jsonl")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.writelines(surviving)
                for row in keep:
                    fh.write(json.dumps(row) + "\n")
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
