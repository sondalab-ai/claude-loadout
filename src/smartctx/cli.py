from __future__ import annotations
import os, subprocess, sys
from fnmatch import fnmatch
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import NamedTuple
from smartctx.config import load_config
from smartctx.inventory import claude_code_inventory, Item
from smartctx.goal import detect_goal, write_goal_cache
from smartctx.ranker import Ranker, make_model2vec_embed, keyword_embed, bundled_model_path, resolve_model_source
from smartctx.compose import compose
from smartctx.rules import load_rules, apply_rules, has_rule, save_rule, Rule, Predicate, evaluate
from smartctx.compiler import compile_rule, make_local_instruct
from smartctx import savings as _savings
from smartctx import measure as _measure

class _Abort(Exception):
    """User declined to pick a profile at the selection prompt."""

_SGR = {"dim": "2", "bold": "1", "green": "32", "red": "31", "cyan": "36", "yellow": "33"}

def _supports_color(err: bool) -> bool:
    # Looked up lazily (not cached at import) so pytest's capsys stream swap is honoured.
    if os.environ.get("NO_COLOR") or os.environ.get("SMARTCTX_NO_COLOR"):
        return False
    stream = sys.stderr if err else sys.stdout
    return stream.isatty()

def _paint(text: str, *codes: str, err: bool = False) -> str:
    if not _supports_color(err):
        return text
    return f"\033[{';'.join(_SGR[c] for c in codes)}m{text}\033[0m"

def _yn(flag: bool, *, err: bool = False) -> str:
    return _paint("present", "green", err=err) if flag else _paint("absent", "dim", err=err)

def _warn(msg: str) -> None:
    print(f"{_paint('smartctx:', 'yellow', err=True)} {msg}", file=sys.stderr)

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

def _rules_intro(compile_fn) -> None:
    # Printed once before an elicitation run so the interaction isn't a cold prompt.
    p = lambda s: print(s, file=sys.stderr)
    dim = lambda s: _paint(s, "dim", err=True)
    p("")
    if compile_fn:
        p(f"{_paint('smartctx:', 'yellow', err=True)} for each tool, describe in plain "
          "language when to keep or drop it.")
        p(dim('    e.g.  "keep only when the goal is frontend"'))
        p(dim('          "drop unless it mentions email"'))
        p(dim('          "always keep this"'))
        p(dim("    press enter to skip; if a description can't be translated "
              "you'll get keep/drop/skip choices."))
    else:
        p(f"{_paint('smartctx:', 'yellow', err=True)} no rule model configured — "
          "natural-language rules are unavailable.")
        p(dim("    [k]eep always / [d]rop always write a permanent rule; "
              "[s]kip (enter) decides nothing and asks again next time."))
    p("")

def _pick_keep_drop_skip(item: Item) -> Predicate | None:
    choice = _ask(f"  '{item.id}': [k]eep always / [d]rop always / [s]kip (decide later)? ").strip().lower()
    return {"k": Predicate("always_keep", (), "any"),
            "d": Predicate("always_drop", (), "any")}.get(choice)

def _elicit(item: Item, context: str, compile_fn, config_root: Path) -> str:
    nl = ""
    if compile_fn:                                 # natural-language authoring path
        nl = _ask(f"smartctx: rule for '{item.id}' ({item.kind})? [enter=skip] ").strip()
        if not nl:
            return "undecided"
        pred = compile_rule(nl, item, compile_fn)
        if pred is not None:
            save_rule(config_root, Rule(target=item.id, nl=nl, predicate=pred))
            return evaluate(pred, context)
        _warn(f"couldn't translate that into a rule for '{item.id}'; choose manually")
    pred = _pick_keep_drop_skip(item)              # spec §7 degrade / no rule model
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
    savings: _savings.Savings
    connectors: list                                # (id, tokens) claude.ai connectors strict-mode drops
    measured: bool                                  # whether a costs.json cache was found

def _scoped_plan(passthrough: list[str], cwd: Path, config_root_override: Path | None = None):
    cfg = load_config(cwd=cwd, config_root_override=config_root_override)
    items = claude_code_inventory(cfg.config_root, cwd, cfg.global_config_path)
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
            _rules_intro(compile_fn)
            for item in ruleless:
                if _elicit(item, context, compile_fn, cfg.config_root) == "keep":
                    kept.append(item)
                    dropped = [(i, s) for i, s in dropped if i.id != item.id]
    plan = compose(kept, items, cfg.config_root, passthrough, cwd=cwd,
                   global_config_path=cfg.global_config_path,
                   launch_config_dir=_explicit_profile(config_root_override))
    measured = _measure.load_costs(cfg.config_root)
    saved = _savings.estimate_savings(kept, [i for i, _ in dropped], cfg.token_costs, measured)
    mcp_ids = {i.id for i in items if i.kind == "mcp"}
    connectors = sorted(_measure.connector_costs(measured, mcp_ids).items(), key=lambda kv: -kv[1])
    return _Scope(context, gsource, gconf, cfg.threshold, kept, dropped,
                  saved, connectors, bool(measured)), plan

def _cmd_rules(cwd: Path, config_root_override: Path | None = None) -> int:
    if not _interactive([]):                        # no TTY -> nothing to elicit; fail-open
        _warn("rule authoring needs an interactive terminal; nothing to do")
        return 0
    cfg = load_config(cwd=cwd, config_root_override=config_root_override)
    items = claude_code_inventory(cfg.config_root, cwd, cfg.global_config_path)
    rules = load_rules(cfg.config_root, cwd)
    compile_fn = _build_compiler(cfg)
    context = _resolve_goal(cwd, [])
    pending = [i for i in items if not has_rule(i, rules)]
    if not pending:
        print(f"{_paint('smartctx:', 'green')} every tool already has a rule")
        return 0
    _rules_intro(compile_fn)
    authored = 0
    for item in pending:
        if _elicit(item, context, compile_fn, cfg.config_root) != "undecided":
            authored += 1
    print()
    print(f"{_paint('smartctx:', 'green')} authored {_plural(authored, 'rule')}")
    cache = _measure.load_costs(cfg.config_root)
    b = _savings.budget(items, cfg.token_costs, cache)
    print(f"{_paint('smartctx:', 'green')} up to ~{_savings.human_tokens(b.tokens)} tokens "
          "prunable per session — run `smartctx --explain` for this session's estimate")
    conns = _measure.connector_costs(cache, {i.id for i in items if i.kind == "mcp"})
    if conns:
        print(f"{_paint('smartctx:', 'green')} plus ~{_savings.human_tokens(sum(conns.values()))} "
              f"tokens from {_plural(len(conns), 'claude.ai connector')} dropped by strict mode")
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
    print(f"{_paint('smartctx:', 'yellow', err=True)} CLAUDE_CONFIG_DIR not set — "
          "pick a Claude profile:", file=sys.stderr)
    print("", file=sys.stderr)
    for idx, prof in enumerate(profiles, 1):
        has_cfg = (prof / "smartctx" / "config.toml").is_file()
        num = _paint(f"{idx})", "bold", err=True)
        print(f"  {num} {prof}  {_paint(f'(smartctx config: {_yn(has_cfg, err=True)})', 'dim', err=True)}",
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

def _profile_report(root: Path, cwd: Path, active: bool,
                    global_config_path: Path | None = None,
                    token_costs: dict[str, int] | None = None) -> None:
    tag = f" {_paint('(active)', 'green', 'bold')}" if active else ""
    print(f"  {_paint(str(root), 'cyan')}{tag}")
    user_cfg = root / "smartctx" / "config.toml"
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
    print(f"    prunable:     up to ~{_savings.human_tokens(b.tokens)} tokens "
          f"{_paint('(estimate; actual depends on the session goal)', 'dim')}")
    conns = _measure.connector_costs(cache, {i.id for i in items if i.kind == "mcp"})
    if conns:
        print(f"    connectors:   {_plural(len(conns), 'server')} dropped by strict mode, "
              f"~{_savings.human_tokens(sum(conns.values()))} tokens "
              f"{_paint('(measured; not selectable)', 'dim')}")

def _cmd_doctor(cwd: Path) -> int:
    cfg = load_config(cwd=cwd)
    print(_paint("smartctx doctor", "bold"))
    print()
    profiles = _discover_profiles(cfg.config_root)
    print(_paint(f"  claude profiles: {_plural(len(profiles), 'profile')}", "bold"))
    print()
    for root in profiles:
        active = root == cfg.config_root
        _profile_report(root, cwd, active,
                        cfg.global_config_path if active else None, cfg.token_costs)
        print()
    repo_cfg = cwd / ".smartctx" / "config.toml"
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
    print("    1. Point your launch command at smartctx, e.g. add to your shell rc:")
    print(f"         {_paint('alias claude=\"smartctx\"', 'cyan')}")
    print("       or wrap a separate profile:")
    print(f"         {_paint('alias claude-work=\"CLAUDE_CONFIG_DIR=~/.claude-work smartctx\"', 'cyan')}")
    print("    2. Preview what a session would load, without launching anything:")
    print(f"         {_paint('smartctx --explain', 'cyan')}")
    print("    3. Scope tools with plain-language rules:")
    print(f"         {_paint('smartctx rules', 'cyan')}")
    return 0

def _cmd_measure(cwd: Path) -> int:
    cfg = load_config(cwd=cwd)
    print(_paint("smartctx measure", "bold"))
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
    print(f"{_paint('smartctx:', 'green')} measured {_plural(n, 'server')}, "
          f"~{_savings.human_tokens(total)} tokens total")
    print(_paint(f"  cached to {_measure.costs_path(cfg.config_root)}", "dim"))
    print(_paint("  these are a diagnostic view; a measured cost feeds savings only for MCP "
                 "servers in your .claude.json/.mcp.json (matched by bare name).", "dim"))
    print(_paint("  claude.ai connectors and plugin-bundled servers are shown here but aren't "
                 "pruned by smartctx.", "dim"))
    return 0

def _smartctx_version() -> str:
    try:
        return version("smartctx")
    except PackageNotFoundError:                     # running from a source tree, uninstalled
        return "unknown"

def _print_help() -> None:
    cmd = lambda s: _paint(s, "cyan")
    print(
        f"{_paint('smartctx', 'bold')} — goal-aware launcher for Claude Code\n"
        "\n"
        f"{_paint('Usage:', 'bold')}\n"
        f"  {cmd('smartctx [claude-args...]')}   Launch claude with a goal-scoped tool set\n"
        f"  {cmd('smartctx --explain')}          Print the scoping plan, then exit (no launch)\n"
        f"  {cmd('smartctx rules')}              Author keep/drop rules interactively\n"
        f"  {cmd('smartctx doctor')}             Report profiles, config, and model state\n"
        f"  {cmd('smartctx measure')}            Measure real MCP tool-token cost (opt-in, connects)\n"
        f"  {cmd('smartctx --help, -h')}         Show this help\n"
        f"  {cmd('smartctx --version, -V')}      Show the smartctx version\n"
        "\n"
        f"{_paint('Any other flags pass straight through to claude — run `claude --help` for those.', 'dim')}"
    )

def _print_explain(scope: _Scope, plan) -> None:
    print(_paint("smartctx — scoping plan", "bold"))
    print()
    lbl = lambda s: _paint(f"  {s:<11}", "dim")
    print(f"{lbl('goal')}{scope.goal!r}   "
          f"{_paint(f'({scope.source} · confidence {scope.confidence:.2f})', 'dim')}")
    print(f"{lbl('threshold')}{scope.threshold}")
    print()
    kept = [i.id for i in scope.kept]
    print(_paint(f"  keeping ({len(kept)})", "bold"))
    for cid in kept:
        print(f"    {_paint('✓', 'green')} {cid}")
    if not kept:
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
    s = scope.savings
    print(_paint("  savings", "bold"))
    print(f"    ranked tools:   {s.dropped} of {s.total} pruned "
          f"{_paint('(≈ ' + _savings.human_tokens(s.tokens) + ' tokens)', 'dim')}")
    if scope.connectors:
        print(f"    connectors:     {len(scope.connectors)} dropped "
              f"{_paint('(≈ ' + _savings.human_tokens(conn_tok) + ' tokens, measured)', 'dim')}")
    elif not scope.measured:
        print(_paint("    connectors:     run `smartctx measure` to quantify the claude.ai "
                     "connectors strict mode drops", "dim"))
    print(f"    {_paint('≈ ' + _savings.human_tokens(s.tokens + conn_tok) + ' tokens', 'green', 'bold')} "
          "trimmed this session (estimate)")
    print()
    print(_paint("  command", "bold"))
    print(f"    {_paint(' '.join(plan.argv), 'dim')}")

def _savings_line(scope: _Scope) -> str:
    s = scope.savings
    conn_tok = sum(t for _, t in scope.connectors)
    core = f"scoped out {s.dropped} of {s.total} prunable tools"
    if scope.connectors:
        core += f" + {len(scope.connectors)} connectors"
    return f"{core} · ~{_savings.human_tokens(s.tokens + conn_tok)} tokens trimmed (estimate)"

def main(argv: list[str] | None = None) -> int:
    try:
        return _run(argv)
    except KeyboardInterrupt:                       # Ctrl-C at any prompt -> clean abort, no traceback
        _warn("aborted")
        return 130

def _run(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cwd = Path.cwd()
    if argv and argv[0] in ("--help", "-h", "help"):  # smartctx's own help; no profile prompt/scoping
        _print_help()
        return 0
    if argv and argv[0] in ("--version", "-V"):
        print(f"smartctx {_smartctx_version()}")
        return 0
    if argv and argv[0] == "doctor":                # doctor enumerates every profile itself
        return _cmd_doctor(cwd)
    if argv and argv[0] == "measure":               # opt-in, connects to servers; no scoping
        return _cmd_measure(cwd)
    rules_cmd = bool(argv) and argv[0] == "rules"
    explain = "--explain" in argv
    passthrough = [a for a in argv if a != "--explain"]
    try:                                            # ask which profile when it is implicit
        override = _resolve_config_root(os.environ, [] if rules_cmd else passthrough)
    except _Abort:
        _warn("no profile selected; nothing to do")
        return 0
    if rules_cmd:
        return _cmd_rules(cwd, override)
    fallback_env = _launch_env(override)            # keep a prompted profile on the fallback launches
    try:
        result, plan = _scoped_plan(passthrough, cwd, override)
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
    if (scope.savings.dropped or scope.connectors) and _interactive(passthrough):  # skip on -p pipelines
        _warn(_savings_line(scope))
    try:
        return subprocess.run(plan.argv, env=plan.env).returncode
    finally:
        _cleanup(plan.tmp_paths)
