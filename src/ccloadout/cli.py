from __future__ import annotations
import json, os, re, subprocess, sys, time
from fnmatch import fnmatch
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import NamedTuple
from ccloadout.config import load_config, set_memory_enabled
from ccloadout.inventory import claude_code_inventory, Item
from ccloadout.goal import detect_goal, write_goal_cache
from ccloadout.ranker import Ranker, make_model2vec_embed, keyword_embed, bundled_model_path, resolve_model_source
from ccloadout.compose import compose
from ccloadout.memory import (EntryExists, read_all, read_store, set_status,
                              slug_for, write_entry)
from ccloadout.flags import clear_flag, load_flags, set_flag
from ccloadout.usage import load_usage, record_delivery
from ccloadout.candidates import drop_rows, load_candidates, record_session
from ccloadout.decisions import corpus_dir, new_decision, supersede
from ccloadout.recall import (anchor_state, assess, build_payload, estimate_tokens,
                              recall_command, search, select, strip_frontmatter)
from ccloadout.rules import (load_rules, apply_rules, has_rule, save_rule, write_rules,
                            read_rules, profile_rules_file, rules_for,
                            Rule, Predicate, evaluate)
from ccloadout.compiler import compile_rule, make_local_instruct
from ccloadout import savings as _savings
from ccloadout import measure as _measure

_LAUNCH_PAUSE_S = 1.5   # seeded launch: hold the scoping summary on screen before claude's TUI takes over

class _Abort(Exception):
    """User declined to pick a profile at the selection prompt."""

# Color codes resolve through the shared Sondalab palette: on a non-truecolor
# terminal the named-ANSI floor is byte-identical to the previous static codes;
# on COLORTERM=truecolor they upgrade to the brand hues. dim/bold stay styles.
import sondalab_palette as _sl
_STYLE_SGR = {"dim": "2", "bold": "1"}
_COLOR_ROLE = {"green": "ok", "red": "err", "yellow": "warn", "cyan": "accent"}

def _sgr(code: str) -> str:
    if code in _STYLE_SGR:
        return _STYLE_SGR[code]
    ansi, rgb = _sl.ROLES[_COLOR_ROLE[code]]
    return f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}" if _sl.supports_truecolor() else str(ansi)

def _supports_color(err: bool) -> bool:
    # Looked up lazily (not cached at import) so pytest's capsys stream swap is honoured.
    if os.environ.get("NO_COLOR") or os.environ.get("LOADOUT_NO_COLOR"):
        return False
    stream = sys.stderr if err else sys.stdout
    return stream.isatty()

def _paint(text: str, *codes: str, err: bool = False) -> str:
    if not _supports_color(err):
        return text
    return f"\033[{';'.join(_sgr(c) for c in codes)}m{text}\033[0m"

def _yn(flag: bool, *, err: bool = False) -> str:
    return _paint("present", "green", err=err) if flag else _paint("absent", "dim", err=err)

def _warn(msg: str) -> None:
    print(f"{_paint('claude-loadout:', 'yellow', err=True)} {msg}", file=sys.stderr)

def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"

def _cleanup(tmp_paths) -> None:
    for p in tmp_paths:
        try:
            p.unlink()
        except OSError:
            pass

def _interactive(passthrough: list[str]) -> bool:
    return sys.stdin.isatty() and "-p" not in passthrough and "--print" not in passthrough

def _ask(prompt: str) -> str:                       # EOF on piped stdin -> treat as skip
    try:
        return input(prompt)
    except EOFError:
        return ""

def _build_embed(model_name: str):
    try:
        return make_model2vec_embed(model_name)
    except Exception as exc:                       # import error, download failure, etc.
        _warn(f"model unavailable ({exc}); using keyword fallback")
        return keyword_embed

def _build_compiler(cfg):
    if not cfg.rule_model_path:
        return None
    try:
        return make_local_instruct(cfg.rule_model_path)
    except Exception as exc:
        _warn(f"rule model unavailable ({exc}); rule authoring disabled")
        return None

def _resolve_goal(cwd: Path, passthrough: list[str]) -> tuple[str, str, float]:
    goal = detect_goal(cwd)                          # returns (text, source, confidence)
    if goal.confidence < 0.15 and _interactive(passthrough):
        entered = _ask(f"claude-loadout: session goal? [{goal.goal}] ").strip()
        if entered:
            write_goal_cache(cwd, entered)         # preflight ruling: persist, don't re-ask
            return entered, "prompt", 1.0
    return goal.goal, goal.source, goal.confidence

def _rules_intro(compile_fn, scope: str) -> None:
    # Printed once before an elicitation run so the interaction isn't a cold prompt.
    # `scope` names where authored rules land (this repo vs the whole profile).
    p = lambda s: print(s, file=sys.stderr)
    dim = lambda s: _paint(s, "dim", err=True)
    p("")
    if compile_fn:
        p(f"{_paint('claude-loadout:', 'yellow', err=True)} for each tool, describe in plain "
          "language when to keep or drop it.")
        p(dim('    e.g.  "keep only when the goal is frontend"'))
        p(dim('          "drop unless it mentions email"'))
        p(dim('          "always keep this"'))
        p(dim("    press enter to skip; if a description can't be translated "
              "you'll get keep/drop/skip choices."))
    else:
        p(f"{_paint('claude-loadout:', 'yellow', err=True)} no rule model configured — "
          "natural-language rules are unavailable.")
        p(dim("    [k]eep always / [d]rop always write a permanent rule; "
              "[s]kip (enter) decides nothing and asks again next time."))
    p(dim(f"    rules you author here apply {scope}."))
    p("")

def _pick_keep_drop_skip(item: Item) -> Predicate | None:
    choice = _ask(f"  '{item.id}': [k]eep always / [d]rop always / [s]kip (decide later)? ").strip().lower()
    return {"k": Predicate("always_keep", (), "any"),
            "d": Predicate("always_drop", (), "any")}.get(choice)

def _elicit(item: Item, context: str, compile_fn, save) -> str:
    # `save(Rule)` persists an authored rule to the chosen scope (profile or a repo's local file).
    nl = ""
    if compile_fn:                                 # natural-language authoring path
        nl = _ask(f"claude-loadout: rule for '{item.id}' ({item.kind})? [enter=skip] ").strip()
        if not nl:
            return "undecided"
        pred = compile_rule(nl, item, compile_fn)
        if pred is not None:
            save(Rule(target=item.id, nl=nl, predicate=pred))
            return evaluate(pred, context)
        _warn(f"couldn't translate that into a rule for '{item.id}'; choose manually")
    pred = _pick_keep_drop_skip(item)              # spec §7 degrade / no rule model
    if pred is None:
        return "undecided"
    save(Rule(target=item.id, nl=nl, predicate=pred))
    return evaluate(pred, context)

class _Memory(NamedTuple):
    shown: int
    total: int
    injected: int                                   # resident tokens the payload costs (heuristic)
    delivered: tuple = ()                           # entry ids in the payload, counted only on launch
    config_root: Path | None = None

class _Scope(NamedTuple):
    goal: str
    source: str
    confidence: float
    threshold: float
    kept: list
    dropped: list
    savings: _savings.Savings
    connectors: list                                # (id, tokens) claude.ai connectors strict-mode drops
    measured: bool                                  # whether a costs.json cache was found
    memory: _Memory | None = None                   # None when [memory] is off

class _EditGate(NamedTuple):
    # Everything the pre-launch review needs to redraw and re-compose a plan.
    items: list
    editable: list                                  # prunable, non-pinned items the user may toggle
    kept_ids: set                                   # prunable ids currently kept (checkbox preset)
    pinned_ids: set
    replan: object                                  # callable(selected_ids) -> (_Scope, plan)
    cwd: Path
    cfg: object
    goal: str

def _recall_payload(cfg, cwd: Path, goal: str, embed):
    # Ranked recall rides in as system-prompt text. Off by default; below min_entries it injects
    # nothing at all, since an instruction to query an empty store costs tokens for no answer.
    if not cfg.memory.enabled:
        return None, None
    store = read_store(cwd, cfg.config_root)
    total = len(store.entries)
    if total < cfg.memory.min_entries:
        return None, _Memory(0, total, 0)
    exe = recall_command()
    chosen = select(store.entries, goal, embed, cfg.memory.threshold,
                    cfg.memory.budget_tokens, exe=exe, root=cwd,
                    usage=load_usage(cfg.config_root, cwd),
                    promote_after=cfg.memory.promote_after,
                    decay_days=cfg.memory.decay_days, decay_factor=cfg.memory.decay_factor)
    payload = build_payload(chosen, exe=exe, total=total, root=cwd)
    return payload, _Memory(len(chosen), total, estimate_tokens(payload),
                            tuple(e.id for e in chosen), cfg.config_root)

def _git(cwd: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    out = getattr(proc, "stdout", None)
    return out.strip() if getattr(proc, "returncode", 1) == 0 and isinstance(out, str) else None

def _changed_since(cwd: Path, head_before: str | None) -> list[str]:
    names = []
    head_now = _git(cwd, "rev-parse", "HEAD")
    if head_before and head_now and head_before != head_now:
        names += (_git(cwd, "diff", "--name-only", head_before, head_now) or "").splitlines()
    dirty = (_git(cwd, "status", "--porcelain") or "").splitlines()
    names += [line[3:] for line in dirty if len(line) > 3]
    return sorted(dict.fromkeys(n for n in names if n))

def _capture_session(scope, cwd: Path, exit_code: int, head_before: str | None) -> None:
    # The launcher outlives the session (lever F), so the end-of-session envelope needs no hook.
    mem = getattr(scope, "memory", None)
    if mem is None or mem.config_root is None:
        return
    try:
        record_session(mem.config_root, cwd, goal=scope.goal, exit_code=exit_code,
                       changed=_changed_since(cwd, head_before))
    except OSError as exc:                          # never fail a session over its own bookkeeping
        _warn(f"could not record session candidate ({exc})")

def _cmd_consolidate(cwd: Path, override: Path | None = None) -> int:
    cfg = load_config(cwd=cwd, config_root_override=override)
    rows = load_candidates(cfg.config_root, cwd)
    if not rows:
        print(_paint("no session candidates recorded for this repository", "dim"))
        return 0
    groups: dict[str, list[dict]] = {}
    for row in rows:                                # near-duplicates collapse by goal
        groups.setdefault(str(row.get("goal") or ""), []).append(row)
    interactive = _interactive([])
    kept_rows: list[dict] = []
    for goal, rows_for_goal in groups.items():
        files = sorted({f for r in rows_for_goal for f in (r.get("changed") or [])})
        print(_paint(f"  {_plural(len(rows_for_goal), 'session')} · {goal}", "bold"))
        if files:
            print(_paint(f"    touched: {', '.join(files[:6])}"
                         + (" …" if len(files) > 6 else ""), "dim"))
        if not interactive:                         # no TTY: report, promote nothing
            kept_rows += rows_for_goal
            continue
        answer = _ask("    [k]eep as a memory / [d]iscard / [s]kip? ").strip().lower()
        if answer.startswith("d"):
            continue                                # dropped: the rows go, no entry is written
        if not answer.startswith("k"):
            kept_rows += rows_for_goal
            continue
        description = _ask("    one line describing what to remember: ").strip() or goal
        try:
            path = write_entry(cfg.config_root, cwd, name=_slugify(description),
                               description=description, kind="memory",
                               git_tracked=cfg.memory.git_tracked,
                               body="\n".join(f"- {r.get('at')} · exit {r.get('exit_code')}"
                                               for r in rows_for_goal))
        except EntryExists as exc:
            _warn(f"an entry already exists at {exc}; keeping the candidate")
            kept_rows += rows_for_goal
            continue
        print(f"    {_paint('wrote', 'green')} {path}")
    if interactive:
        drop_rows(cfg.config_root, cwd, kept_rows)
    return 0

def _record_delivery(scope, cwd: Path) -> None:
    mem = getattr(scope, "memory", None)
    if mem and mem.delivered and mem.config_root is not None:
        try:
            record_delivery(mem.config_root, cwd, mem.delivered)
        except OSError as exc:                      # a counter is never worth failing a launch for
            _warn(f"could not record memory usage ({exc})")

def _slugify(text: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-").split("-")
    return "-".join(w for w in words if w)[:60] or "entry"

def _pop_flag(args: list[str], flag: str) -> tuple[list[str], list[str]]:
    values = []
    while flag in args:
        at = args.index(flag)
        values.append(args[at + 1]) if at + 1 < len(args) else None
        args = args[:at] + args[at + 2:]
    return args, values

def _cmd_write_entry(cwd: Path, args: list[str], kind: str, override: Path | None) -> int:
    args, names = _pop_flag(list(args), "--name")
    args, anchors = _pop_flag(args, "--anchor")
    description = " ".join(args).strip()
    if not description:
        _warn(f"{kind} add needs a description")
        return 2
    cfg = load_config(cwd=cwd, config_root_override=override)
    try:
        path = write_entry(cfg.config_root, cwd, name=names[0] if names else _slugify(description),
                           description=description, kind=kind,
                           git_tracked=cfg.memory.git_tracked, anchors=anchors)
    except EntryExists as exc:
        _warn(f"an entry already exists at {exc}; pick another --name")
        return 1
    print(f"{_paint('wrote', 'green')} {path}")
    return 0

def _cmd_debt(cwd: Path, args: list[str], override: Path | None = None) -> int:
    action, rest = (args[0], args[1:]) if args else ("list", [])
    if action == "add":
        return _cmd_write_entry(cwd, rest, "debt", override)
    cfg = load_config(cwd=cwd, config_root_override=override)
    entries = [e for e in read_store(cwd, cfg.config_root).entries if e.kind == "debt"]
    if action == "resolve":
        if not rest:
            _warn("debt resolve needs the name of an entry")
            return 2
        match = next((e for e in entries if e.name == rest[0]), None)
        if match is None:
            _warn(f"no debt entry named {rest[0]!r}")
            return 1
        set_status(match.path, "resolved")
        print(f"{_paint('resolved', 'green')} {match.name}")
        return 0
    if action != "list":
        _warn(f"unknown debt command {action!r}; try add, list or resolve")
        return 2
    show_all = "--all" in rest
    shown = [e for e in entries if show_all or e.status != "resolved"]
    if not shown:
        print(_paint("no open debt recorded for this repository", "dim"))
        return 0
    for e in shown:
        state = _paint("resolved", "dim") if e.status == "resolved" else _paint("open", "yellow")
        mark = "" if anchor_state(e, cwd) != "missing" else _paint("  ⚠ anchor gone", "yellow")
        print(f"  {state}  {e.name} — {_short_desc(e.description, 60)}{mark}")
    return 0

def _prompt_recall_env(cfg, cwd: Path, mem) -> dict | None:
    # Slice 3: a per-prompt hook, off unless asked for. It re-ranks lexically (no model, ~40 ms
    # measured) and is told what is already resident so it never repeats the launch payload.
    if not (cfg.memory.enabled and cfg.memory.prompt_recall) or mem is None:
        return None
    return {"LOADOUT_CONFIG_ROOT": str(cfg.config_root),
            "LOADOUT_REPO": str(cwd),
            "LOADOUT_RESIDENT_IDS": ",".join(mem.delivered),
            "LOADOUT_PROMPT_MAX": cfg.memory.prompt_recall_max,
            "LOADOUT_PROMPT_TIMEOUT_MS": cfg.memory.prompt_timeout_ms}

_AUDIT_HINT = "↑/↓ move · space keep/drop · a all/none · enter apply · q cancel"
_INTRO_MARKER = ".memory-intro-seen"                # printed once per profile, then never again

def _memory_state(cwd: Path, cfg) -> dict:
    store = read_store(cwd, cfg.config_root)
    usage = load_usage(cfg.config_root, cwd)
    return {"entries": store.entries,
            "kinds": {k: sum(1 for e in store.entries if e.kind == k)
                      for k in sorted({e.kind for e in store.entries})},
            "usage": usage,
            "promoted": sum(1 for r in usage.values() if r.uses >= cfg.memory.promote_after),
            "flags": load_flags(cfg.config_root, cwd),
            "candidates": load_candidates(cfg.config_root, cwd),
            "gone": [e for e in store.entries if anchor_state(e, cwd) == "missing"],
            "debt": [e for e in store.entries if e.kind == "debt" and e.status != "resolved"],
            "shadowed": store.shadowed}

def _memory_next_steps(state: dict, enabled: bool) -> list[tuple[str, str]]:
    # At most three, chosen by what the store actually needs right now — an empty list means
    # there is nothing to do, which is worth saying plainly rather than filling with advice.
    if not enabled:
        return [("claude-loadout memory enable", "turn ranked recall on for this repository")]
    steps = []
    if state["candidates"]:
        steps.append(("claude-loadout memory consolidate",
                      f"{_plural(len(state['candidates']), 'recorded session')} not yet a memory"))
    if state["flags"] or state["gone"]:
        why = " and ".join(filter(None, [
            _plural(len(state["flags"]), "flag") if state["flags"] else "",
            f"{_plural(len(state['gone']), 'dead anchor')}" if state["gone"] else ""]))
        steps.append(("claude-loadout memory audit", f"{why} to review"))
    if not state["entries"]:
        steps.append(("claude-loadout memory add <note>", "the store is empty; nothing to recall"))
    if state["debt"]:
        steps.append(("claude-loadout debt list",
                      f"{_plural(len(state['debt']), 'open item')} still outstanding"))
    if not steps and state["entries"]:
        steps.append(("claude-loadout memory audit --context '<goal>'",
                      "see what a session on that goal would recall, and why"))
    return steps[:3]

def _print_memory_status(cwd: Path, cfg) -> int:
    on = cfg.memory.enabled
    head = _paint("on", "green") if on else _paint("off", "dim")
    print(f"{_paint('  memory', 'bold')} — {head} for this repository")
    if not on:
        print(_paint("    Ranked recall puts only the notes relevant to a session into its "
                     "context,", "dim"))
        print(_paint("    inside a token budget you set. Nothing is loaded until you turn it on.",
                     "dim"))
    state = _memory_state(cwd, cfg)
    if on or state["entries"]:
        kinds = ", ".join(f"{n} {k}" for k, n in state["kinds"].items())
        print(f"    entries:      {len(state['entries'])}" + (f"  ({kinds})" if kinds else ""))
        if state["usage"]:
            print(f"    delivered:    {len(state['usage'])}, "
                  f"{state['promoted']} promoted")
        if state["candidates"]:
            print(f"    candidates:   {len(state['candidates'])} "
                  + _paint("sessions waiting to be reviewed", "dim"))
        for label, rows in (("flags", state["flags"]), ("dead anchors", state["gone"]),
                            ("open debt", state["debt"])):
            if rows:
                print(f"    {label + ':':<14}{_paint(str(len(rows)), 'yellow')}")
    steps = _memory_next_steps(state, on)
    if steps:
        print()
        print(_paint("  next", "bold"))
        width = max(len(cmd) for cmd, _ in steps)
        for command, why in steps:
            print(f"    {_paint(command.ljust(width), 'cyan')}  {_paint(why, 'dim')}")
    return 0

def _cmd_memory_toggle(cwd: Path, enabled: bool, override: Path | None) -> int:
    path = set_memory_enabled(cwd, enabled)
    word = "enabled" if enabled else "disabled"
    print(f"{_paint(word, 'green' if enabled else 'dim')} memory recall for this repository "
          + _paint(f"({path})", "dim"))
    if enabled:
        cfg = load_config(cwd=cwd, config_root_override=override)
        state = _memory_state(cwd, cfg)
        if not state["entries"]:
            print(_paint("  the store is empty — notes arrive with `claude-loadout memory add`, "
                         "or from sessions you consolidate", "dim"))
        else:
            print(_paint(f"  {_plural(len(state['entries']), 'entry')} ready; "
                         "`claude-loadout memory audit --context \'<goal>\'` shows what a "
                         "session would get", "dim"))
    return 0

def _memory_intro_once(mem) -> None:
    # The first injection is otherwise invisible: the user sees no difference and does not know
    # anything can be reviewed or undone. Said once per profile, never again.
    if mem is None or not mem.shown or mem.config_root is None:
        return
    marker = mem.config_root / "loadout" / _INTRO_MARKER
    if marker.exists():
        return
    p = lambda s: print(s, file=sys.stderr)
    dim = lambda s: _paint(s, "dim", err=True)
    p("")
    p(f"{_paint('claude-loadout:', 'yellow', err=True)} "
      f"{_plural(mem.shown, 'note')} from earlier sessions went into this one "
      f"(~{_savings.human_tokens(mem.injected)} tokens).")
    p(dim("    the session can search the rest itself with `claude-loadout recall`"))
    p(dim("    review or delete them:  claude-loadout memory audit"))
    p(dim("    turn it off here:       claude-loadout memory disable"))
    p("")
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("")
    except OSError:                                 # unwritable profile: say it once per run, not never
        pass

def _entry_signals(entry, cwd: Path, usage, flags, shadowed_ids: set) -> str:
    bits = []
    state = anchor_state(entry, cwd)
    if state == "missing":
        bits.append(_paint("anchor gone", "red", err=True))
    elif state == "changed":
        bits.append(_paint("code changed", "yellow", err=True))
    if entry.id in flags:
        bits.append(_paint(f"flagged: {_short_desc(flags[entry.id].reason, 40)}", "yellow", err=True))
    if entry.id in shadowed_ids:
        bits.append(_paint("shadowed", "dim", err=True))
    if entry.status == "resolved":
        bits.append(_paint("resolved", "dim", err=True))
    rec = usage.get(entry.id)
    if rec:
        bits.append(_paint(f"{_plural(rec.uses, 'use')} · last {rec.last_used}", "dim", err=True))
    else:
        bits.append(_paint("never delivered", "dim", err=True))
    return "  ".join(bits)

def _audit_rows(cwd: Path, cfg, all_repos: bool):
    usage, flags = load_usage(cfg.config_root, cwd), load_flags(cfg.config_root, cwd)
    if not all_repos:
        store = read_store(cwd, cfg.config_root)
        shadowed = {dropped.id for _, dropped in store.shadowed}
        return [("this repository", e) for e in store.entries], usage, flags, shadowed
    rows = [(project, e) for project, entries in sorted(read_all(cfg.config_root).items())
            for e in entries]
    return rows, usage, flags, set()

def _cmd_audit(cwd: Path, args: list[str], override: Path | None = None) -> int:
    args, contexts = _pop_flag(list(args), "--context")
    all_repos, as_json = "--all-repos" in args, "--json" in args
    cfg = load_config(cwd=cwd, config_root_override=override)
    rows, usage, flags, shadowed = _audit_rows(cwd, cfg, all_repos)
    if not rows:
        print(_paint("no memory entries to audit", "dim"))
        return 0
    if contexts:
        return _audit_context(cwd, cfg, [e for _, e in rows], contexts[0], usage, flags, as_json)
    if as_json:
        print(json.dumps([{"project": project, "id": e.id, "kind": e.kind, "scope": e.scope,
                           "name": e.name, "description": e.description, "path": str(e.path),
                           "status": e.status, "anchors": list(e.anchors),
                           "anchor_state": anchor_state(e, cwd),
                           "uses": usage[e.id].uses if e.id in usage else 0,
                           "last_used": usage[e.id].last_used if e.id in usage else None,
                           "flagged": flags[e.id].reason if e.id in flags else None,
                           "shadowed": e.id in shadowed}
                          for project, e in rows], indent=1))
        return 0
    if not _interactive([]):
        for project, e in rows:
            print(f"  {_paint(f'[{e.kind} · {project}]', 'dim')} {e.name} — "
                  f"{_short_desc(e.description, 50)}")
            signals = _entry_signals(e, cwd, usage, flags, shadowed)
            if signals:
                print(f"      {signals}")
        return 0
    return _audit_interactive(cwd, cfg, rows, usage, flags, shadowed)

def _audit_interactive(cwd: Path, cfg, rows, usage, flags, shadowed) -> int:
    labels, headers, seen = [], {}, None
    for i, (project, entry) in enumerate(rows):
        if project != seen:
            headers[i] = project
            seen = project
        signals = _entry_signals(entry, cwd, usage, flags, shadowed)
        labels.append(f"{_short_desc(entry.name, 34):<34} {_short_desc(entry.description, 44)}"
                      + (f"   {signals}" if signals else ""))
    title = (_paint("claude-loadout — memory audit", "bold", err=True) + "\n"
             + _paint("  unchecked entries are proposed for deletion", "dim", err=True))
    keep = _checkbox_select(title, labels, hint=_AUDIT_HINT, headers=headers, allow_empty=True)
    if keep is None:
        _warn("audit cancelled; nothing was changed")
        return 0
    doomed = [rows[i][1] for i in range(len(rows)) if i not in set(keep)]
    if not doomed:
        print(_paint("nothing to delete", "dim"))
        return 0
    print(_paint(f"  about to delete {_plural(len(doomed), 'entry')}:", "bold"))
    for entry in doomed:
        print(f"    {_paint('✗', 'red')} {entry.path}")
    if not _ask("  type 'delete' to confirm: ").strip().lower().startswith("delete"):
        _warn("nothing was deleted")
        return 0
    for entry in doomed:
        try:
            entry.path.unlink()
        except OSError as exc:
            _warn(f"could not delete {entry.path} ({exc})")
            continue
        clear_flag(cfg.config_root, cwd, entry.id)
    print(f"  {_paint('deleted', 'green')} {_plural(len(doomed), 'entry')}")
    return 0

def _audit_context(cwd: Path, cfg, entries, context: str, usage, flags, as_json: bool) -> int:
    verdicts = assess(entries, context, _build_embed(cfg.model_name), cfg.memory.threshold,
                      cfg.memory.budget_tokens, exe=recall_command(), root=cwd,
                      usage=usage, flags=flags, promote_after=cfg.memory.promote_after,
                      decay_days=cfg.memory.decay_days, decay_factor=cfg.memory.decay_factor)
    if as_json:
        print(json.dumps([{"id": v.entry.id, "name": v.entry.name, "score": round(v.score, 4),
                           "base": round(v.base, 4), "reasons": list(v.reasons),
                           "admitted": v.admitted} for v in verdicts], indent=1))
        return 0
    print(_paint(f"  what a session on {context!r} would recall", "bold"))
    print(_paint(f"  threshold {cfg.memory.threshold} · budget {cfg.memory.budget_tokens} tokens",
                 "dim"))
    print()
    cut_drawn = False
    for v in verdicts:
        if not v.admitted and not cut_drawn:
            print(_paint("    ── below the line ──", "dim"))
            cut_drawn = True
        mark = _paint("✓", "green") if v.admitted else _paint("·", "dim")
        why = _paint("  " + ", ".join(v.reasons), "dim") if v.reasons else ""
        moved = "" if abs(v.score - v.base) < 1e-9 else _paint(f" (from {v.base:.3f})", "dim")
        print(f"    {mark} {v.score:.3f}{moved}  "
              f"{_paint(v.entry.kind[:4], 'dim')} {_short_desc(v.entry.name, 38):<38} "
              f"{_short_desc(v.entry.description, 38)}{why}")
    return 0

def _cmd_flag(cwd: Path, args: list[str], override: Path | None = None) -> int:
    args, reasons = _pop_flag(list(args), "--reason")
    clearing = "--clear" in args
    names = [a for a in args if not a.startswith("--")]
    if not names:
        _warn("memory flag needs the name of an entry")
        return 2
    cfg = load_config(cwd=cwd, config_root_override=override)
    match = next((e for e in read_store(cwd, cfg.config_root).entries
                  if e.name == names[0] or e.name.startswith(names[0])), None)
    if match is None:
        _warn(f"no entry named {names[0]!r}")
        return 1
    if clearing:
        clear_flag(cfg.config_root, cwd, match.id)
        print(f"{_paint('cleared', 'green')} {match.name}")
        return 0
    if not reasons:
        _warn("flagging needs --reason; a flag without one cannot be judged later")
        return 2
    set_flag(cfg.config_root, cwd, match.id, reasons[0])
    print(f"{_paint('flagged', 'yellow')} {match.name} — {reasons[0]}")
    return 0

def _cmd_decision(cwd: Path, args: list[str], override: Path | None = None) -> int:
    action, rest = (args[0], args[1:]) if args else ("list", [])
    cfg = load_config(cwd=cwd, config_root_override=override)
    decisions = [e for e in read_store(cwd, cfg.config_root).entries if e.kind == "decision"]
    if action == "new":
        rest, tag_args = _pop_flag(list(rest), "--tags")
        title = " ".join(rest).strip()
        if not title:
            _warn("decision new needs a title")
            return 2
        tags = [t for arg in tag_args for t in arg.split(",") if t]
        path = new_decision(corpus_dir(cfg.config_root, cwd), slug_for(cwd), title, tags)
        print(f"{_paint('wrote', 'green')} {path}")
        return 0
    if action == "show":
        match = next((e for e in decisions if rest and e.name.startswith(rest[0])), None)
        if match is None:
            _warn("decision show needs the id of an existing decision")
            return 1
        print(strip_frontmatter(match.path.read_text(errors="ignore")).strip())
        return 0
    if action == "supersede":
        if len(rest) < 2:
            _warn("decision supersede needs an id and the title of the new decision")
            return 2
        old = next((e for e in decisions if e.name.startswith(rest[0])), None)
        if old is None:
            _warn(f"no decision matching {rest[0]!r}")
            return 1
        path = new_decision(corpus_dir(cfg.config_root, cwd), slug_for(cwd), " ".join(rest[1:]), [])
        supersede(old.path, path.stem)
        print(f"{_paint('wrote', 'green')} {path}\n{_paint('superseded', 'dim')} {old.name}")
        return 0
    if action != "list":
        _warn(f"unknown decision command {action!r}; try new, list, show or supersede")
        return 2
    if not decisions:
        print(_paint("no decisions recorded for this repository", "dim"))
        return 0
    for e in decisions:
        state = _paint("active", "green") if e.status == "active" else _paint(e.status or "?", "dim")
        print(f"  {state}  {e.name} — {_short_desc(e.description, 60)}")
    return 0

def _cmd_recall(cwd: Path, args: list[str], config_root_override: Path | None = None) -> int:
    limit = 3
    if "--limit" in args:
        at = args.index("--limit")
        limit = max(1, int(args[at + 1])); args = args[:at] + args[at + 2:]
    query = " ".join(args).strip()
    cfg = load_config(cwd=cwd, config_root_override=config_root_override)
    store = read_store(cwd, cfg.config_root)
    if not store.entries:
        print(_paint("no memory entries found for this repository", "dim"))
        return 0
    if not query:                                   # no query: name what is there, cheaply
        for e in store.entries:
            print(f"  {_paint(f'[{e.kind} · {e.scope}]', 'dim')} {e.name} — {_short_desc(e.description, 60)}")
        return 0
    for entry, score in search(store.entries, query, _build_embed(cfg.model_name), limit):
        state = anchor_state(entry, cwd)
        mark = {"missing": "  ⚠ anchored code is gone",
                "changed": "  ⚠ anchored code changed since this was written"}.get(state, "")
        print(_paint(f"{entry.name}  ({entry.kind} · {entry.scope} · {score:.3f})", "bold")
              + _paint(mark, "yellow"))
        print(_paint(f"{entry.path}", "dim"))
        body = strip_frontmatter(entry.path.read_text(errors="ignore")).strip()
        print(body + "\n")
    return 0

def _scoped_plan(passthrough: list[str], cwd: Path, config_root_override: Path | None = None,
                 scope_skills: bool = True):
    cfg = load_config(cwd=cwd, config_root_override=config_root_override)
    items = claude_code_inventory(cfg.config_root, cwd, cfg.global_config_path)
    if not items:
        return None, None, None
    context, gsource, gconf = _resolve_goal(cwd, passthrough)
    rules = load_rules(cfg.config_root, cwd)
    pinned = [i for i in items if any(fnmatch(i.id, g) for g in cfg.always_keep)]
    pinned_ids = {i.id for i in pinned}            # always_keep config wins over rules (spec §12)
    remainder = [i for i in items if i.id not in pinned_ids]
    outcome = apply_rules(remainder, rules, context)
    embed = _build_embed(cfg.model_name)
    ranked = Ranker(embed=embed).rank(context, list(outcome.undecided), cfg.threshold, cfg.always_keep)
    # Skill scoping on: every kind (mcp, plugin, skill) is prunable and flows through keep/drop
    # (compose turns dropped user skills off via skillOverrides). --no-scope-skills force-keeps
    # skills — none get dropped, so compose writes no overrides.
    force_keep = lambda i: not scope_skills and i.kind == "skill"
    always_loaded = ([i for i, _ in ranked.dropped if force_keep(i)]
                     + [i for i in outcome.forced_drop if force_keep(i)])
    kept = list(pinned) + list(outcome.forced_keep) + list(ranked.kept) + always_loaded
    dropped = ([(i, s) for i, s in ranked.dropped if not force_keep(i)]
               + [(i, "rule") for i in outcome.forced_drop if not force_keep(i)])
    # Launch-time keep/drop review is the pre-launch gate (a single checkbox over every prunable
    # tool), not a per-item prompt — see _launch_gate. `claude-loadout rules` remains the per-item /
    # natural-language authoring path.
    payload, mem = _recall_payload(cfg, cwd, context, embed)
    prompt_recall = _prompt_recall_env(cfg, cwd, mem)
    def _finish(kept, dropped):                    # compose + cost accounting for a keep/drop decision
        plan = compose(kept, items, cfg.config_root, passthrough, cwd=cwd,
                       global_config_path=cfg.global_config_path,
                       launch_config_dir=_explicit_profile(config_root_override),
                       memory_payload=payload, prompt_recall=prompt_recall)
        measured = _measure.load_costs(cfg.config_root)
        saved = _savings.estimate_savings(kept, [i for i, _ in dropped], cfg.token_costs, measured,
                                          injected=mem.injected if mem else 0)
        mcp_ids = {i.id for i in items if i.kind == "mcp"}
        connectors = sorted(_measure.connector_costs(measured, mcp_ids).items(), key=lambda kv: -kv[1])
        scope = _Scope(context, gsource, gconf, cfg.threshold, kept, dropped,
                       saved, connectors, bool(measured), mem)
        return scope, plan
    _is_prunable = lambda i: i.kind in _savings.PRUNABLE and (scope_skills or i.kind != "skill")
    prunable = [i for i in items if _is_prunable(i)]
    editable = [i for i in prunable if i.id not in pinned_ids]     # pinned always stay; not offered
    def _replan(selected_ids: set[str]):           # rebuild the plan from an edited prunable keep-set
        final = pinned_ids | selected_ids
        nkept = [i for i in items if not _is_prunable(i) or i.id in final]
        ndropped = [(i, "edited") for i in prunable if i.id not in final]
        return _finish(nkept, ndropped)
    scope, plan = _finish(kept, dropped)
    gate = _EditGate(items=items, editable=editable,
                     kept_ids={i.id for i in kept if i.kind in _savings.PRUNABLE},
                     pinned_ids=pinned_ids, replan=_replan, cwd=cwd, cfg=cfg, goal=context)
    return scope, plan, gate

def _cmd_rules(cwd: Path, config_root_override: Path | None = None) -> int:
    if not _interactive([]):                        # no TTY -> nothing to elicit; fail-open
        _warn("rule authoring needs an interactive terminal; nothing to do")
        return 0
    cfg = load_config(cwd=cwd, config_root_override=config_root_override)
    items = claude_code_inventory(cfg.config_root, cwd, cfg.global_config_path)
    rules = load_rules(cfg.config_root, cwd)
    compile_fn = _build_compiler(cfg)
    context, _src, _conf = _resolve_goal(cwd, [])   # goal string for conditional-rule evaluation
    pending = [i for i in items if not has_rule(i, rules)]
    if not pending:
        print(f"{_paint('claude-loadout:', 'green')} every tool already has a rule")
        return 0
    _rules_intro(compile_fn, "to every repo in this profile")
    save = lambda r: save_rule(cfg.config_root, r)   # profile-scoped: the deliberate global path
    authored = 0
    for item in pending:
        if _elicit(item, context, compile_fn, save) != "undecided":
            authored += 1
    print()
    print(f"{_paint('claude-loadout:', 'green')} authored {_plural(authored, 'rule')}")
    cache = _measure.load_costs(cfg.config_root)
    b = _savings.budget(items, cfg.token_costs, cache)
    print(f"{_paint('claude-loadout:', 'green')} up to ~{_savings.human_tokens(b.eager)} tokens trimmed up "
          "front per session (skill + plugin context) — run `claude-loadout --explain` for this session")
    conns = _measure.connector_costs(cache, {i.id for i in items if i.kind == "mcp"})
    on_demand = b.deferred + sum(conns.values())
    if on_demand:
        print(f"{_paint('claude-loadout:', 'green')} plus ~{_savings.human_tokens(on_demand)} tokens of "
              "MCP/connector schemas that load on demand — avoided only if those tools are used")
    return 0

def _discover_profiles(active_root: Path) -> list[Path]:
    # Claude Code keeps each profile in its own dir (~/.claude, ~/.claude-perso, ...);
    # CLAUDE_CONFIG_DIR selects one. Report every sibling so a wrong-profile setup is visible.
    profiles = [active_root]
    for path in sorted(Path.home().glob(".claude*")):
        if path.is_dir() and path.resolve() != active_root.resolve() and path not in profiles:
            profiles.append(path)
    return profiles

def _explicit_profile(override: Path | None) -> Path | None:
    # A prompted profile must reach the launched claude via CLAUDE_CONFIG_DIR — except the
    # ~/.claude default, which resolves .claude.json from the HOME root and so stays env-less.
    return override if override and override != Path.home() / ".claude" else None

def _launch_env(override: Path | None) -> dict | None:
    prof = _explicit_profile(override)                  # None -> inherit parent env unchanged
    return {**os.environ, "CLAUDE_CONFIG_DIR": str(prof)} if prof else None

def _resolve_config_root(environ, passthrough: list[str]) -> Path | None:
    # None -> resolve profile from env as usual. A Path -> user picked that profile.
    # Only prompt when the profile is implicit (env unset), a real choice exists, and
    # we have a TTY; an explicit CLAUDE_CONFIG_DIR is always honored without asking.
    if environ.get("CLAUDE_CONFIG_DIR"):
        return None
    if not _interactive(passthrough):
        return None
    profiles = _discover_profiles(Path.home() / ".claude")
    if len(profiles) <= 1:
        return None
    print("", file=sys.stderr)
    print(f"{_paint('claude-loadout:', 'yellow', err=True)} CLAUDE_CONFIG_DIR not set — "
          "pick a Claude profile:", file=sys.stderr)
    print("", file=sys.stderr)
    for idx, prof in enumerate(profiles, 1):
        has_cfg = (prof / "loadout" / "config.toml").is_file()
        num = _paint(f"{idx})", "bold", err=True)
        print(f"  {num} {prof}  {_paint(f'(claude-loadout config: {_yn(has_cfg, err=True)})', 'dim', err=True)}",
              file=sys.stderr)
    print("", file=sys.stderr)
    while True:                                          # no default (spec B): Enter re-asks
        try:
            raw = input(f"profile [1-{len(profiles)}]: ").strip()
        except EOFError:                                 # Ctrl-D / exhausted stdin -> abort
            raise _Abort
        if not raw:
            continue
        if raw.isdigit() and 1 <= int(raw) <= len(profiles):
            return profiles[int(raw) - 1]
        _warn(f"invalid choice {raw!r}")

_CONFIG_HEADER = (
    "# generated by loadout init — local, gitignored; layers over your profile config\n"
    "# (~/.claude*/loadout/config.toml). Edit or extend freely.\n")

# init writes machine-derived config it treats as local; ignore the whole dir so
# nothing lands in git. Hand-authored config (via `claude-loadout rules`) stays shareable.
_LOCAL_GITIGNORE = "# generated by loadout init — local machine config, do not commit\n*\n"

def _discover_projects(root: Path) -> list[Path]:
    # A project is any direct subdirectory of ROOT; skip dotfile dirs (.git, .venv, …).
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))

def _parse_selection(raw: str, n: int) -> set[int] | None:
    # "all" -> everything; else comma/space separated 1-based indices and a-b ranges.
    raw = raw.strip().lower()
    if raw == "all":
        return set(range(1, n + 1))
    picks: set[int] = set()
    for tok in raw.replace(",", " ").split():
        if "-" in tok[1:]:                              # a-b range (not a leading minus)
            a, _, b = tok.partition("-")
            if not (a.isdigit() and b.isdigit()):
                return None
            lo, hi = int(a), int(b)
            if not (1 <= lo <= hi <= n):
                return None
            picks.update(range(lo, hi + 1))
        elif tok.isdigit() and 1 <= int(tok) <= n:
            picks.add(int(tok))
        else:
            return None
    return picks or None

_CHECK_HINT = "↑/↓ move · space toggle · a all/none · enter confirm · q cancel"

def _read_key():                                    # one keypress -> normalized token
    import termios, tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":                            # CSI escape -> arrow keys
            seq = sys.stdin.read(2)
            return {"[A": "up", "[B": "down"}.get(seq, "esc")
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

def _render_checklist(title, labels, selected, cursor, out, redraw_lines, hint=_CHECK_HINT,
                      headers=None):
    if redraw_lines:                                # rewind over the previous frame
        out.write(f"\033[{redraw_lines}F")
    lines = [*title.split("\n"), ""]                # title may carry a legend line under it
    for i, lab in enumerate(labels):
        if headers and i in headers:                # group header before the first item of a kind
            lines.append(_paint(f" {headers[i]}", "dim", err=True))
        box = "[x]" if selected[i] else "[ ]"
        pointer = _paint(">", "cyan", "bold", err=True) if i == cursor else " "
        lines.append(f" {pointer} {box} {lab}")
    lines.append("")
    lines.append(_paint(f"  {hint}", "dim", err=True))
    out.write("".join(f"\033[K{l}\n" for l in lines))
    out.flush()
    return len(lines)

def _checkbox_select(title, labels, read_key=None, out=sys.stderr,
                     preset=None, allow_empty=False, hint=_CHECK_HINT,
                     headers=None) -> list[int] | None:
    # Interactive multi-select; returns chosen 0-based indices, or None to cancel.
    # Logic is driven by read_key() so tests can feed a key sequence without a real tty.
    # preset seeds the initial ticks (default: all on); allow_empty lets Enter confirm an
    # empty pick (keep-nothing is a valid keep/drop outcome) instead of reading it as cancel.
    # headers: {item_index -> label} draws a dim group header before that item (render-only —
    # cursor and selection index over items, never headers).
    read_key = read_key or _read_key
    n = len(labels)
    selected = list(preset) if preset is not None else [True] * n
    cursor = 0
    drawn = _render_checklist(title, labels, selected, cursor, out, 0, hint, headers)
    while True:
        key = read_key()
        if key in ("up", "k"):
            cursor = (cursor - 1) % n
        elif key in ("down", "j"):
            cursor = (cursor + 1) % n
        elif key == " ":
            selected[cursor] = not selected[cursor]
        elif key in ("a", "A"):
            fill = not all(selected)                 # all on -> clear; otherwise select all
            selected = [fill] * n
        elif key in ("\r", "\n"):
            chosen = [i for i, s in enumerate(selected) if s]
            return chosen if allow_empty else (chosen or None)
        elif key == "\x03":                              # Ctrl-C -> exit 130 like every other prompt
            raise KeyboardInterrupt
        elif key in ("q", "Q", "esc", "\x04"):           # q / Esc / Ctrl-D -> quiet cancel
            return None
        else:
            continue                                # ignore unmapped keys without redrawing
        drawn = _render_checklist(title, labels, selected, cursor, out, drawn, hint, headers)

def _can_raw() -> bool:
    # True only when stdin is a real terminal we can put into raw mode; the picker
    # renders before its first keystroke, so probe up front to avoid a stray frame.
    try:
        import termios
        termios.tcgetattr(sys.stdin.fileno())
        return sys.stdin.isatty()
    except Exception:
        return False

def _select_repos(eligible: list[Path], already: int,
                  label: str = "claude-loadout init:", action: str = "seed") -> list[Path] | None:
    import shutil
    rows = shutil.get_terminal_size((80, 24)).lines
    if not _can_raw() or len(eligible) + 4 > rows:  # no raw tty / frame taller than the window
        return _select_repos_line(eligible, already, label, action)
    head = _plural(len(eligible), "project") + f" eligible to {action}"
    if already:
        head += f" ({already} already configured, skipped)"
    title = f"{_paint(label, 'yellow', err=True)} {head}"
    picks = _checkbox_select(title, [str(p) for p in eligible])
    return [eligible[i] for i in picks] if picks else None

def _select_repos_line(eligible: list[Path], already: int,
                       label: str = "claude-loadout init:", action: str = "seed") -> list[Path] | None:
    print("", file=sys.stderr)
    head = _plural(len(eligible), "project") + " eligible"
    if already:
        head += f" ({already} already configured, skipped)"
    print(f"{_paint(label, 'yellow', err=True)} {head}", file=sys.stderr)
    print("", file=sys.stderr)
    for idx, repo in enumerate(eligible, 1):
        print(f"  {_paint(f'{idx})', 'bold', err=True)} {repo}", file=sys.stderr)
    print("", file=sys.stderr)
    while True:
        raw = _ask(f"select projects to {action} [1-{len(eligible)}, ranges, 'all'; enter to cancel]: ")
        if not raw.strip():
            return None
        picks = _parse_selection(raw, len(eligible))
        if picks is not None:
            return [eligible[i - 1] for i in sorted(picks)]
        _warn(f"invalid selection {raw!r}")

def _pick_profile(profiles: list[Path], default: Path, repo: Path,
                  label: str = "claude-loadout init:") -> Path:
    print("", file=sys.stderr)
    print(f"{_paint(label, 'yellow', err=True)} profile for "
          f"{_paint(str(repo), 'cyan', err=True)}", file=sys.stderr)
    for idx, prof in enumerate(profiles, 1):
        mark = _paint(" (default)", "dim", err=True) if prof == default else ""
        print(f"  {_paint(f'{idx})', 'bold', err=True)} {prof}{mark}", file=sys.stderr)
    while True:
        raw = _ask(f"profile [1-{len(profiles)}, enter for default]: ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(profiles):
            return profiles[int(raw) - 1]
        _warn(f"invalid choice {raw!r}")

def _confirm_goal(repo: Path, use_cache: bool = True, label: str = "claude-loadout init:") -> str | None:
    # Returns the accepted/overridden goal, or None to skip this repo. update re-infers fresh.
    g = detect_goal(repo, use_cache=use_cache)
    print("", file=sys.stderr)
    print(f"{_paint(label, 'yellow', err=True)} {_paint(str(repo), 'cyan', err=True)}", file=sys.stderr)
    print(f"  {_paint('goal', 'dim', err=True)}   {g.goal}", file=sys.stderr)
    print(f"         {_paint(f'from {g.source} · confidence {g.confidence:.2f}', 'dim', err=True)}",
          file=sys.stderr)
    raw = _ask(f"  {_paint('enter', 'bold', err=True)} accept · type to override · "
               f"{_paint('s', 'bold', err=True)} skip repo: ").strip()
    if raw.lower() == "s":
        return None
    return raw or g.goal

def _decide_keep(items, cfg, context: str, rules: list[Rule]) -> set[str]:
    # Mirror _scoped_plan's non-interactive core: pinned + rules + ranked kept set.
    pinned = [i for i in items if any(fnmatch(i.id, g) for g in cfg.always_keep)]
    pinned_ids = {i.id for i in pinned}
    outcome = apply_rules([i for i in items if i.id not in pinned_ids], rules, context)
    embed = _build_embed(cfg.model_name)
    ranked = Ranker(embed=embed).rank(context, list(outcome.undecided), cfg.threshold, cfg.always_keep)
    return pinned_ids | {i.id for i in outcome.forced_keep} | {i.id for i in ranked.kept}

# Machine-materialized rules carry this nl prefix so `update` can tell them from rules a human
# authored (via `claude-loadout rules` or by hand) and regenerate only the machine ones.
_SEED_NL_PREFIX = "seeded by loadout"   # on-disk marker in rules.toml (read back by is_seed_rule); keep stable for compat

def _is_seeded_rule(rule: Rule) -> bool:
    return rule.nl.startswith(_SEED_NL_PREFIX)

def _materialize_rules(items, kept_ids: set[str], goal: str, verb: str = "init") -> list[Rule]:
    # Freeze keep/drop for every kind compose can prune (mcp, plugin, and skills via skillOverrides).
    nl = f"{_SEED_NL_PREFIX} {verb} (goal: {goal})"
    rules = []
    for i in items:
        if i.kind not in _savings.PRUNABLE:
            continue
        action = "always_keep" if i.id in kept_ids else "always_drop"
        rules.append(Rule(target=i.id, nl=nl, predicate=Predicate(action, (), "any")))
    return rules

def _prunable(items) -> list[Item]:
    return [i for i in items if i.kind in _savings.PRUNABLE]

def _pinned_ids(items, cfg) -> set[str]:
    # Tools the config's always_keep pins: they win over rules at launch (spec §12), so the
    # keep/drop review must not offer them — a rule that dropped one would be ignored anyway.
    return {i.id for i in items if any(fnmatch(i.id, g) for g in cfg.always_keep)}

def _plan_repo(repo: Path, profile: Path, goal: str):
    # Load the profile, inventory the repo, and auto-decide keep/drop (no side effects).
    cfg = load_config(cwd=repo, config_root_override=profile)
    items = claude_code_inventory(cfg.config_root, repo, cfg.global_config_path)
    kept_ids = _decide_keep(items, cfg, goal, load_rules(cfg.config_root, repo))
    return cfg, items, kept_ids

_KIND_ORDER = {"mcp": 0, "plugin": 1, "skill": 2}
_KIND_HEADER = {"mcp": "mcp servers", "plugin": "plugins", "skill": "skills"}

def _display_name(item) -> str:
    return item.id.split("@", 1)[0]                 # drop the @marketplace suffix for readability

def _short_desc(text: str, width: int) -> str:
    s = " ".join((text or "").split())              # collapse newlines/runs of whitespace
    if width <= 0 or not s:
        return ""
    return s if len(s) <= width else s[: width - 1].rstrip() + "…"

def _review_keep_drop(repo: Path, prunable: list[Item], kept_ids: set[str],
                      label: str = "claude-loadout init:"):
    # Let the user adjust the auto keep/drop before it is frozen. Rows are grouped by kind with a
    # dim one-line description; a legend spells out what a ticked/unticked box means. Returns the
    # kept-id set, or None to skip the repo. Falls back to the auto decision when a raw tty isn't
    # available or the frame is taller than the window.
    import shutil
    if not prunable:
        return set(kept_ids)
    size = shutil.get_terminal_size((80, 24))
    ordered = sorted(prunable, key=lambda i: (_KIND_ORDER.get(i.kind, 9), _display_name(i).lower()))
    headers, seen = {}, set()
    for idx, i in enumerate(ordered):
        if i.kind not in seen:
            headers[idx] = _KIND_HEADER.get(i.kind, i.kind); seen.add(i.kind)
    frame = len(ordered) + len(headers) + 5         # title + legend + blank + rows + headers + blank + hint
    if not _can_raw() or frame > size.lines:
        return set(kept_ids)                        # can't draw the picker; accept auto silently
    id_w = min(max(len(_display_name(i)) for i in ordered), 30)
    desc_w = size.columns - id_w - 12               # room left after the box, name column, and gaps
    labels = []
    for i in ordered:
        desc = _short_desc(i.description, desc_w)
        if desc == i.id or desc in (">", "|", ">-", "|-", ">+", "|+"):
            desc = ""                               # useless: plugin id-fallback / bare YAML block scalar
        tail = f"  {_paint(desc, 'dim', err=True)}" if desc else ""
        labels.append(f"{_display_name(i):<{id_w}}{tail}")
    preset = [i.id in kept_ids for i in ordered]
    count = _paint(f"({_plural(len(ordered), 'tool')})", "dim", err=True)
    title = (f"{_paint(label, 'yellow', err=True)} keep/drop · "
             f"{_paint(repo.name, 'cyan', err=True)}  {count}\n"
             f"  {_paint('[x] keep — loads here', 'green', err=True)}   ·   "
             f"{_paint('[ ] drop — pruned this session', 'dim', err=True)}")
    hint = "↑/↓ move · space keep/drop · a all/none · enter save · q skip repo"
    picks = _checkbox_select(title, labels, preset=preset, allow_empty=True, hint=hint, headers=headers)
    if picks is None:                               # q / Esc -> skip this repo entirely
        return None
    return {ordered[i].id for i in picks}

_REPO_RULES_HEADER = "# generated by loadout — local, gitignored keep/drop decisions\n"

def _write_seed_scaffold(repo: Path, cfg, goal: str) -> None:
    # Local seed metadata, no rules: .gitignore + goal cache + config.toml.
    d = repo / ".loadout"; d.mkdir(exist_ok=True)
    (d / ".gitignore").write_text(_LOCAL_GITIGNORE)   # before write_goal_cache, which only writes if absent
    write_goal_cache(repo, goal)
    model = cfg.model_name.replace("\\", "\\\\").replace('"', '\\"')   # TOML basic string
    (d / "config.toml").write_text(
        f'{_CONFIG_HEADER}threshold = {cfg.threshold}\nmodel_name = "{model}"\n')

def _write_seed(repo: Path, cfg, goal: str, rules: list[Rule]) -> None:
    # Persist the local seed: scaffold + a fresh rules.toml holding `rules`.
    _write_seed_scaffold(repo, cfg, goal)
    write_rules(repo / ".loadout" / "rules.toml", rules, header=_REPO_RULES_HEADER)

def _kept_dropped(prunable: list[Item], kept_ids: set[str]) -> tuple[list[Item], list[Item]]:
    return ([i for i in prunable if i.id in kept_ids],
            [i for i in prunable if i.id not in kept_ids])

def _seed_repo(repo: Path, cfg, items, kept_ids: set[str], goal: str) -> tuple[list[Item], list[Item]]:
    # init path: freeze every prunable tool's keep/drop as a machine rule.
    _write_seed(repo, cfg, goal, _materialize_rules(items, kept_ids, goal, verb="init"))
    return _kept_dropped(_prunable(items), kept_ids)

def _cmd_init(cwd: Path, args: list[str], environ) -> int:
    yes = "--yes" in args
    root_args = [a for a in args if a != "--yes"]
    configured = lambda r: ((r / ".loadout" / "config.toml").is_file()
                            or (r / ".loadout" / "rules.toml").is_file())
    if root_args:                                    # bulk: every unconfigured project under ROOT
        root = Path(root_args[0]).expanduser()
        if not root.is_dir():
            _warn(f"{root} is not a directory; nothing to do")
            return 0
        projects = _discover_projects(root)
        eligible = [r for r in projects if not configured(r)]   # either config file -> skip whole project
        already_list = [r for r in projects if configured(r)]   # named in the report, never re-seeded
    else:                                            # single: seed the current repo itself
        eligible = [] if configured(cwd) else [cwd]
        already_list = [cwd] if configured(cwd) else []
    already = len(already_list)
    if not eligible:
        print(f"{_paint('claude-loadout:', 'green')} nothing to seed"
              f"{f' ({already} already configured — run `claude-loadout update` to refresh)' if already else ' (no project folders found)'}")
        for repo in already_list:
            print(f"  {_paint('–', 'dim')} {repo} {_paint('(already configured)', 'dim')}")
        return 0
    if not sys.stdin.isatty() and not yes:
        _warn("init needs an interactive terminal (or pass --yes); nothing to do")
        return 0
    if root_args and not yes:
        selected = _select_repos(eligible, already)   # bulk picker; empty pick / Ctrl-D -> None
        if not selected:
            _warn("no repos selected; nothing to do")
            return 0
    else:                                            # single repo, or --yes: no picker
        selected = eligible
    active = load_config(cwd=cwd).config_root
    profiles = _discover_profiles(active)
    sticky = active
    seeded = 0
    chosen = set(selected)                          # eligible projects the user left out of the run
    skipped: list[tuple[Path, str]] = [(r, "not selected") for r in eligible if r not in chosen]
    seeded_repos: list[Path] = []
    for repo in selected:
        profile = sticky
        if not yes and len(profiles) > 1:
            profile = sticky = _pick_profile(profiles, sticky, repo)
        if yes:
            goal = detect_goal(repo).goal
        else:
            goal = _confirm_goal(repo)
            if goal is None:
                skipped.append((repo, "skipped at goal prompt"))
                continue
        cfg, items, auto_kept = _plan_repo(repo, profile, goal)
        prunable = _prunable(items)
        locked = _pinned_ids(prunable, cfg)         # config always_keep wins at launch — not reviewable
        if yes:
            kept_ids = auto_kept
        else:
            picked = _review_keep_drop(repo, [i for i in prunable if i.id not in locked], auto_kept)
            if picked is None:                      # q / Esc in the keep/drop picker
                skipped.append((repo, "skipped at keep/drop review"))
                continue
            kept_ids = set(picked) | locked         # pinned tools always survive
        kept, dropped = _seed_repo(repo, cfg, items, kept_ids, goal)
        seeded += 1
        seeded_repos.append(repo)
        _report_repo(repo, kept, dropped)
    _offer_memory(seeded_repos, yes)
    _print_init_summary(seeded, skipped, already_list)
    return 0

def _offer_memory(repos: list[Path], yes: bool) -> None:
    # Asked once for the whole run, not per repo, and only where something was actually seeded.
    # Default is no: recall spends context, and a user who has not asked for it should not pay.
    if not repos or yes or not _interactive([]):
        return
    print()
    print(_paint("  memory recall", "bold"))
    print(_paint("    Puts the notes relevant to a session into its context, inside a token "
                 "budget.", "dim"))
    print(_paint("    Off unless you say otherwise; reversible with "
                 "`claude-loadout memory disable`.", "dim"))
    if not _ask(f"    enable it for {_plural(len(repos), 'repo')}? [y/N] ").strip().lower()\
            .startswith("y"):
        print(_paint("    left off — turn it on later with `claude-loadout memory enable`", "dim"))
        return
    for repo in repos:
        set_memory_enabled(repo, True)
    print(f"    {_paint('enabled', 'green')} for {_plural(len(repos), 'repo')}")

def _report_repo(repo: Path, kept: list[Item], dropped: list[Item]) -> None:
    # Verbose per-repo receipt: the count line, then the full kept/dropped id lists.
    print(f"  {_paint('✓', 'green')} {repo}  "
          f"{_paint(f'({len(kept)} kept / {len(dropped)} dropped)', 'dim')}")
    for label, items, mark, color in (("kept", kept, "✓", "green"),
                                       ("dropped", dropped, "✗", "red")):
        for i in items:
            print(f"      {_paint(mark, color)} {i.id} {_paint(f'({i.kind})', 'dim')}")
    if not kept and not dropped:
        print(f"      {_paint('(no prunable tools)', 'dim')}")

def _print_init_summary(seeded: int, skipped: list[tuple[Path, str]],
                        already_list: list[Path]) -> None:
    print()
    tail = f"seeded {_plural(seeded, 'project')}"
    if skipped:
        tail += f", {_plural(len(skipped), 'project')} skipped"
    if already_list:
        tail += f", {len(already_list)} already configured"
    print(f"{_paint('claude-loadout:', 'green')} {tail}")
    notes = skipped + [(r, "already configured") for r in already_list]
    for repo, reason in notes:                       # name every project that didn't get seeded
        print(f"  {_paint('–', 'dim')} {repo} {_paint(f'({reason})', 'dim')}")

_UPDATE_LABEL = "claude-loadout update:"

def _is_seeded(repo: Path) -> bool:
    # init always writes config.toml under a local gitignore; its presence marks a repo that
    # update owns. A repo carrying only a hand-committed rules.toml (no config) is left alone.
    return (repo / ".loadout" / "config.toml").is_file()

def _split_repo_rules(repo: Path) -> tuple[list[Rule], list[Rule]]:
    # (human, machine) split of the repo's local rules.toml by the seed nl marker.
    rr = read_rules(repo / ".loadout" / "rules.toml")
    return ([r for r in rr if not _is_seeded_rule(r)],
            [r for r in rr if _is_seeded_rule(r)])

def _decision_rules(cfg, repo_human: list[Rule]) -> list[Rule]:
    # Rules that decide keep/drop during an update: profile rules + the repo's human rules
    # (repo overrides profile per target). The old machine rules are deliberately excluded so
    # ranking gets a fresh say on every tool the human hasn't pinned.
    merged = {r.target: r for r in read_rules(profile_rules_file(cfg.config_root))}
    merged.update({r.target: r for r in repo_human})
    return list(merged.values())

def _reconcile_rules(prunable: list[Item], final_kept: set[str],
                     decision: list[Rule], repo_human: list[Rule], goal: str) -> list[Rule]:
    # Preserve human rules that still yield the chosen decision; write a machine rule for every
    # other prunable tool. A human exact-id rule the user flipped is dropped so the machine wins.
    nl = f"{_SEED_NL_PREFIX} update (goal: {goal})"
    machine, overridden = [], set()
    for i in prunable:
        want = "keep" if i.id in final_kept else "drop"
        gov = rules_for(i, decision)
        if gov and evaluate(gov[0].predicate, goal) == want:
            continue                                 # an existing rule already yields it; leave as-is
        if any(r.target == i.id for r in repo_human):
            overridden.add(i.id)                     # user flipped a human exact rule -> replace it
        action = "always_keep" if want == "keep" else "always_drop"
        machine.append(Rule(target=i.id, nl=nl, predicate=Predicate(action, (), "any")))
    preserved = [r for r in repo_human if r.target not in overridden]
    return preserved + machine

def _update_repo(repo: Path, profile: Path, goal: str, yes: bool):
    # Refresh one seeded repo. Returns (kept, dropped) prunable item lists, or None if skipped.
    cfg = load_config(cwd=repo, config_root_override=profile)
    items = claude_code_inventory(cfg.config_root, repo, cfg.global_config_path)
    repo_human, _machine = _split_repo_rules(repo)
    decision = _decision_rules(cfg, repo_human)
    baseline = _decide_keep(items, cfg, goal, decision)   # human rules honored, machine rules re-ranked
    prunable = _prunable(items)
    locked = _pinned_ids(prunable, cfg)               # config always_keep wins at launch — not reviewable
    if yes:
        final = baseline
    else:
        picked = _review_keep_drop(repo, [i for i in prunable if i.id not in locked],
                                   baseline, label=_UPDATE_LABEL)
        if picked is None:
            return None
        final = set(picked) | locked
    _write_seed(repo, cfg, goal, _reconcile_rules(prunable, final, decision, repo_human, goal))
    return _kept_dropped(prunable, final)

def _print_update_summary(updated: int, skipped: list[tuple[Path, str]],
                          unseeded: list[Path]) -> None:
    print()
    tail = f"updated {_plural(updated, 'project')}"
    if skipped:
        tail += f", {_plural(len(skipped), 'project')} skipped"
    if unseeded:
        tail += f", {len(unseeded)} not seeded"
    print(f"{_paint('claude-loadout:', 'green')} {tail}")
    notes = skipped + [(r, "not seeded — run init") for r in unseeded]
    for repo, reason in notes:
        print(f"  {_paint('–', 'dim')} {repo} {_paint(f'({reason})', 'dim')}")

def _cmd_update(cwd: Path, args: list[str], environ) -> int:
    yes = "--yes" in args
    root_args = [a for a in args if a != "--yes"]
    unseeded: list[Path] = []
    if root_args:                                    # bulk: every seeded project under ROOT
        root = Path(root_args[0]).expanduser()
        if not root.is_dir():
            _warn(f"{root} is not a directory; nothing to do")
            return 0
        projects = _discover_projects(root)
        eligible = [r for r in projects if _is_seeded(r)]
        unseeded = [r for r in projects if not _is_seeded(r)]
        if not eligible:
            print(f"{_paint('claude-loadout:', 'green')} nothing to update"
                  f"{f' ({len(unseeded)} not seeded — run init)' if unseeded else ' (no seeded projects found)'}")
            return 0
    else:                                            # single: the current repo
        if not _is_seeded(cwd):
            _warn("this repo isn't claude-loadout-seeded; run `claude-loadout init` first")
            return 0
        eligible = [cwd]
    if not sys.stdin.isatty() and not yes:
        _warn("update needs an interactive terminal (or pass --yes); nothing to do")
        return 0
    if root_args and not yes:
        selected = _select_repos(eligible, 0, label=_UPDATE_LABEL, action="update")
        if not selected:
            _warn("no repos selected; nothing to do")
            return 0
    else:
        selected = eligible
    active = load_config(cwd=cwd).config_root
    profiles = _discover_profiles(active)
    sticky = active
    updated = 0
    chosen = set(selected)
    skipped: list[tuple[Path, str]] = [(r, "not selected") for r in eligible if r not in chosen]
    seeded_repos: list[Path] = []
    for repo in selected:
        profile = sticky
        if not yes and len(profiles) > 1:
            profile = sticky = _pick_profile(profiles, sticky, repo, label=_UPDATE_LABEL)
        if yes:
            goal = detect_goal(repo, use_cache=False).goal
        else:
            goal = _confirm_goal(repo, use_cache=False, label=_UPDATE_LABEL)
            if goal is None:
                skipped.append((repo, "skipped at goal prompt"))
                continue
        result = _update_repo(repo, profile, goal, yes)
        if result is None:
            skipped.append((repo, "skipped at keep/drop review"))
            continue
        kept, dropped = result
        updated += 1
        _report_repo(repo, kept, dropped)
    _print_update_summary(updated, skipped, unseeded)
    return 0

def _profile_report(root: Path, cwd: Path, active: bool,
                    global_config_path: Path | None = None,
                    token_costs: dict[str, int] | None = None) -> None:
    tag = f" {_paint('(active)', 'green', 'bold')}" if active else ""
    print(f"  {_paint(str(root), 'cyan')}{tag}")
    user_cfg = root / "loadout" / "config.toml"
    print(f"    user config:  {_yn(user_cfg.is_file())}")
    try:
        items = claude_code_inventory(root, cwd, global_config_path)
    except Exception as exc:                        # fail-open: doctor must never crash
        print(f"    inventory:    {_paint(f'unavailable ({exc})', 'red')}")
        return
    counts = {k: sum(1 for i in items if i.kind == k) for k in ("mcp", "plugin", "skill")}
    print(f"    inventory:    {counts['mcp']} mcp, {_plural(counts['plugin'], 'plugin')}, "
          f"{_plural(counts['skill'], 'skill')}")
    cache = _measure.load_costs(root)
    b = _savings.budget(items, token_costs, cache)
    print(f"    up front:     up to ~{_savings.human_tokens(b.eager)} tokens "
          f"{_paint('(skill + plugin context; actual depends on the goal)', 'dim')}")
    conns = _measure.connector_costs(cache, {i.id for i in items if i.kind == "mcp"})
    on_demand = b.deferred + sum(conns.values())
    if on_demand:
        detail = f"{_plural(len(conns), 'connector')} + MCP schemas" if conns else "MCP schemas"
        print(f"    on-demand:    ~{_savings.human_tokens(on_demand)} tokens, {detail} "
              f"{_paint('(load lazily; avoided only if used)', 'dim')}")

def _memory_report(cfg, cwd: Path) -> None:
    print(_paint("  memory", "bold"))
    if not cfg.memory.enabled:
        print(_paint("    off — enable with [memory] enabled = true in loadout/config.toml", "dim"))
        print()
        return
    store = read_store(cwd, cfg.config_root)
    kinds = {k: sum(1 for e in store.entries if e.kind == k)
             for k in sorted({e.kind for e in store.entries})}
    print(f"    entries:          {len(store.entries)}"
          + (f" ({', '.join(f'{n} {k}' for k, n in kinds.items())})" if kinds else ""))
    open_debt = [e for e in store.entries if e.kind == "debt" and e.status != "resolved"]
    if open_debt:
        print(f"    open debt:        {_paint(str(len(open_debt)), 'yellow')} "
              + _paint("— claude-loadout debt list", "dim"))
    states = [anchor_state(e, cwd) for e in store.entries]
    if states.count("missing") or states.count("changed"):
        print(f"    stale anchors:    {_paint(str(states.count('missing')), 'yellow')} gone, "
              f"{states.count('changed')} changed")
    for kept, dropped in store.shadowed:            # same slug in two stores: one is invisible
        print(f"    {_paint('shadowed', 'yellow')}  {dropped.path} "
              + _paint(f"(hidden by {kept.path})", "dim"))
    usage = load_usage(cfg.config_root, cwd)
    if usage:
        earning = sum(1 for rec in usage.values() if rec.uses >= cfg.memory.promote_after)
        print(f"    delivered:        {len(usage)} entries, {earning} promoted "
              + _paint(f"(>= {cfg.memory.promote_after} deliveries)", "dim"))
    pending = load_candidates(cfg.config_root, cwd)
    if pending:
        print(f"    candidates:       {len(pending)} "
              + _paint("— claude-loadout memory consolidate", "dim"))
    print()

def _cmd_doctor(cwd: Path) -> int:
    cfg = load_config(cwd=cwd)
    print(_paint("claude-loadout doctor", "bold"))
    print()
    profiles = _discover_profiles(cfg.config_root)
    print(_paint(f"  claude profiles: {_plural(len(profiles), 'profile')}", "bold"))
    print()
    for root in profiles:
        active = root == cfg.config_root
        _profile_report(root, cwd, active,
                        cfg.global_config_path if active else None, cfg.token_costs)
        print()
    _memory_report(cfg, cwd)
    repo_cfg = cwd / ".loadout" / "config.toml"
    print(_paint("  environment", "bold"))
    print(f"    repo config:      {_paint(str(repo_cfg), 'cyan')} ({_yn(repo_cfg.is_file())})")
    resolved = resolve_model_source(cfg.model_name)
    embed = _build_embed(cfg.model_name)           # warns + falls back on failure
    model_state = ("keyword fallback" if embed is keyword_embed
                   else "bundled copy" if resolved == str(bundled_model_path()) else "external")
    print(f"    embedding model:  {cfg.model_name} {_paint(f'({model_state})', 'dim')}")
    rule_state = cfg.rule_model_path or _paint(
        "not configured — rule authoring uses keep/drop/skip prompts", "dim")
    print(f"    rule model:       {rule_state}")
    print()
    print(_paint("  Next steps", "bold"))
    print("    1. Point your launch command at claude-loadout (short alias: cld),")
    print("       e.g. add to your shell rc:")
    alias_plain = _paint('alias claude="claude-loadout"', 'cyan')  # kept out of the f-string: nested
    print(f"         {alias_plain}")                                # double quotes break f-strings on 3.11
    print("       or wrap a separate profile:")
    alias_profile = _paint('alias claude-work="CLAUDE_CONFIG_DIR=~/.claude-work claude-loadout"', 'cyan')
    print(f"         {alias_profile}")
    print("    2. Preview what a session would load, without launching anything:")
    print(f"         {_paint('claude-loadout --explain', 'cyan')}")
    print("    3. Scope tools with plain-language rules:")
    print(f"         {_paint('claude-loadout rules', 'cyan')}")
    steps = _memory_next_steps(_memory_state(cwd, cfg), cfg.memory.enabled)
    if steps:
        command, why = steps[0]
        print(f"    4. {why[0].upper()}{why[1:]}:")
        print(f"         {_paint(command, 'cyan')}")
    return 0

def _cmd_measure(cwd: Path) -> int:
    cfg = load_config(cwd=cwd)
    print(_paint("claude-loadout measure", "bold"))
    print(_paint("  connecting to each MCP server to tokenize its real tool set …", "dim"))
    print()
    try:
        servers = _measure.discover()
    except _measure.MeasureError as exc:            # fail-open: never crash a diagnostic
        _warn(str(exc))
        return 0
    if not servers:
        print("  no MCP servers found")
        return 0
    width = max(len(s.id) for s in servers)
    n_total = len(servers)
    live = sys.stdout.isatty()                          # transient progress only on a real terminal
    results, total = [], 0
    for i, s in enumerate(servers, 1):
        if live:                                        # overwrite-in-place status while we connect
            sys.stdout.write(f"\r  [{i}/{n_total}] connecting {s.id} …\033[K")
            sys.stdout.flush()
        r = _measure.measure_server(s)
        results.append(r)
        prefix = "\r\033[K" if live else ""             # clear the status line, then print the result
        if r.tokens is None:
            print(f"{prefix}  {s.id:<{width}}  {_paint('unmeasured', 'yellow')} {_paint(f'({r.reason})', 'dim')}")
        else:
            total += r.tokens
            print(f"{prefix}  {s.id:<{width}}  "
                  f"{_paint('~' + _savings.human_tokens(r.tokens) + ' tok', 'green')} "
                  f"{_paint('(measured, ' + r.method + ')', 'dim')}")
    _measure.save_costs(cfg.config_root, results)
    n = sum(1 for r in results if r.tokens is not None)
    print()
    print(f"{_paint('claude-loadout:', 'green')} measured {_plural(n, 'server')}, "
          f"~{_savings.human_tokens(total)} tokens total")
    print(_paint(f"  cached to {_measure.costs_path(cfg.config_root)}", "dim"))
    print(_paint("  these are a diagnostic view; a measured cost feeds savings only for MCP "
                 "servers in your .claude.json/.mcp.json (matched by bare name).", "dim"))
    print(_paint("  claude.ai connectors and plugin-bundled servers are shown here but aren't "
                 "pruned by ccloadout.", "dim"))
    return 0

def _loadout_version() -> str:
    try:
        return version("ccloadout")
    except PackageNotFoundError:                     # running from a source tree, uninstalled
        return "unknown"

def _print_help() -> None:
    cmd = lambda s: _paint(s, "cyan")
    print(
        f"{_paint('claude-loadout', 'bold')} — goal-aware launcher for Claude Code  {_paint('(alias: cld)', 'dim')}\n"
        "\n"
        f"{_paint('Usage:', 'bold')}\n"
        f"  {cmd('cld [claude-args...]')}   Launch claude with a goal-scoped tool set\n"
        f"  {cmd('cld --explain')}          Print the scoping plan, then exit (no launch)\n"
        f"  {cmd('cld recall <query>')}    Search the memory store and print matching entries in full\n"
        f"  {cmd('cld memory add <text>')} Record a note for this repo (--name slug, --anchor path)\n"
        f"  {cmd('cld memory consolidate')}  Review recorded sessions and promote them to memories\n"
        f"  {cmd('cld memory audit')}     Review the store and delete what no longer earns its place\n"
        f"                                (--context '<goal>' to see what it would recall and why,\n"
        f"                                 --all-repos for every project, --json for a session to read)\n"
        f"  {cmd('cld memory flag <name>')}  Mark an entry as wrong (--reason ..., --clear to undo)\n"
        f"  {cmd('cld debt add|list|resolve')}  Track shims, stubs and skipped tests you left behind\n"
        f"  {cmd('cld decision new|list|show|supersede')}  Architectural decisions, versioned per repo\n"
        f"  {cmd('cld rules')}              Author profile-wide keep/drop rules (all repos; launch prompts are repo-local)\n"
        f"  {cmd('cld init [ROOT]')}        Seed local config — this repo, or bulk-seed every project under ROOT\n"
        f"  {cmd('cld update [ROOT]')}      Refresh existing seeds — this repo, or all seeded under ROOT\n"
        f"  {cmd('cld doctor')}             Report profiles, config, and model state\n"
        f"  {cmd('cld measure')}            Measure real MCP tool-token cost — MCP only (they expose tools at runtime; opt-in, connects)\n"
        f"  {cmd('cld --no-gate')}          Launch without the pre-launch review pause (or set LOADOUT_NO_GATE)\n"
        f"  {cmd('cld --no-scope-skills')}  Keep every user skill loaded — skip skill scoping (or set LOADOUT_NO_SCOPE_SKILLS)\n"
        f"  {cmd('cld --help, -h')}         Show this help\n"
        f"  {cmd('cld --version, -V')}      Show the claude-loadout version\n"
        "\n"
        f"{_paint('Any other flags pass straight through to claude — run `claude --help` for those.', 'dim')}"
    )

def _print_explain(scope: _Scope, plan) -> None:
    print(_paint("claude-loadout — scoping plan", "bold"))
    print()
    lbl = lambda s: _paint(f"  {s:<11}", "dim")
    print(f"{lbl('goal')}{scope.goal!r}   "
          f"{_paint(f'({scope.source} · confidence {scope.confidence:.2f})', 'dim')}")
    print(f"{lbl('threshold')}{scope.threshold}")
    print()
    kept_prunable = [i for i in scope.kept if i.kind in _savings.PRUNABLE]
    print(_paint(f"  keeping ({len(kept_prunable)})", "bold"))
    for i in kept_prunable:
        print(f"    {_paint('✓', 'green')} {i.id}")
    if not kept_prunable:
        print(_paint("    (nothing)", "dim"))
    print()
    dropped = scope.dropped
    print(_paint(f"  dropping ({len(dropped)})", "bold"))
    width = max((len(i.id) for i, _ in dropped), default=0)
    for item, s in dropped:
        reason = f"{s:.3f}" if isinstance(s, float) else str(s)
        print(f"    {_paint('✗', 'red')} {item.id:<{width}}  {_paint(reason, 'dim')}")
    if not dropped:
        print(_paint("    (nothing)", "dim"))
    print()
    conn_tok = sum(t for _, t in scope.connectors)
    if scope.connectors:
        print(_paint("  connectors dropped (all-or-nothing, strict mode)", "bold"))
        w = max(len(c) for c, _ in scope.connectors)
        for cid, tok in scope.connectors:
            print(f"    {_paint('✗', 'red')} {cid:<{w}}  {_paint('~' + _savings.human_tokens(tok), 'dim')}")
        print()
    if scope.memory is not None:
        m = scope.memory
        print(_paint("  memory", "bold"))
        if m.total == 0:
            print(_paint("    store is empty — nothing injected", "dim"))
        elif m.shown == 0:
            print(_paint(f"    {_plural(m.total, 'entry')} in store, none injected "
                         "(below min_entries or threshold)", "dim"))
        else:
            print(f"    injected:       {m.shown} of {m.total} entries  "
                  f"{_paint('≈ ' + _savings.human_tokens(m.injected) + ' tokens (heuristic)', 'yellow')}")
        print()
    s = scope.savings
    on_demand = s.deferred + conn_tok
    print(_paint("  savings", "bold"))
    print(f"    pruned:         {s.dropped} of {s.total} tools")
    print(f"    up front:       "
          f"{_paint('≈ ' + _savings.human_tokens(s.eager) + ' tokens', 'green', 'bold')} "
          f"{_paint('— skill + plugin context, gone from turn one', 'dim')}")
    if s.injected:
        net = _savings.human_tokens(abs(s.net))
        sign = "gain" if s.net >= 0 else "cost"
        print(f"    net up front:   "
              f"{_paint(('≈ ' if s.net >= 0 else '≈ -') + net + ' tokens', 'bold')} "
              f"{_paint(f'— after the memory payload ({sign})', 'dim')}")
    if on_demand:
        note = "measured" if scope.connectors else "estimate"
        print(f"    on-demand:      {_paint('≈ ' + _savings.human_tokens(on_demand) + ' tokens', 'dim')} "
              f"{_paint(f'— MCP/connector schemas load lazily; avoided only if used ({note})', 'dim')}")
    elif not scope.measured:
        print(_paint("    on-demand:      run `claude-loadout measure` to quantify the claude.ai "
                     "connectors strict mode blocks", "dim"))
    print()
    print(_paint("  command", "bold"))
    print(f"    {_paint(' '.join(plan.argv), 'dim')}")

def _savings_line(scope: _Scope) -> str:
    s = scope.savings
    on_demand = s.deferred + sum(t for _, t in scope.connectors)
    core = f"scoped out {s.dropped} of {s.total} tools"
    if scope.connectors:
        core += f" + {len(scope.connectors)} connectors"
    parts = [core, f"~{_savings.human_tokens(s.eager)} trimmed up front"]
    if on_demand:                                   # MCP schemas load lazily — cost only if used
        parts.append(f"~{_savings.human_tokens(on_demand)} on-demand avoided")
    return " · ".join(parts)

def _maybe_persist_edit(gate: _EditGate, selected_ids: set) -> None:
    # Offer to freeze the edited keep/drop as a repo seed so future launches respect it.
    ans = _ask("  save these choices to this repo? [y/N] ").strip().lower()
    if ans not in ("y", "yes"):
        return
    _seed_repo(gate.cwd, gate.cfg, gate.items, gate.pinned_ids | selected_ids, gate.goal)
    _warn("saved — this repo is now seeded (`claude-loadout update` refreshes it)")

def _launch_gate(scope: _Scope, plan, gate: _EditGate, passthrough: list[str], no_gate: bool = False):
    # Interactive pre-launch review: read the summary, optionally edit keep/drop, then launch.
    # Returns the (possibly re-composed) (scope, plan) to run. Non-interactive -> pass through.
    if gate is None or not (_interactive(passthrough) and (scope.savings.dropped or scope.connectors)):
        return scope, plan
    _warn(_savings_line(scope))
    seeded = _is_seeded(gate.cwd)
    if not seeded:
        _warn("this repo isn't seeded — run `claude-loadout init` to persist scoping for it")
    if no_gate:                                     # opted out — launch immediately, no pause
        return scope, plan
    if seeded:                                      # settled config — brief readable pause, then launch
        time.sleep(_LAUNCH_PAUSE_S)
        return scope, plan
    while True:
        choice = _ask("  [enter] launch · [e] edit keep/drop · [q] cancel? ").strip().lower()
        if choice == "q":
            raise KeyboardInterrupt                 # clean abort, exit 130
        if choice != "e":
            return scope, plan
        if not gate.editable:
            _warn("nothing to edit — every kept tool is pinned by config")
            return scope, plan
        if not _can_raw():                          # no raw terminal -> can't draw the checkbox
            _warn("can't draw the editor here; launching as scoped")
            return scope, plan
        preset = [i.id in gate.kept_ids for i in gate.editable]
        labels = [f"{i.id}  {_paint('(' + i.kind + ')', 'dim', err=True)}" for i in gate.editable]
        picks = _checkbox_select(_paint("keep/drop for this launch", "yellow", err=True),
                                 labels, preset=preset, allow_empty=True)
        if picks is None:                           # q in the checkbox -> back to the gate prompt
            continue
        selected = {gate.editable[i].id for i in picks}
        _cleanup(plan.tmp_paths)                    # discard the superseded plan's temp files
        scope, plan = gate.replan(selected)
        _maybe_persist_edit(gate, selected)
        _warn(_savings_line(scope))
        return scope, plan

def main(argv: list[str] | None = None) -> int:
    try:
        return _run(argv)
    except KeyboardInterrupt:                       # Ctrl-C at any prompt -> clean abort, no traceback
        _warn("aborted")
        return 130

def _run(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cwd = Path.cwd()
    if argv and argv[0] in ("--help", "-h", "help"):  # loadout's own help; no profile prompt/scoping
        _print_help()
        return 0
    if argv and argv[0] in ("--version", "-V"):
        print(f"claude-loadout {_loadout_version()}")
        return 0
    if argv and argv[0] == "doctor":                # doctor enumerates every profile itself
        return _cmd_doctor(cwd)
    if argv and argv[0] == "decision":              # absorbed from the debug-decisions skill
        return _cmd_decision(cwd, argv[1:], _resolve_config_root(os.environ, []))
    if argv and argv[0] == "debt":                  # the debt ledger: explicit lifecycle only
        return _cmd_debt(cwd, argv[1:], _resolve_config_root(os.environ, []))
    if argv and argv[0] == "memory":
        sub = argv[1] if len(argv) > 1 else ""
        if sub == "add":
            return _cmd_write_entry(cwd, argv[2:], "memory", _resolve_config_root(os.environ, []))
        if sub == "consolidate":
            return _cmd_consolidate(cwd, _resolve_config_root(os.environ, []))
        if not sub:                                 # a bare `memory` is the entry point, not an error
            return _print_memory_status(cwd, load_config(
                cwd=cwd, config_root_override=_resolve_config_root(os.environ, [])))
        if sub in ("enable", "disable"):
            return _cmd_memory_toggle(cwd, sub == "enable", _resolve_config_root(os.environ, []))
        if sub == "audit":
            return _cmd_audit(cwd, argv[2:], _resolve_config_root(os.environ, []))
        if sub == "flag":
            return _cmd_flag(cwd, argv[2:], _resolve_config_root(os.environ, []))
        _warn(f"unknown memory command {sub!r}; try enable, add, audit, flag or consolidate")
        return 2
    if argv and argv[0] == "recall":                # T2 retrieval: no scoping, no launch
        return _cmd_recall(cwd, argv[1:], _resolve_config_root(os.environ, []))
    if argv and argv[0] == "measure":               # opt-in, connects to servers; no scoping
        return _cmd_measure(cwd)
    if argv and argv[0] == "init":                  # bulk-seed repo config; resolves profiles itself
        return _cmd_init(cwd, argv[1:], os.environ)
    if argv and argv[0] == "update":                # refresh existing seeds (single repo or bulk)
        return _cmd_update(cwd, argv[1:], os.environ)
    rules_cmd = bool(argv) and argv[0] == "rules"
    explain = "--explain" in argv
    no_gate = "--no-gate" in argv or bool(os.environ.get("LOADOUT_NO_GATE"))
    scope_skills = "--no-scope-skills" not in argv and not os.environ.get("LOADOUT_NO_SCOPE_SKILLS")
    passthrough = [a for a in argv                                          # loadout flags, not claude's
                   if a not in ("--explain", "--no-gate", "--no-scope-skills")]
    try:                                            # ask which profile when it is implicit
        override = _resolve_config_root(os.environ, [] if rules_cmd else passthrough)
    except _Abort:
        _warn("no profile selected; nothing to do")
        return 0
    if rules_cmd:
        return _cmd_rules(cwd, override)
    fallback_env = _launch_env(override)            # keep a prompted profile on the fallback launches
    try:
        result, plan, gate = _scoped_plan(passthrough, cwd, override, scope_skills)
    except Exception as exc:
        _warn(f"scoping failed ({exc}); launching full session")
        return subprocess.run(["claude", *passthrough], env=fallback_env).returncode
    if plan is None:
        return subprocess.run(["claude", *passthrough], env=fallback_env).returncode
    scope = result
    if explain:
        _print_explain(scope, plan)
        _cleanup(plan.tmp_paths)
        return 0
    scope, plan = _launch_gate(scope, plan, gate, passthrough, no_gate)   # read/adjust before claude takes the screen
    _memory_intro_once(scope.memory)                # the first injection explains itself, once
    _record_delivery(scope, cwd)                    # only a real launch counts as a delivery
    # Only when recall is on: a launcher that captures nothing should not shell out to git.
    head_before = _git(cwd, "rev-parse", "HEAD") if getattr(scope, "memory", None) else None
    try:
        code = subprocess.run(plan.argv, env=plan.env).returncode
    finally:
        _cleanup(plan.tmp_paths)
    _capture_session(scope, cwd, code, head_before)
    return code
