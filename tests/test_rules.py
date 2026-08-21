from pathlib import Path
from ccloadout.inventory import Item
from ccloadout.rules import Predicate, Rule, evaluate, apply_rules, has_rule, load_rules, save_rule

def _items():
    return [Item("design-system-ds", "plugin", "design-system-ds", "corporate design system"),
            Item("astro", "skill", "astro", "astrophotography")]

def test_evaluate_semantics():
    keep_if = Predicate("keep_if", ("design system", "frontend"), "any")
    assert evaluate(keep_if, "design system frontend work") == "keep"
    assert evaluate(keep_if, "astro imaging") == "drop"          # keep_if no-match -> drop
    drop_if = Predicate("drop_if", ("astro",), "any")
    assert evaluate(drop_if, "astro imaging") == "drop"
    assert evaluate(drop_if, "design system work") == "undecided"      # drop_if no-match -> undecided
    assert evaluate(Predicate("always_keep", (), "any"), "anything") == "keep"

def test_apply_rules_partitions_items():
    rules = [Rule("design-system-*", "corp", Predicate("keep_if", ("design system",), "any"))]
    out = apply_rules(_items(), rules, context="astro imaging")
    assert [i.id for i in out.forced_drop] == ["design-system-ds"]     # keep_if no-match -> drop
    assert [i.id for i in out.undecided] == ["astro"]            # no rule -> undecided
    assert out.forced_keep == ()

def test_exact_id_beats_glob():
    rules = [Rule("design-system-*", "g", Predicate("always_drop", (), "any")),
             Rule("design-system-ds", "e", Predicate("always_keep", (), "any"))]
    out = apply_rules(_items()[:1], rules, context="x")
    assert [i.id for i in out.forced_keep] == ["design-system-ds"]

def test_load_and_save_roundtrip(tmp_path: Path):
    root = tmp_path / "root"; (root / "loadout").mkdir(parents=True)
    save_rule(root, Rule("figma*", "design only", Predicate("keep_if", ("design", "ui"), "any")))
    rules = load_rules(config_root=root, cwd=tmp_path)
    assert any(r.target == "figma*" and r.predicate.match == ("design", "ui") for r in rules)
    assert has_rule(Item("figma@x", "plugin", "figma", ""), rules) is True

def test_malformed_rules_warns_and_does_not_crash(tmp_path, capsys):
    root = tmp_path / "root"; (root / "loadout").mkdir(parents=True)
    (root / "loadout" / "rules.toml").write_text("this = is = not valid toml\n[[")
    rules = load_rules(config_root=root, cwd=tmp_path)    # must not raise
    assert rules == []
    assert "parse error" in capsys.readouterr().err.lower()

def test_save_rule_escapes_control_chars_roundtrip(tmp_path: Path):
    root = tmp_path / "root"; (root / "loadout").mkdir(parents=True)
    nasty = 'line1\nline2\ttab "quote" \\back'
    save_rule(root, Rule("weird*", nasty, Predicate("drop_if", (nasty,), "any")))
    rules = load_rules(config_root=root, cwd=tmp_path)    # appended TOML stays parseable
    r = next(r for r in rules if r.target == "weird*")
    assert r.nl == nasty and r.predicate.match == (nasty,)

def test_parse_skips_rule_without_target(tmp_path, capsys):
    root = tmp_path / "root"; (root / "loadout").mkdir(parents=True)
    (root / "loadout" / "rules.toml").write_text(
        '[[rule]]\nnl = "no target here"\n[rule.predicate]\naction = "always_keep"\n'
        '[[rule]]\ntarget = "ok"\n[rule.predicate]\naction = "always_drop"\n')
    rules = load_rules(config_root=root, cwd=tmp_path)    # must not raise KeyError
    assert [r.target for r in rules] == ["ok"]
    assert "without target" in capsys.readouterr().err.lower()

def test_conditional_rule_matches_docs_goal_with_separator():
    from ccloadout.rules import Predicate, evaluate
    goal = "loadout — start sessions with only what you need · python project"
    assert evaluate(Predicate("keep_if", ("python",), "any"), goal) == "keep"   # separator doesn't block substring
    assert evaluate(Predicate("drop_if", ("email",), "any"), goal) == "undecided"
