from __future__ import annotations
import subprocess
from pathlib import Path

def repo_root(path: Path) -> Path:
    """The main checkout's root for a path inside a git repository or any of its worktrees.

    Claude Code keys its own memory folder by the main checkout, so every per-repository store
    cld keeps (notes, decisions, usage, flags, candidates) does the same: a note written from a
    worktree must not land somewhere only that worktree sees. A linked worktree steps out of the
    shared `.git`; a submodule or a separate git dir has no such parent and uses its own top
    level. Outside git, or when git cannot answer, the path itself (resolved).
    """
    here = Path(path).resolve()
    try:                                            # no --path-format: it needs git 2.31+
        proc = subprocess.run(["git", "rev-parse", "--git-common-dir", "--show-toplevel"],
                              cwd=here, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return here
    out = getattr(proc, "stdout", None)
    if getattr(proc, "returncode", 1) != 0 or not isinstance(out, str):
        return here
    fields = out.strip().splitlines()
    if len(fields) != 2 or not all(fields):         # a bare repository has no top level
        return here
    common, top = (here / fields[0]).resolve(), Path(fields[1]).resolve()
    return common.parent if common.name == ".git" else top
