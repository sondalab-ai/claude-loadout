from __future__ import annotations
import json, os, shutil, tempfile
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class LaunchPlan:
    argv: list[str]
    env: dict[str, str]
    tmp_paths: list[Path]
    servers: tuple[str, ...] = ()        # MCP servers the curated config carries
    tmp_dirs: tuple[Path, ...] = ()      # temp directories the launch flow removes after the run

def remove_tmp(plan: LaunchPlan) -> None:
    """Delete a plan's temp files and temp directories, ignoring what is already gone.

    A symlink inside a temp directory is removed as a link: rmtree never follows one, so the
    files it points at (a harness's real auth or sessions) are left alone.
    """
    for p in plan.tmp_paths:
        try:
            p.unlink()
        except OSError:
            pass
    for d in plan.tmp_dirs:
        if d.is_symlink():
            d.unlink()
        else:
            shutil.rmtree(d, ignore_errors=True)

def write_tmp_text(prefix: str, text: str, suffix: str = ".txt") -> Path:
    """Write text to a fresh temp file named loadout-<prefix>-*<suffix> and return its path.

    The caller owns the file: a LaunchPlan lists it in tmp_paths, and the launch flow
    deletes it after the run.
    """
    fd, name = tempfile.mkstemp(prefix=f"loadout-{prefix}-", suffix=suffix)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    return Path(name)

def write_tmp_json(prefix: str, data: dict) -> Path:
    """Write data as JSON to a fresh temp file named loadout-<prefix>-*.json and return its path.

    Same ownership as write_tmp_text: the plan that carries the path deletes the file.
    """
    fd, name = tempfile.mkstemp(prefix=f"loadout-{prefix}-", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    return Path(name)
