from __future__ import annotations
import subprocess
from pathlib import Path

def repo_root(path: Path) -> Path:
    """The main checkout's root for a path inside a git repository or any of its worktrees.

    Claude Code keys its own memory folder by the main checkout, so every per-repository store
    cld keeps (notes, decisions, usage, flags, candidates) does the same: a note written from a
    worktree must not land somewhere only that worktree sees. Outside git, or when git cannot
    answer, the path itself (resolved).
    """
    here = Path(path).resolve()
    try:
        proc = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                              cwd=here, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return here
    out = getattr(proc, "stdout", None)
    if getattr(proc, "returncode", 1) != 0 or not isinstance(out, str) or not out.strip():
        return here
    common = Path(out.strip())
    # A bare repository or an unusual layout has no `.git` directory to step out of.
    return common.parent.resolve() if common.name == ".git" else here
