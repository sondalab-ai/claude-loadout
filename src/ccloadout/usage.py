from __future__ import annotations
import json, os, tempfile, time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping

# Delivery counters live here and never in the entry files: writing them back into
# <repo>/docs/memory/*.md would dirty git-tracked files on every launch (spec §5.5).
_LOCK_TIMEOUT_S = 5.0
_LOCK_POLL_S = 0.01

@dataclass(frozen=True)
class Usage:
    uses: int = 0
    last_used: str = ""

    def days_since(self, today: date) -> int | None:
        try:
            return (today - date.fromisoformat(self.last_used)).days
        except ValueError:                          # absent or unparsable: no age to report
            return None

def usage_path(config_root: Path) -> Path:
    return config_root / "loadout" / "usage.json"

def _repo_key(repo: Path) -> str:
    return str(repo)

def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):         # absent or corrupt: start clean, never crash
        return {}
    return data if isinstance(data, dict) else {}

def load_usage(config_root: Path, repo: Path) -> dict[str, Usage]:
    per_repo = _read(usage_path(config_root)).get(_repo_key(repo))
    if not isinstance(per_repo, dict):
        return {}
    out: dict[str, Usage] = {}
    for entry_id, rec in per_repo.items():
        if isinstance(rec, dict):
            out[entry_id] = Usage(uses=int(rec.get("uses") or 0),
                                  last_used=str(rec.get("last_used") or ""))
    return out

class _Lock:
    # Two launches in the same repository are ordinary, so the read-modify-write is serialised
    # with an exclusive lock file. A lock older than the timeout is broken rather than obeyed:
    # a crashed launcher must not block every later one.
    def __init__(self, path: Path):
        self._path = path.with_suffix(".lock")

    def __enter__(self):
        deadline = time.monotonic() + _LOCK_TIMEOUT_S
        while True:
            try:
                fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return self
            except FileExistsError:
                if time.monotonic() > deadline:
                    self._path.unlink(missing_ok=True)
                    continue
                time.sleep(_LOCK_POLL_S)

    def __exit__(self, *exc):
        self._path.unlink(missing_ok=True)

def record_delivery(config_root: Path, repo: Path, entry_ids: Iterable[str],
                    today: date | None = None) -> None:
    ids = [i for i in entry_ids]
    if not ids:                                     # nothing was delivered: nothing to record
        return
    stamp = (today or date.today()).isoformat()
    path = usage_path(config_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _Lock(path):
        data = _read(path)
        per_repo = data.setdefault(_repo_key(repo), {})
        if not isinstance(per_repo, dict):
            per_repo = data[_repo_key(repo)] = {}
        for entry_id in ids:
            rec = per_repo.get(entry_id)
            uses = int(rec.get("uses") or 0) if isinstance(rec, dict) else 0
            per_repo[entry_id] = {"uses": uses + 1, "last_used": stamp}
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".usage-", suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, path)                       # atomic: a reader sees old or new, never half

def promoted_ids(usage: Mapping[str, Usage], promote_after: int) -> set[str]:
    return {eid for eid, rec in usage.items() if rec.uses >= promote_after}
