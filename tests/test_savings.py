from ccloadout import savings
from ccloadout.inventory import Item

def _it(kind, id):
    return Item(id=id, kind=kind, name=id, description=id)

def test_estimate_counts_all_prunable_kinds_including_skills():
    kept = [_it("mcp", "A"), _it("skill", "s1")]
    dropped = [_it("plugin", "P"), _it("skill", "s2"), _it("mcp", "B")]
    s = savings.estimate_savings(kept, dropped)
    assert s.dropped == 3                      # plugin + skill + mcp, skills now prunable
    assert s.total == 5                        # 2 kept prunable + 3 dropped prunable
    assert s.tokens == 600 + 50 + 1200         # plugin + skill + mcp defaults

def test_footprint_beats_the_flat_fallback_and_measured_beats_both():
    skill = Item(id="s", kind="skill", name="s", description="d", footprint=600)
    plugin = Item(id="P", kind="plugin", name="P", description="d", footprint=2000)
    assert savings.estimate_savings([], [skill, plugin]).eager == 150 + 500   # chars / 4, not 50 + 600
    assert savings.estimate_savings([], [skill], measured={"s": 7}).eager == 7

def test_a_plugin_that_lists_nothing_costs_nothing_up_front():
    hooks_only = Item(id="H", kind="plugin", name="H", description="d", footprint=0)
    unknown = Item(id="U", kind="plugin", name="U", description="d")       # install dir unreadable
    assert savings.estimate_savings([], [hooks_only]).eager == 0
    assert savings.estimate_savings([], [unknown]).eager == 600           # flat fallback

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

# --- injected memory tokens: recall spends what pruning saves -----------------

def test_injected_tokens_are_reported_and_netted():
    from ccloadout.savings import estimate_savings
    kept = [_it("skill", "a"), _it("mcp", "b")]
    dropped = [_it("skill", "c"), _it("skill", "d")]
    s = estimate_savings(kept, dropped, injected=30)
    assert s.eager == 100 and s.injected == 30
    assert s.net == 70                                  # eager saved minus resident memory

def test_net_ignores_deferred_savings():
    from ccloadout.savings import estimate_savings
    s = estimate_savings([], [_it("mcp", "m")], injected=40)
    assert s.deferred == 1200 and s.eager == 0
    assert s.net == -40          # an mcp drop frees ~nothing up front; memory still costs

def test_memory_is_not_a_prunable_kind():
    from ccloadout.savings import PRUNABLE, estimate_savings
    assert "memory" not in PRUNABLE
    s = estimate_savings([], [_it("memory", "x")], injected=0)
    assert s.dropped == 0 and s.tokens == 0             # un-injected memories are not savings
