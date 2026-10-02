from __future__ import annotations
import os, re, tempfile
from datetime import date
from dataclasses import dataclass
from pathlib import Path
from ccloadout.inventory import _frontmatter

# Index files live beside entries in every store the reader walks; they list entries, they aren't one.
_INDEX_NAMES = {"MEMORY.md", "INDEX.md", "README.md"}
_HEADING = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_INDEX_LINE = re.compile(r"^- \[[^\]]*\]\(([^)]+)\)")   # `- [Title](file.md) — hook`
_INDEX_FILE = "MEMORY.md"
_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

def safe_name(raw: str) -> str:
    # An entry name becomes a filename. `--name ../../x` and `--name /etc/x` used to write there,
    # and the injected payload tells the session to run `memory add` — so the name is agent-reachable.
    name = _UNSAFE_NAME.sub("-", (raw or "").strip()).strip("-.")[:80]
    return name or "entry"

def one_line(raw: str) -> str:
    # Descriptions land in YAML frontmatter, in a MEMORY.md table row and in the system prompt.
    # A newline in any of those is a structural break, not text: collapse it here, once.
    return _CONTROL.sub("", " ".join((raw or "").split()))

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
    links: tuple[str, ...] = ()     # names of related notes, for the expansion pass in `recall`

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
    links = meta.get("links") if isinstance(meta.get("links"), list) else []
    body = raw.split("\n---", 1)[-1] if raw.startswith("---") else raw
    links = list(dict.fromkeys([str(l) for l in links] + _WIKILINK.findall(body)))
    return Entry(id=f"{kind}:{name}", kind=kind, name=name,
                 description=_str(fm.get("description")) or "", path=path,
                 # Scope is a property of the note, never of the folder it sits in: that is what
                 # lets a cross-project note live in a canonical store instead of one of ours.
                 scope=_str(meta.get("scope")) or scope,
                 status=_str(meta.get("status")),
                 anchors=tuple(anchors) if isinstance(anchors, list) else (),
                 content_sha=_str(meta.get("content_sha")),
                 links=tuple(links))

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
    close = raw.find("\n---", 3) if raw.startswith("---") else -1
    body = raw[close + 1:] if close != -1 else raw  # unterminated frontmatter: never mine it for a title
    heading = _HEADING.search(body)
    return Entry(id=f"decision:{project}/{did}", kind="decision", name=did,
                 description=heading.group(1).strip() if heading else "",
                 # Decisions written before the skill recorded a status are active: that is what
                 # its own INDEX.md shows for them, and "?" would read as an error, not a fact.
                 path=path, scope="repo", status=_str(fm.get("status")) or "active")

def _locations(cwd: Path, config_root: Path, home: Path) -> list[tuple[Path, str, str]]:
    # Precedence order (spec §5.1): repo-tracked, harness-native, cross-project, then the two
    # decision corpora — the legacy `~/.claude` path is hardcoded by the skill and must be read
    # even when CLAUDE_CONFIG_DIR points elsewhere, or none of an existing corpus is found.
    out = [(cwd / "docs" / "memory", "repo", "memory")]
    out += [(config_root / "projects" / s / "memory", "repo", "memory") for s in _slugs(cwd)]
    out += [(root / "debug-decisions" / s, "repo", "decision")
            for root in (config_root, home / ".claude") for s in _slugs(cwd)]
    return out

def _global_dirs(config_root: Path) -> list[Path]:
    # Every project's own memory directory. A note marked `scope: global` in any of them belongs
    # to every session; the rest of that project's notes stay where they are.
    try:
        return sorted(p / "memory" for p in (config_root / "projects").iterdir() if p.is_dir())
    except OSError:
        return []

def read_store(cwd: Path, config_root: Path, home: Path | None = None,
               scopes: tuple[str, ...] = ("repo", "global")) -> Store:
    home = Path.home() if home is None else home
    slug = slug_for(cwd)
    entries: list[Entry] = []
    shadowed: list[tuple[Entry, Entry]] = []
    seen_paths: set[Path] = set()                  # realpath: memory-org symlinks one store onto another
    by_id: dict[str, Entry] = {}
    # Local stores first (their notes may be either scope), then every other project's store,
    # from which only notes marked `scope: global` are taken.
    locations = [(d, s, k, False) for d, s, k in _locations(cwd, config_root, home)]
    if "global" in scopes:
        locations += [(d, "repo", "memory", True) for d in _global_dirs(config_root)]
    for directory, scope, kind, globals_only in locations:
        for entry in _read_dir(directory, scope, kind, slug):
            if entry.scope not in scopes or (globals_only and entry.scope != "global"):
                continue
            real = entry.path.resolve()
            if real in seen_paths:                 # memory-org symlinks one store onto another
                continue
            seen_paths.add(real)
            if entry.id in by_id:                  # distinct files, same slug: earlier wins
                shadowed.append((by_id[entry.id], entry))
                continue
            by_id[entry.id] = entry
            entries.append(entry)
    return Store(entries=tuple(entries), shadowed=tuple(shadowed))


class EntryExists(Exception):
    """A store already holds an entry with this name; entries are never silently overwritten."""

def store_dir(config_root: Path, repo: Path, git_tracked: bool) -> Path:
    # Where new entries are written. Tracked entries travel with the repository and reach
    # collaborators; untracked ones stay in the harness's own directory (spec §5.1, §9).
    return repo / "docs" / "memory" if git_tracked \
        else config_root / "projects" / harness_slug(repo) / "memory"

def write_entry(config_root: Path, repo: Path, name: str, description: str, kind: str,
                git_tracked: bool, anchors: list[str] | None = None, body: str = "",
                today: date | None = None, scope: str = "repo") -> Path:
    from ccloadout.recall import sha_of                   # local: recall imports memory
    # A global note still lives in a canonical store — the harness's own project directory — so
    # uninstalling this tool strands nothing and needs no migration.
    directory = store_dir(config_root, repo, git_tracked and scope != "global")
    directory.mkdir(parents=True, exist_ok=True)
    name, description = safe_name(name), one_line(description)
    path = directory / f"{name}.md"
    if not path.resolve().parent == directory.resolve():   # belt and braces over safe_name
        raise EntryExists(f"{path} would fall outside {directory}")
    if path.exists():
        raise EntryExists(str(path))
    anchors = anchors or []
    meta = [f"  node_type: memory",
            f"  loadout_kind: {kind}",
            f"  scope: {scope}",
            f"  created: {(today or date.today()).isoformat()}"]
    anchors = [one_line(a) for a in anchors if one_line(a)]
    if anchors:
        meta.append("  anchors: [" + ", ".join(anchors) + "]")
        meta.append(f"  content_sha: {sha_of([repo / a.split('#', 1)[0] for a in anchors])}")
    if kind == "debt":
        meta.append("  status: open")               # only an explicit resolve closes it (spec §11)
    front = "\n".join([f"name: {name}", f"description: {description}", "metadata:", *meta])
    path.write_text(f"---\n{front}\n---\n\n{body}\n" if body else f"---\n{front}\n---\n")
    _index_add(path, name, description)
    return path

class NoStatus(Exception):
    """The file carries no such key in its frontmatter, so there is nothing to set."""

def set_status(path: Path, status: str) -> None:
    set_meta(path, "status", status)

def set_meta(path: Path, key: str, value: str) -> None:
    # Rewrites exactly one line and leaves every other byte alone: keepends preserves CRLF and the
    # unicode separators `splitlines()` would otherwise normalise, and the write is atomic because
    # this touches the user's own notes, which may be git-tracked.
    raw = path.read_text(errors="replace")
    lines = raw.splitlines(keepends=True)
    end = next((i for i, ln in enumerate(lines[1:], 1) if ln.strip() == "---"), len(lines))
    for i, line in enumerate(lines[:end]):          # frontmatter only; a body line is not a key
        if line.strip().startswith(f"{key}:"):
            indent = line[:len(line) - len(line.lstrip())]
            newline = line[len(line.rstrip("\r\n")):]
            lines[i] = f"{indent}{key}: {value}{newline or chr(10)}"
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".entry-", suffix=".md")
            with os.fdopen(fd, "w", newline="") as fh:
                fh.write("".join(lines))
            os.replace(tmp, path)
            return
    # The key is absent: notes written by hand, or by the harness, carry no `scope:` or `status:`.
    # Insert it into the metadata block rather than refusing — refusing would mean a note you did
    # not create with this tool could never be promoted or resolved.
    meta_at = next((i for i, ln in enumerate(lines[:end]) if ln.strip() == "metadata:"), None)
    if meta_at is None:
        if not lines or lines[0].strip() != "---":
            raise NoStatus(f"{path} has no frontmatter to add {key} to")
        lines.insert(1, "metadata:\n")
        meta_at = 1
    lines.insert(meta_at + 1, f"  {key}: {value}\n")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".entry-", suffix=".md")
    with os.fdopen(fd, "w", newline="") as fh:
        fh.write("".join(lines))
    os.replace(tmp, path)

def read_all(config_root: Path, home: Path | None = None) -> dict[str, tuple[Entry, ...]]:
    # Every project's store under this profile, for auditing across repositories — memories
    # accumulate per project, and the ones worth deleting are usually in a repo you left behind.
    home = Path.home() if home is None else home
    out: dict[str, tuple[Entry, ...]] = {}
    for base, kind in ((config_root / "projects", "memory"),
                       (config_root / "debug-decisions", "decision"),
                       (home / ".claude" / "debug-decisions", "decision")):
        try:
            slugs = sorted(p for p in base.iterdir() if p.is_dir())
        except OSError:
            continue
        for slug_dir in slugs:
            directory = slug_dir / "memory" if kind == "memory" else slug_dir
            entries = _read_dir(directory, "repo", kind, slug_dir.name)
            if entries:
                out[slug_dir.name] = out.get(slug_dir.name, ()) + entries
    return out

def _read_dir(directory: Path, scope: str, kind: str, project: str) -> tuple[Entry, ...]:
    try:
        files = sorted(p for p in directory.iterdir() if p.suffix == ".md")
    except OSError:
        return ()
    out = []
    for path in files:
        if path.name in _INDEX_NAMES:
            continue
        entry = (_memory_entry(path, scope) if kind == "memory"
                 else _decision_entry(path, project))
        if entry is not None:
            out.append(entry)
    return tuple(out)


def indexed_files(config_root: Path, repo: Path) -> set[Path]:
    """Files the harness itself already puts in every session, via its MEMORY.md index.

    Verified on Claude Code 2.1.263: the harness injects the index lines and nothing else — an
    unindexed file reaches no session, and an indexed one contributes its title and hook. Whatever
    is in here is already resident, so recalling it again would spend the budget twice.
    """
    found: set[Path] = set()
    for slug in _slugs(repo):
        index = config_root / "projects" / slug / "memory" / _INDEX_FILE
        try:
            lines = index.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            m = _INDEX_LINE.match(line.strip())
            if m:
                target = (index.parent / m.group(1)).resolve()
                if target.exists():                 # a line pointing nowhere indexes nothing
                    found.add(target)
    return found

def index_line(name: str, filename: str, description: str = "") -> str:
    """One MEMORY.md entry, `- [name](file.md) — description`: the shape Claude Code reads."""
    desc = one_line(description)
    return f"- [{one_line(name)}]({filename})" + (f" — {desc}" if desc else "")

def _index_add(path: Path, name: str, description: str) -> None:
    # Only where an index already exists: inventing one would start injecting entries into every
    # session, which is the opposite of what this tool is for.
    index = path.parent / _INDEX_FILE
    if not index.exists():
        return
    line = index_line(name, path.name, description) + "\n"
    text = index.read_text(errors="replace")
    index.write_text(text if line in text else text.rstrip("\n") + "\n" + line)

def _index_remove(path: Path) -> None:
    index = path.parent / _INDEX_FILE
    if not index.exists():
        return
    target = path.resolve()
    def points_here(line: str) -> bool:
        m = _INDEX_LINE.match(line.strip())
        # Compare resolved paths: two entries can share a basename in different subdirectories,
        # and removing the wrong line silently un-indexes a note nobody touched.
        return bool(m) and (index.parent / m.group(1)).resolve() == target
    kept = [ln for ln in index.read_text(errors="replace").splitlines(keepends=True)
            if not points_here(ln)]
    index.write_text("".join(kept))

def forget_entry(path: Path) -> None:
    """Delete an entry and the index line that pointed at it, so the harness is left consistent."""
    _index_remove(path)                             # before the unlink: resolve() needs the file
    path.unlink(missing_ok=True)
