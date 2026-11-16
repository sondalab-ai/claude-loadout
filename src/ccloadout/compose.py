from __future__ import annotations
import json, os, tempfile
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class LaunchPlan:
    argv: list[str]
    env: dict[str, str]
    tmp_paths: list[Path]
    servers: tuple[str, ...] = ()        # MCP servers the curated config carries

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
