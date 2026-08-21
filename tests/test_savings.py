from smartctx import savings
from smartctx.inventory import Item

def _it(kind, id):
    return Item(id=id, kind=kind, name=id, description=id)

def test_estimate_counts_all_prunable_kinds_including_skills():
    kept = [_it("mcp", "A"), _it("skill", "s1")]
    dropped = [_it("plugin", "P"), _it("skill", "s2"), _it("mcp", "B")]
    s = savings.estimate_savings(kept, dropped)
    assert s.dropped == 3                      # plugin + skill + mcp, skills now prunable
    assert s.total == 5                        # 2 kept prunable + 3 dropped prunable
    assert s.tokens == 600 + 50 + 1200         # plugin + skill + mcp defaults

def test_skills_contribute_their_fallback_cost():
    assert savings.token_estimate([_it("skill", "s1"), _it("skill", "s2")]) == 100

def test_costs_override_is_honoured():
    dropped = [_it("mcp", "A"), _it("plugin", "P")]
    s = savings.estimate_savings([], dropped, {"mcp": 100, "plugin": 0})
    assert s.tokens == 100

def test_measured_cost_overrides_kind_constant():
    dropped = [_it("mcp", "Gmail"), _it("mcp", "Calendar")]
    s = savings.estimate_savings([], dropped, measured={"Gmail": 5000})
    assert s.tokens == 5000 + 1200   # Gmail measured, Calendar falls back to the constant

def test_budget_is_the_ceiling_over_all_prunable_items():
    items = [_it("mcp", "A"), _it("plugin", "P"), _it("skill", "s")]
    b = savings.budget(items)
    assert b.dropped == 0 and b.total == 3 and b.tokens == 1200 + 600 + 50

def test_human_tokens_formatting():
    assert savings.human_tokens(900) == "900"
    assert savings.human_tokens(1800) == "1.8k"
    assert savings.human_tokens(5000) == "5k"
    assert savings.human_tokens(12300) == "12.3k"
