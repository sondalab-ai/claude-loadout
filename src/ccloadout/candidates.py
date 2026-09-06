from __future__ import annotations
import json
from datetime import date, datetime
from pathlib import Path

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
    path.rename(target)

def record_session(config_root: Path, repo: Path, goal: str, exit_code: int,
                   changed: list[str], max_bytes: int = DEFAULT_MAX_BYTES) -> None:
    path = candidates_path(config_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate(path, max_bytes)
    row = {"at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "repo": str(repo), "goal": goal, "exit_code": exit_code,
           "changed": changed[:_MAX_CHANGED], "changed_total": len(changed)}
    with path.open("a") as fh:
        fh.write(json.dumps(row) + "\n")

def load_candidates(config_root: Path, repo: Path | None = None) -> list[dict]:
    try:
        lines = candidates_path(config_root).read_text().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:                # a half-written line is skipped, never fatal
            continue
        if isinstance(row, dict) and (repo is None or row.get("repo") == str(repo)):
            rows.append(row)
    return rows

def drop_rows(config_root: Path, repo: Path, keep: list[dict]) -> None:
    # Rewrites the file with this repository's rows replaced by `keep`; other repositories'
    # rows are preserved verbatim. Atomic replace, so a reader never sees a half-written file.
    import os, tempfile
    path = candidates_path(config_root)
    others = [r for r in load_candidates(config_root) if r.get("repo") != str(repo)]
    rows = others + keep
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".candidates-", suffix=".jsonl")
    with os.fdopen(fd, "w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    os.replace(tmp, path)
