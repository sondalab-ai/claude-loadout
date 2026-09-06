from __future__ import annotations
import json, os, tempfile, time
from pathlib import Path
from typing import Callable

# Small shared sidecars under <config_root>/loadout/. They are written by every launch and by
# explicit commands, so two processes touching one is ordinary: read-modify-write happens under an
# exclusive lock and lands through an atomic replace.
_LOCK_TIMEOUT_S = 5.0
_LOCK_POLL_S = 0.01

def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):         # absent or corrupt: start clean, never crash
        return {}
    return data if isinstance(data, dict) else {}

class _Lock:
    # A lock older than the timeout is broken rather than obeyed: a crashed writer must not
    # block every later one.
    def __init__(self, path: Path):
        self._path = path.with_suffix(".lock")

    def __enter__(self):
        deadline = time.monotonic() + _LOCK_TIMEOUT_S
        while True:
            try:
                os.close(os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return self
            except FileExistsError:
                if time.monotonic() > deadline:
                    self._path.unlink(missing_ok=True)
                    continue
                time.sleep(_LOCK_POLL_S)

    def __exit__(self, *exc):
        self._path.unlink(missing_ok=True)

def update_json(path: Path, mutate: Callable[[dict], None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _Lock(path):
        data = read_json(path)
        mutate(data)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, path)                       # a reader sees old or new, never half
