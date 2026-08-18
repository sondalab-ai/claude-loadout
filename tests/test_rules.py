from pathlib import Path
from smartctx.inventory import Item
from smartctx.rules import Predicate, Rule, evaluate, apply_rules, has_rule, load_rules, save_rule

def _items():
    return [Item("camunda-ds", "plugin", "camunda-ds", "corporate design system"),
            Item("astro", "skill", "astro", "astrophotography")]

def test_evaluate_semantics():
    keep_if = Predicate("keep_if", ("camunda", "bpmn"), "any")
    assert evaluate(keep_if, "camunda frontend work") == "keep"
    assert evaluate(keep_if, "astro imaging") == "drop"          # keep_if no-match -> drop
    drop_if = Predicate("drop_if", ("astro",), "any")
    assert evaluate(drop_if, "astro imaging") == "drop"
    assert evaluate(drop_if, "camunda work") == "undecided"      # drop_if no-match -> undecided
    assert evaluate(Predicate("always_keep", (), "any"), "anything") == "keep"

def test_apply_rules_partitions_items():
    rules = [Rule("camunda-*", "corp", Predicate("keep_if", ("camunda",), "any"))]
    out = apply_rules(_items(), rules, context="astro imaging")
    assert [i.id for i in out.forced_drop] == ["camunda-ds"]     # keep_if no-match -> drop
    assert [i.id for i in out.undecided] == ["astro"]            # no rule -> undecided
    assert out.forced_keep == ()

def test_exact_id_beats_glob():
    rules = [Rule("camunda-*", "g", Predicate("always_drop", (), "any")),
             Rule("camunda-ds", "e", Predicate("always_keep", (), "any"))]
    out = apply_rules(_items()[:1], rules, context="x")
    assert [i.id for i in out.forced_keep] == ["camunda-ds"]

def test_load_and_save_roundtrip(tmp_path: Path):
    root = tmp_path / "root"; (root / "smartctx").mkdir(parents=True)
    save_rule(root, Rule("figma*", "design only", Predicate("keep_if", ("design", "ui"), "any")))
    rules = load_rules(config_root=root, cwd=tmp_path)
    assert any(r.target == "figma*" and r.predicate.match == ("design", "ui") for r in rules)
    assert has_rule(Item("figma@x", "plugin", "figma", ""), rules) is True
