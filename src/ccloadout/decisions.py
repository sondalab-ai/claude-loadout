from __future__ import annotations
import re
from datetime import datetime
from pathlib import Path
from ccloadout.memory import slug_for, harness_slug, set_status

# Writing side of the `debug-decisions` corpus the store already reads. The file shape is the
# skill's, so both tools see the same directory and neither has to migrate the other's files.
_INDEX = "INDEX.md"
_HEADER = "| ID | Date | Status | Tags | Title |\n|----|------|--------|------|-------|\n"

def corpus_dir(config_root: Path, repo: Path, home: Path | None = None) -> Path:
    # Write where a corpus already lives — the skill hardcodes ~/.claude, and that is where an
    # existing corpus is. Only a fresh one goes under the active profile.
    home = Path.home() if home is None else home
    for base in (home / ".claude", config_root):
        for slug in (slug_for(repo), harness_slug(repo)):
            candidate = base / "debug-decisions" / slug
            if candidate.is_dir():
                return candidate
    return config_root / "debug-decisions" / slug_for(repo)

def _slug(text: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return words[:48].rstrip("-") or "decision"

def new_decision(directory: Path, project: str, title: str, tags: list[str],
                 now: datetime | None = None) -> Path:
    now = now or datetime.now()
    did = f"{now:%Y-%m-%d-%H%M}-{_slug(title)}"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{did}.md"
    suffix = 2
    while path.exists():                            # two decisions in the same minute
        path = directory / f"{did}-{suffix}.md"; did = path.stem; suffix += 1
    path.write_text(
        f"---\nid: {did}\ndate: {now.astimezone():%Y-%m-%dT%H:%M%z}\nproject: {project}\n"
        f"status: active\ntags: [{', '.join(tags)}]\n---\n\n"
        f"# {title}\n\n## Context\n\n## Decision\n\n## Alternatives considered\n\n## Rationale\n")
    _index_row(directory, did, f"{now:%Y-%m-%d}", "active", tags, title)
    return path

def _index_row(directory: Path, did: str, day: str, status: str,
               tags: list[str], title: str) -> None:
    index = directory / _INDEX
    row = f"| {did} | {day} | {status} | {','.join(tags)} | {title} |\n"
    if not index.exists():
        index.write_text(f"# Decisions — {directory.name}\n\n{_HEADER}{row}")
        return
    lines = index.read_text().splitlines(keepends=True)
    at = next((i for i, ln in enumerate(lines) if ln.startswith("|---")), None)
    if at is None:                                  # an index we do not recognise: append, never rewrite
        index.write_text("".join(lines) + row)
        return
    lines.insert(at + 1, row)                       # newest first, matching the skill's ordering
    index.write_text("".join(lines))

def supersede(old: Path, new_id: str) -> None:
    set_status(old, "superseded")
    with old.open("a") as fh:
        fh.write(f"\n## Superseded by\n\n{new_id}\n")
    index = old.parent / _INDEX
    if index.exists():
        text = index.read_text()
        index.write_text(re.sub(rf"^\| {re.escape(old.stem)} \| ([^|]*) \| active \|",
                                rf"| {old.stem} | \1 | superseded |", text, flags=re.MULTILINE))
