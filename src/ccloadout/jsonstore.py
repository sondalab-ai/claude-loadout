from __future__ import annotations
import fcntl, json, os, tempfile
from pathlib import Path
from typing import Callable

# Small shared sidecars under <config_root>/loadout/. They are written by every launch and by
# explicit commands, so two processes touching one is ordinary: read-modify-write happens while
# holding an advisory lock on a separate lock file, and lands through an atomic replace.
#
# `flock` rather than an O_EXCL sentinel: a sentinel has to be broken after a timeout, and a
# timeout-broken sentinel is not a lock at all — two waiters can break it together, and a slow
# writer's release then deletes the lock a *different* process is holding. flock is released by
# the kernel when the holder exits, so a crashed launcher unblocks the next one for free.

def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):                   # absent, corrupt or not even UTF-8:
                                                    # start clean, never crash (UnicodeDecodeError
                                                    # is a ValueError, not an OSError)
        return {}
    return data if isinstance(data, dict) else {}

class _Lock:
    def __init__(self, path: Path):
        self._path = path.with_suffix(".lock")
        self._fd: int | None = None

    def __enter__(self):
        self._fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

def update_json(path: Path, mutate: Callable[[dict], None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _Lock(path):
        data = read_json(path)
        mutate(data)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, indent=1)
            os.replace(tmp, path)                   # a reader sees old or new, never half
        except BaseException:
            Path(tmp).unlink(missing_ok=True)       # a failed write leaves no litter behind
            raise
