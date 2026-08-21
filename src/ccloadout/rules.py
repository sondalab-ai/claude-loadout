from __future__ import annotations
import sys, tomllib
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from ccloadout.inventory import Item

def _warn(msg: str) -> None:                       # local, avoids importing cli (cycle)
    print(f"loadout: {msg}", file=sys.stderr)

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

def rules_for(item: Item, rules: list[Rule]) -> list[Rule]:   # public: governing rules, most specific first
    return _rules_for(item, rules)

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
    return config_root / "loadout" / "rules.toml"

def profile_rules_file(config_root: Path) -> Path:   # public accessor for the profile-level rules file
    return _rules_file(config_root)

def _parse(path: Path) -> list[Rule]:
    try:
        data = tomllib.loads(path.read_text())
    except OSError:                                 # absent file is the normal case
        return []
    except tomllib.TOMLDecodeError as exc:          # spec §7: parse error -> keep all + warn
        _warn(f"rules parse error in {path} ({exc}); ignoring rules file")
        return []
    out = []
    for r in data.get("rule", []):
        try:
            target = r["target"]
        except KeyError:
            _warn(f"skipping rule without target in {path}")
            continue
        p = r.get("predicate", {})
        out.append(Rule(target=target, nl=r.get("nl", ""),
                        predicate=Predicate(action=p.get("action", "always_keep"),
                                            match=tuple(p.get("match", [])),
                                            match_mode=p.get("match_mode", "any"))))
    return out

def read_rules(path: Path) -> list[Rule]:            # public: parse one rules.toml (absent -> [])
    return _parse(path)

def load_rules(config_root: Path, cwd: Path) -> list[Rule]:
    user = {r.target: r for r in _parse(_rules_file(config_root))}
    repo = {r.target: r for r in _parse(cwd / ".loadout" / "rules.toml")}
    return list({**user, **repo}.values())   # repo overrides per target

_ESC = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}

def _esc(s: str) -> str:                            # keep appended TOML basic strings parseable
    return "".join(
        _ESC.get(c, f"\\u{ord(c):04x}" if ord(c) < 0x20 or ord(c) == 0x7f else c)
        for c in s)                                 # escape C0 controls + DEL (TOML forbids raw)

def _rule_block(rule: Rule) -> str:
    p = rule.predicate
    match_list = ", ".join(f'"{_esc(m)}"' for m in p.match)
    return (f'\n[[rule]]\ntarget = "{_esc(rule.target)}"\nnl = "{_esc(rule.nl)}"\n'
            f'[rule.predicate]\naction = "{p.action}"\n'
            f'match = [{match_list}]\n'
            f'match_mode = "{p.match_mode}"\n')

def append_rule(path: Path, rule: Rule) -> None:
    # Append one rule to an arbitrary rules.toml (profile or a repo's local file).
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(_rule_block(rule))

def save_rule(config_root: Path, rule: Rule) -> None:
    append_rule(_rules_file(config_root), rule)   # profile-scoped: applies across the profile's repos

def write_rules(path: Path, rules: list[Rule], header: str = "") -> None:
    # Full write to an arbitrary rules.toml (init seeds a fresh repo file, not the profile).
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "".join(_rule_block(r) for r in rules))
