import json
from smartctx.inventory import Item
from smartctx.compiler import compile_rule, build_prompt

_ITEM = Item("camunda-ds", "plugin", "camunda-ds", "corporate design system")

def test_build_prompt_mentions_item_and_actions():
    p = build_prompt("corporate, only for camunda work", _ITEM)
    assert "camunda-ds" in p and "keep_if" in p and "drop_if" in p

def test_compile_rule_parses_valid_json():
    def fake(_prompt):
        return json.dumps({"action": "keep_if", "match": ["camunda", "bpmn"], "match_mode": "any"})
    pred = compile_rule("corporate only for camunda", _ITEM, compile_fn=fake)
    assert pred.action == "keep_if" and pred.match == ("camunda", "bpmn")

def test_compile_rule_retries_then_gives_up():
    calls = {"n": 0}
    def bad(_prompt):
        calls["n"] += 1
        return "not json"
    assert compile_rule("x", _ITEM, compile_fn=bad) is None
    assert calls["n"] == 2                     # one retry
