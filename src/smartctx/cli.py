from __future__ import annotations
import subprocess, sys
from fnmatch import fnmatch
from pathlib import Path
from typing import NamedTuple
from smartctx.config import load_config
from smartctx.inventory import claude_code_inventory, Item
from smartctx.goal import detect_goal, write_goal_cache
from smartctx.ranker import Ranker, make_model2vec_embed, keyword_embed, bundled_model_path, resolve_model_source
from smartctx.compose import compose
from smartctx.rules import load_rules, apply_rules, has_rule, save_rule, Rule, Predicate, evaluate
from smartctx.compiler import compile_rule, make_local_instruct

def _warn(msg: str) -> None:
    print(f"smartctx: {msg}", file=sys.stderr)

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
        entered = _ask(f"smartctx: session goal? [{goal.goal}] ").strip()
        if entered:
            write_goal_cache(cwd, entered)         # preflight ruling: persist, don't re-ask
            return entered, "prompt", 1.0
    return goal.goal, goal.source, goal.confidence

def _elicit(item: Item, context: str, compile_fn, config_root: Path) -> str:
    nl = _ask(f"smartctx: rule for '{item.id}' ({item.kind})? [enter=skip] ").strip()
    if not nl:
        return "undecided"
    pred = compile_rule(nl, item, compile_fn) if compile_fn else None
    if pred is None:                               # spec §7 degrade
        choice = _ask("  couldn't compile; [k]eep always / [d]rop always / [s]kip? ").strip().lower()
        pred = {"k": Predicate("always_keep", (), "any"),
                "d": Predicate("always_drop", (), "any")}.get(choice)
        if pred is None:
            return "undecided"
    save_rule(config_root, Rule(target=item.id, nl=nl, predicate=pred))
    return evaluate(pred, context)

class _Scope(NamedTuple):
    goal: str
    source: str
    confidence: float
    threshold: float
    kept: list
    dropped: list

def _scoped_plan(passthrough: list[str], cwd: Path):
    cfg = load_config(cwd=cwd)
    items = claude_code_inventory(cfg.config_root, cwd)
    if not items:
        return None, None
    context, gsource, gconf = _resolve_goal(cwd, passthrough)
    rules = load_rules(cfg.config_root, cwd)
    pinned = [i for i in items if any(fnmatch(i.id, g) for g in cfg.always_keep)]
    pinned_ids = {i.id for i in pinned}            # always_keep config wins over rules (spec §12)
    remainder = [i for i in items if i.id not in pinned_ids]
    outcome = apply_rules(remainder, rules, context)
    embed = _build_embed(cfg.model_name)
    ranked = Ranker(embed=embed).rank(context, list(outcome.undecided), cfg.threshold, cfg.always_keep)
    kept = list(pinned) + list(outcome.forced_keep) + list(ranked.kept)
    dropped = list(ranked.dropped) + [(i, "rule") for i in outcome.forced_drop]  # surface in --explain
    if _interactive(passthrough):                  # launch-time elicitation for rule-less drops
        compile_fn = _build_compiler(cfg)
        ruleless = [i for i, _ in dropped                     # only prunable kinds are actionable
                    if i.kind in {"mcp", "plugin"} and not has_rule(i, rules)]
        if ruleless:
            _warn(f"{len(ruleless)} item(s) would be dropped with no rule; asking (enter to skip)")
            for item in ruleless:
                if _elicit(item, context, compile_fn, cfg.config_root) == "keep":
                    kept.append(item)
                    dropped = [(i, s) for i, s in dropped if i.id != item.id]
    plan = compose(kept, items, cfg.config_root, passthrough, cwd=cwd)
    return _Scope(context, gsource, gconf, cfg.threshold, kept, dropped), plan

def _cmd_rules(cwd: Path) -> int:
    if not _interactive([]):                        # no TTY -> nothing to elicit; fail-open
        _warn("rule authoring needs an interactive terminal; nothing to do")
        return 0
    cfg = load_config(cwd=cwd)
    items = claude_code_inventory(cfg.config_root, cwd)
    rules = load_rules(cfg.config_root, cwd)
    compile_fn = _build_compiler(cfg)
    context = _resolve_goal(cwd, [])
    authored = 0
    for item in items:
        if has_rule(item, rules):
            continue
        if _elicit(item, context, compile_fn, cfg.config_root) != "undecided":
            authored += 1
    print(f"smartctx: authored {authored} rule(s)")
    return 0

def _cmd_doctor(cwd: Path) -> int:
    cfg = load_config(cwd=cwd)
    print("smartctx doctor — setup check")
    print(f"  config dir: {cfg.config_root}")
    for label, path in (("user config", cfg.config_root / "smartctx" / "config.toml"),
                        ("repo config", cwd / ".smartctx" / "config.toml")):
        print(f"  {label}: {path} ({'present' if path.is_file() else 'absent'})")
    try:
        items = claude_code_inventory(cfg.config_root, cwd)
    except Exception as exc:                       # fail-open: doctor must never crash
        print(f"  inventory: unavailable ({exc})")
    else:
        counts = {k: sum(1 for i in items if i.kind == k) for k in ("mcp", "plugin", "skill")}
        print(f"  inventory: {counts['mcp']} mcp, {_plural(counts['plugin'], 'plugin')}, "
              f"{_plural(counts['skill'], 'skill')}")
    resolved = resolve_model_source(cfg.model_name)
    embed = _build_embed(cfg.model_name)           # warns + falls back on failure
    model_state = ("keyword fallback" if embed is keyword_embed
                   else "bundled copy" if resolved == str(bundled_model_path()) else "external")
    print(f"  embedding model: {cfg.model_name} ({model_state})")
    print(f"  rule model: {cfg.rule_model_path or 'not configured — rule authoring uses keep/drop/skip prompts'}")
    print("\nNext steps")
    print("  1. Point your launch command at smartctx, e.g. add to your shell rc:")
    print('       alias claude="smartctx"')
    print('     or wrap a separate profile:')
    print('       alias claude-work="CLAUDE_CONFIG_DIR=~/.claude-work smartctx"')
    print("  2. Preview what a session would load, without launching anything:")
    print("       smartctx --explain")
    print("  3. Scope tools with plain-language rules:")
    print("       smartctx rules")
    return 0

def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cwd = Path.cwd()
    if argv and argv[0] == "rules":
        return _cmd_rules(cwd)
    if argv and argv[0] == "doctor":
        return _cmd_doctor(cwd)
    explain = "--explain" in argv
    passthrough = [a for a in argv if a != "--explain"]
    try:
        result, plan = _scoped_plan(passthrough, cwd)
    except Exception as exc:
        _warn(f"scoping failed ({exc}); launching full session")
        return subprocess.run(["claude", *passthrough]).returncode
    if plan is None:
        return subprocess.run(["claude", *passthrough]).returncode
    scope = result
    if explain:
        print(f"goal: {scope.goal!r}  (source: {scope.source}, confidence: {scope.confidence:.2f})")
        print(f"threshold: {scope.threshold}")
        print(f"kept: {[i.id for i in scope.kept]}")
        print(f"dropped: {[(i.id, round(s, 3) if isinstance(s, float) else s) for i, s in scope.dropped]}")
        print("argv: " + " ".join(plan.argv))
        _cleanup(plan.tmp_paths)
        return 0
    try:
        return subprocess.run(plan.argv, env=plan.env).returncode
    finally:
        _cleanup(plan.tmp_paths)
