"""`cld memory link`: make the repository's docs/memory the folder Claude Code loads.

Claude Code reads only `<config_root>/projects/<slug>/memory/MEMORY.md` (index lines, keyed by the
main checkout). Notes written to `<repo>/docs/memory` reach a session only when that folder is
symlinked there. Linking merges whatever Claude Code already keeps into docs/memory, keeps the
original folder as a backup, and replaces it with the symlink. Nothing is deleted.
"""
from __future__ import annotations
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from ccloadout.inventory import _frontmatter
from ccloadout.memory import _HEADING, _INDEX_FILE, _INDEX_LINE, _INDEX_NAMES, harness_slug, index_line

@dataclass(frozen=True)
class LinkPlan:
    harness: Path                   # Claude Code's memory folder for this repository
    store: Path                     # <repo>/docs/memory
    state: str                      # linked | missing | merge | elsewhere | unknown-project
    copies: tuple[Path, ...] = ()   # files in `harness` that docs/memory does not have yet
    conflicts: tuple[str, ...] = () # same name on both sides, different content
    added: tuple[str, ...] = ()     # index lines MEMORY.md gains
    index_text: str = ""            # MEMORY.md as it will be written
    backup: Path | None = None      # where the original folder goes (merge only)

    @property
    def blocked(self) -> bool:
        return self.state in ("elsewhere", "unknown-project") or bool(self.conflicts)

    @property
    def changes(self) -> bool:
        return self.state in ("missing", "merge") or bool(self.added)

def harness_dir(config_root: Path, root: Path) -> Path:
    return config_root / "projects" / harness_slug(root) / "memory"

def is_linked(config_root: Path, root: Path) -> bool:
    """True when Claude Code's memory folder for `root` is (a link to) its docs/memory."""
    try:
        return harness_dir(config_root, root).resolve() == (root / "docs" / "memory").resolve()
    except OSError:
        return False

def _targets(index_text: str) -> set[str]:
    return {m.group(1) for ln in index_text.splitlines() if (m := _INDEX_LINE.match(ln.strip()))}

def _index_line(path: Path) -> str:
    # The same shape `memory add` writes: `- [name](file.md) — description`.
    text = path.read_text(errors="ignore")
    fm = _frontmatter(text)
    heading = _HEADING.search(text)
    desc = str(fm.get("description") or (heading.group(1) if heading else ""))
    return index_line(str(fm.get("name") or path.stem), path.name, desc)

def _notes(directory: Path) -> list[Path]:
    try:
        return sorted(p for p in directory.iterdir()
                      if p.is_file() and p.suffix == ".md" and p.name not in _INDEX_NAMES)
    except OSError:
        return []

def plan_link(config_root: Path, root: Path, now: datetime | None = None) -> LinkPlan:
    """Work out what linking would do, touching nothing.

    Refuses (state `unknown-project`) unless Claude Code already keeps transcripts for this
    repository under the computed slug: a wrong slug would otherwise produce a link the harness
    never reads, and report success while fixing nothing.
    """
    harness, store = harness_dir(config_root, root), root / "docs" / "memory"
    project = harness.parent
    if not project.is_dir() or not any(project.glob("*.jsonl")):
        return LinkPlan(harness, store, "unknown-project")
    if harness.is_symlink():
        if harness.resolve() != store.resolve():
            return LinkPlan(harness, store, "elsewhere")
        state = "linked"
    else:
        state = "merge" if harness.is_dir() else "missing"
    copies, conflicts = [], []
    if state == "merge":
        for src in sorted(harness.iterdir()):
            if src.name == _INDEX_FILE or src.name.startswith("."):
                continue                            # .DS_Store and friends stay in the backup
            dest = store / src.name
            if src.is_symlink():                    # never follow one into a tracked repository
                conflicts.append(f"{src.name} (a symlink)")
            elif src.is_dir() or dest.is_dir():
                conflicts.append(src.name)
            elif dest.exists() and dest.read_bytes() != src.read_bytes():
                conflicts.append(src.name)          # a different file by the same name
            elif not dest.exists():
                copies.append(src)
    base = _read(store / _INDEX_FILE)
    other = _read(harness / _INDEX_FILE) if state == "merge" else ""
    if not base:
        base, other = other, ""
    lines = base.rstrip("\n").splitlines() if base else ["# Memory index"]
    have = _targets("\n".join(lines))
    added = []
    for ln in other.splitlines():                   # what Claude Code's own index already lists
        m = _INDEX_LINE.match(ln.strip())
        if m and m.group(1) not in have:
            added.append(ln.strip()); have.add(m.group(1))
    for note in [*_notes(store), *copies]:          # notes nothing indexes reach no session
        if note.suffix == ".md" and note.name not in have:
            added.append(_index_line(note)); have.add(note.name)
    backup = None
    if state == "merge":
        stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        backup, suffix = harness.with_name(f"memory.bak-{stamp}"), 2
        while backup.exists():                      # a second run within the same second
            backup = harness.with_name(f"memory.bak-{stamp}-{suffix}")
            suffix += 1
    return LinkPlan(harness, store, state, tuple(copies), tuple(conflicts), tuple(added),
                    "\n".join([*lines, *added]) + "\n", backup)

def apply_link(plan: LinkPlan) -> None:
    """Carry out a plan from `plan_link`. Copies, never moves: the backup keeps every original."""
    if plan.blocked:
        raise ValueError(f"cannot link: {plan.state}")
    plan.store.mkdir(parents=True, exist_ok=True)
    for src in plan.copies:
        shutil.copy2(src, plan.store / src.name)
    index = plan.store / _INDEX_FILE
    if _read(index).strip() != plan.index_text.strip():
        index.write_text(plan.index_text)
    if plan.state == "merge":
        plan.harness.rename(plan.backup)
    if plan.state in ("merge", "missing"):
        try:
            plan.harness.symlink_to(plan.store.resolve(), target_is_directory=True)
        except OSError:                             # put Claude Code's folder back as it was
            if plan.state == "merge":
                plan.backup.rename(plan.harness)
            raise

def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""
