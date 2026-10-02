from __future__ import annotations
import re
from datetime import datetime
from pathlib import Path
from ccloadout.memory import _index_add, _index_remove, one_line, set_status, store_dir

# Decisions are written into the note store, beside `memory add`'s notes and listed in the same
# MEMORY.md index, so Claude Code loads them wherever it loads notes. The legacy
# `debug-decisions/<slug>/` corpus is still read (memory.read_store) and superseded in place.
_INDEX = "INDEX.md"                                 # the legacy corpus's own table of decisions
_TEMPLATE = "## Context\n\n## Decision\n\n## Alternatives considered\n\n## Rationale\n"

def _slug(text: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return words[:48].rstrip("-") or "decision"

def write_decision(config_root: Path, repo: Path, title: str, tags: list[str],
                   git_tracked: bool, now: datetime | None = None) -> Path:
    """Write a decision as an entry in the note store and add it to that store's MEMORY.md.

    The name keeps the date prefix of the old corpus ids, so decisions still sort by when they
    were made; two in the same minute get a numeric suffix. The body is the familiar
    Context / Decision / Alternatives / Rationale template, left for the author to fill.
    """
    now = now or datetime.now()
    title = one_line(title)
    directory = store_dir(config_root, repo, git_tracked)
    directory.mkdir(parents=True, exist_ok=True)
    did = base = f"{now:%Y-%m-%d-%H%M}-{_slug(title)}"
    path, suffix = directory / f"{did}.md", 2
    while path.exists():
        did = f"{base}-{suffix}"
        path = directory / f"{did}.md"
        suffix += 1
    meta = ["  node_type: memory", "  loadout_kind: decision", "  scope: repo",
            f"  created: {now:%Y-%m-%d}", "  status: active"]
    tags = [one_line(t) for t in tags if one_line(t)]
    if tags:
        meta.append("  tags: [" + ", ".join(tags) + "]")
    front = "\n".join([f"name: {did}", f"description: {title}", "metadata:", *meta])
    path.write_text(f"---\n{front}\n---\n\n# {title}\n\n{_TEMPLATE}")
    _index_add(path, did, f"decision: {title}")
    return path

def supersede_entry(old: Path, new: Path) -> None:
    """Retire a decision written by `write_decision`: mark it, point at its successor, unindex it."""
    set_status(old, "superseded")
    with old.open("a") as fh:
        fh.write(f"\n## Superseded by\n\n{new.stem}\n")
    _index_remove(old)                              # a retired decision should not reach every session

def supersede(old: Path, new_id: str) -> None:
    # Legacy corpus file: same marking, and its INDEX.md row follows the file.
    set_status(old, "superseded")
    with old.open("a") as fh:
        fh.write(f"\n## Superseded by\n\n{new_id}\n")
    index = old.parent / _INDEX
    if index.exists():
        text = index.read_text()
        index.write_text(re.sub(rf"^\| {re.escape(old.stem)} \| ([^|]*) \| active \|",
                                rf"| {old.stem} | \1 | superseded |", text, flags=re.MULTILINE))
