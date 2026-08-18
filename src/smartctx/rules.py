from __future__ import annotations
import tomllib
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from smartctx.inventory import Item

@dataclass(frozen=True)
class Predicate:
    action: str
    match: tuple[str, ...]
    match_mode: str

@dataclass(frozen=True)
class Rule:
    target: str
    nl: str
    predicate: Predicate

@dataclass(frozen=True)
class RuleOutcome:
    forced_keep: tuple[Item, ...]
    forced_drop: tuple[Item, ...]
    undecided: tuple[Item, ...]

def evaluate(predicate: Predicate, context: str) -> str:
    if predicate.action == "always_keep":
        return "keep"
    if predicate.action == "always_drop":
        return "drop"
    ctx = context.lower()
    hits = [term.lower() in ctx for term in predicate.match]
    matched = all(hits) if predicate.match_mode == "all" else any(hits)
    if predicate.action == "keep_if":
        return "keep" if matched else "drop"
    if predicate.action == "drop_if":
        return "drop" if matched else "undecided"
    return "undecided"

def _rules_for(item: Item, rules: list[Rule]) -> list[Rule]:
    matches = [r for r in rules if fnmatch(item.id, r.target)]
    # exact-id rules first, then always_* over conditional
    matches.sort(key=lambda r: (r.target != item.id,
                                r.predicate.action not in ("always_keep", "always_drop")))
    return matches

def has_rule(item: Item, rules: list[Rule]) -> bool:
    return bool(_rules_for(item, rules))

def apply_rules(items: list[Item], rules: list[Rule], context: str) -> RuleOutcome:
    keep, drop, undecided = [], [], []
    for item in items:
        decision = "undecided"
        for rule in _rules_for(item, rules):
            decision = evaluate(rule.predicate, context)
            if decision != "undecided":
                break
        (keep if decision == "keep" else drop if decision == "drop" else undecided).append(item)
    return RuleOutcome(tuple(keep), tuple(drop), tuple(undecided))

def _rules_file(config_root: Path) -> Path:
    return config_root / "smartctx" / "rules.toml"

def _parse(path: Path) -> list[Rule]:
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return []
    out = []
    for r in data.get("rule", []):
        p = r.get("predicate", {})
        out.append(Rule(target=r["target"], nl=r.get("nl", ""),
                        predicate=Predicate(action=p.get("action", "always_keep"),
                                            match=tuple(p.get("match", [])),
                                            match_mode=p.get("match_mode", "any"))))
    return out

def load_rules(config_root: Path, cwd: Path) -> list[Rule]:
    user = {r.target: r for r in _parse(_rules_file(config_root))}
    repo = {r.target: r for r in _parse(cwd / ".smartctx" / "rules.toml")}
    return list({**user, **repo}.values())   # repo overrides per target

def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')

def save_rule(config_root: Path, rule: Rule) -> None:
    path = _rules_file(config_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    p = rule.predicate
    match_list = ", ".join(f'"{_esc(m)}"' for m in p.match)
    block = (f'\n[[rule]]\ntarget = "{_esc(rule.target)}"\nnl = "{_esc(rule.nl)}"\n'
             f'[rule.predicate]\naction = "{p.action}"\n'
             f'match = [{match_list}]\n'
             f'match_mode = "{p.match_mode}"\n')
    with path.open("a") as fh:
        fh.write(block)
