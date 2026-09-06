"""Stop hook: at session end, ask the session to record what it decided.

Ported from the `debug-decisions` skill's own Stop hook, whose job this tool absorbed. It never
writes anything the store carries — it emits one line of context and lets the session decide.
Deterministic: signals are counted from the transcript, no model call and no ranking.

Reading the transcript is the whole cost, and `compose` gives every injected hook a 5 s ceiling,
so only the tail is parsed. A file long enough to be truncated is a long session by definition,
which is the only thing the turn count is used to decide.
"""
from __future__ import annotations
import json, os, re, sys, time
from pathlib import Path

MIN_TURNS = 5                                       # a short session decided nothing worth filing
MIN_FILES_EDITED = 3
# Stems, not words: matched as substrings against lowercased text, so one entry covers a verb's
# inflections. English, Italian, Spanish and German are covered by default because the cost of a
# stem is a substring test; `[memory] decision_keywords` replaces the list for any other language.
# The other three signals are structural, so an unlisted language is still noticed — it only loses
# the most conversational signal.
KEYWORDS = ("choose", "decid", "approach", "alternativ", "trade-off",   # en
            "scegl", "scelt", "approcc", "decis",                       # it
            "elegi", "escog", "enfoque",                                # es
            "entscheid", "wähl", "wahl", "ansatz", "abwäg")             # de
# Invoking one of these is a statement that the session was designing, not typing.
_DESIGN_SKILL = re.compile(r"^(superpowers:(brainstorming|writing-plans)|feature-dev:)")
_TMP_SLUG = re.compile(r"(-T$|-tmp-|-private-var-folders-)")
_TAIL_BYTES = 512 * 1024
_STATE_MAX_AGE = 7 * 24 * 3600                      # prompted-markers older than this are noise
_WRITERS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

def _state_dir(config_root: Path) -> Path:
    return config_root / "loadout" / ".state"

def _already_prompted(config_root: Path, session_id: str) -> bool:
    if not session_id:
        return False
    try:
        return json.loads((_state_dir(config_root) / f"{session_id}.json").read_text()) is True
    except (OSError, ValueError):                   # never prompted, or a marker we cannot read
        return False

def _mark_prompted(config_root: Path, session_id: str) -> None:
    if not session_id:
        return
    directory = _state_dir(config_root)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{session_id}.json").write_text("true")

def _cleanup_state(config_root: Path) -> None:
    cutoff = time.time() - _STATE_MAX_AGE
    try:
        markers = list(_state_dir(config_root).iterdir())
    except OSError:
        return
    for marker in markers:
        try:
            if marker.suffix == ".json" and marker.stat().st_mtime < cutoff:
                marker.unlink()
        except OSError:                             # a peer removed it first, or it is not ours
            continue

def _read_tail(path: Path) -> tuple[str, bool]:
    # Returns (text, truncated). A partial first line is dropped: it would fail to parse anyway.
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        truncated = size > _TAIL_BYTES
        handle.seek(max(0, size - _TAIL_BYTES))
        raw = handle.read().decode("utf-8", errors="ignore")
    return (raw.split("\n", 1)[-1] if truncated else raw), truncated

def parse_transcript(text: str) -> dict:
    # The transcript is JSONL, one harness event per line; a line we cannot read is skipped.
    turns, messages, files, plan_mode, skills = 0, [], set(), False, []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        content = (event.get("message") or {}).get("content")
        if event.get("type") == "user":
            if isinstance(content, str):
                said = content
            elif isinstance(content, list):
                said = "\n".join(b.get("text", "") for b in content
                                 if isinstance(b, dict) and b.get("type") == "text")
            else:
                said = ""
            if said:
                turns += 1
                messages.append(said)
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name, tool_input = block.get("name"), block.get("input")
            tool_input = tool_input if isinstance(tool_input, dict) else {}
            if name in _WRITERS and tool_input.get("file_path"):
                files.add(tool_input["file_path"])
            elif name == "EnterPlanMode":
                plan_mode = True
            elif name == "Skill" and tool_input.get("skill"):
                skills.append(str(tool_input["skill"]))
    return {"turns": turns, "messages": messages, "files": sorted(files),
            "plan_mode": plan_mode, "skills": skills}

def signals(parsed: dict, slug: str, truncated: bool = False,
            keywords: tuple[str, ...] = ()) -> list[str]:
    """Names the reasons this session looks like it made a decision; empty means stay quiet."""
    if not slug or _TMP_SLUG.search(slug):          # a scratch directory files nothing
        return []
    if not truncated and parsed["turns"] < MIN_TURNS:
        return []
    if any("cld decision" in m or "/decision" in m for m in parsed["messages"]):
        return []                                   # already registered; do not ask twice
    found = []
    if parsed["plan_mode"]:
        found.append("plan-mode")
    if len(parsed["files"]) >= MIN_FILES_EDITED:
        found.append("files-edited")
    if any(_DESIGN_SKILL.match(s) for s in parsed["skills"]):
        found.append("design-skill")
    haystack = "\n".join(parsed["messages"]).lower()
    if any(word in haystack for word in (keywords or KEYWORDS)):
        found.append("keyword")
    return found

def build_reminder(exe: str) -> str:
    return ("This session shows signs of architectural decisions or facts a later session would "
            "have to rediscover. If any were made, record them now — briefly, factually, and only "
            "what is not already obvious from the code or git history:\n"
            f"  {exe} decision new \"<what was decided, and why>\"\n"
            f"  {exe} memory add \"<one line>\"\n"
            "If there is nothing worth recording, say nothing and do not reply to this.")

def main() -> int:
    config_root = None
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        config_root = Path(os.environ["LOADOUT_CONFIG_ROOT"])
        session_id = str(payload.get("session_id") or "")
        if _already_prompted(config_root, session_id):
            return 0                                # one reminder per session, whatever else runs
        cwd = str(payload.get("cwd") or os.environ.get("LOADOUT_REPO") or "")
        transcript = payload.get("transcript_path")
        text, truncated = _read_tail(Path(transcript)) if transcript else ("", False)
        from ccloadout.debt_hook import LIST_SEP     # one separator for every env-passed list
        keywords = tuple(w for w in (os.environ.get("LOADOUT_DECISION_KEYWORDS") or "")
                         .lower().split(LIST_SEP) if w)
        if not signals(parse_transcript(text), cwd.replace("/", "-"), truncated, keywords):
            return 0
        exe = os.environ.get("LOADOUT_EXE") or "claude-loadout"
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "Stop", "additionalContext": build_reminder(exe)}}))
        _mark_prompted(config_root, session_id)
    except BaseException:                           # a reminder that fails is worth nothing at all
        return 0
    finally:
        if config_root is not None:
            try:
                _cleanup_state(config_root)
            except BaseException:
                pass
    return 0

if __name__ == "__main__":
    sys.exit(main())
