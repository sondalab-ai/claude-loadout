from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from ccloadout.inventory import _frontmatter

# Index files live beside entries in every store the reader walks; they list entries, they aren't one.
_INDEX_NAMES = {"MEMORY.md", "INDEX.md", "README.md"}
_HEADING = re.compile(r"^#\s+(.+)$", re.MULTILINE)

@dataclass(frozen=True)
class Entry:
    # Field names match inventory.Item on purpose: the ranker embeds `name. description` and
    # matches `id` against always_keep patterns, so an Entry ranks without a conversion step.
    id: str
    kind: str                       # memory | decision | debt
    name: str
    description: str
    path: Path
    scope: str                      # repo | global
    status: str | None = None
    anchors: tuple[str, ...] = ()
    content_sha: str | None = None

@dataclass(frozen=True)
class Store:
    entries: tuple[Entry, ...]
    shadowed: tuple[tuple[Entry, Entry], ...]      # (kept, dropped) — same slug, distinct files

def slug_for(path: Path) -> str:
    # The `debug-decisions` convention: an absolute path with `/` → `-`, dots preserved.
    return str(path).replace("/", "-")

def harness_slug(path: Path) -> str:
    # Claude Code's own convention for `projects/<slug>`: it flattens dots as well, so
    # /Users/jane.doe/src/x becomes -Users-jane-doe-src-x. Verified against a live config dir.
    return slug_for(path).replace(".", "-")

def _slugs(path: Path) -> list[str]:
    # Try both conventions everywhere: they differ only for paths containing a dot, and reading
    # the wrong one silently finds an empty store rather than failing.
    return list(dict.fromkeys([harness_slug(path), slug_for(path)]))

def _text(path: Path) -> str | None:
    try:
        return path.read_text(errors="ignore")
    except OSError:
        return None

def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None

def _memory_entry(path: Path, scope: str) -> Entry | None:
    raw = _text(path)
    if raw is None:
        return None
    fm = _frontmatter(raw)
    name = _str(fm.get("name"))
    if name is None:                                # a store file without a name is not an entry
        return None
    meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    kind = _str(meta.get("loadout_kind")) or "memory"
    anchors = meta.get("anchors")
    return Entry(id=f"{kind}:{name}", kind=kind, name=name,
                 description=_str(fm.get("description")) or "", path=path, scope=scope,
                 status=_str(meta.get("status")),
                 anchors=tuple(anchors) if isinstance(anchors, list) else (),
                 content_sha=_str(meta.get("content_sha")))

def _decision_entry(path: Path, project: str) -> Entry | None:
    raw = _text(path)
    if raw is None:
        return None
    fm = _frontmatter(raw)
    did = _str(fm.get("id"))
    if did is None:
        return None
    # Decision files carry no `description`; the ranker needs one, and the human title is the
    # first `#` heading of the body (spec §5.1). Frontmatter is left untouched on disk.
    heading = _HEADING.search(raw[raw.find("\n---", 3) + 1:] if raw.startswith("---") else raw)
    return Entry(id=f"decision:{project}/{did}", kind="decision", name=did,
                 description=heading.group(1).strip() if heading else "",
                 path=path, scope="repo", status=_str(fm.get("status")))

def _locations(cwd: Path, config_root: Path, home: Path) -> list[tuple[Path, str, str]]:
    # Precedence order (spec §5.1): repo-tracked, harness-native, cross-project, then the two
    # decision corpora — the legacy `~/.claude` path is hardcoded by the skill and must be read
    # even when CLAUDE_CONFIG_DIR points elsewhere, or none of an existing corpus is found.
    out = [(cwd / "docs" / "memory", "repo", "memory")]
    out += [(config_root / "projects" / s / "memory", "repo", "memory") for s in _slugs(cwd)]
    out.append((config_root / "loadout" / "memory", "global", "memory"))
    out += [(root / "debug-decisions" / s, "repo", "decision")
            for root in (config_root, home / ".claude") for s in _slugs(cwd)]
    return out

def read_store(cwd: Path, config_root: Path, home: Path | None = None) -> Store:
    home = Path.home() if home is None else home
    slug = slug_for(cwd)
    entries: list[Entry] = []
    shadowed: list[tuple[Entry, Entry]] = []
    seen_paths: set[Path] = set()                  # realpath: memory-org symlinks one store onto another
    by_id: dict[str, Entry] = {}
    for directory, scope, kind in _locations(cwd, config_root, home):
        try:
            files = sorted(p for p in directory.iterdir() if p.suffix == ".md")
        except OSError:                            # absent store is the normal case
            continue
        for path in files:
            if path.name in _INDEX_NAMES:
                continue
            real = path.resolve()
            if real in seen_paths:
                continue
            seen_paths.add(real)
            entry = (_memory_entry(path, scope) if kind == "memory"
                     else _decision_entry(path, slug))
            if entry is None:
                continue
            if entry.id in by_id:                  # distinct files, same slug: earlier wins
                shadowed.append((by_id[entry.id], entry))
                continue
            by_id[entry.id] = entry
            entries.append(entry)
    return Store(entries=tuple(entries), shadowed=tuple(shadowed))
