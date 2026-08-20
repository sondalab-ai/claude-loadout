from __future__ import annotations
from collections.abc import Iterable, Mapping
from typing import NamedTuple, Protocol

class _Kinded(Protocol):
    kind: str
    id: str

# Rough, offline estimates of the context each pruned item injects into a session
# (an MCP server's tool schemas, a plugin's bundled skills/agents/hooks). The real
# cost depends on the server's actual tool set, which smartctx can't see without
# connecting to it — so these are deliberately labelled estimates everywhere they
# surface. Override per-kind with a [token_costs] table in config.toml.
DEFAULT_TOKEN_COSTS: dict[str, int] = {"mcp": 1200, "plugin": 600}

# Only these kinds are actually removed from a session; standalone skills are
# inventoried and shown but never pruned, so they must not count toward savings.
PRUNABLE = frozenset(DEFAULT_TOKEN_COSTS)

class Savings(NamedTuple):
    dropped: int        # prunable items pruned this session
    total: int          # prunable items in scope (kept + dropped)
    tokens: int         # estimated tokens trimmed

def _prunable(items: Iterable[_Kinded]) -> list[_Kinded]:
    return [i for i in items if i.kind in PRUNABLE]

def _item_cost(item: _Kinded, costs: Mapping[str, int],
               measured: Mapping[str, int] | None) -> int:
    # A real measured cost for this exact server (from `smartctx measure`) wins over
    # the flat per-kind estimate.
    if measured and item.id in measured:
        return measured[item.id]
    return costs.get(item.kind, 0)

def token_estimate(items: Iterable[_Kinded], costs: Mapping[str, int] | None = None,
                   measured: Mapping[str, int] | None = None) -> int:
    costs = costs or DEFAULT_TOKEN_COSTS
    return sum(_item_cost(i, costs, measured) for i in items if i.kind in PRUNABLE)

def estimate_savings(kept: Iterable[_Kinded], dropped: Iterable[_Kinded],
                     costs: Mapping[str, int] | None = None,
                     measured: Mapping[str, int] | None = None) -> Savings:
    # kept/dropped are the plain Item lists (callers strip any score tuples first).
    kept_p, dropped_p = _prunable(kept), _prunable(dropped)
    return Savings(dropped=len(dropped_p),
                   total=len(kept_p) + len(dropped_p),
                   tokens=token_estimate(dropped_p, costs, measured))

def budget(items: Iterable[_Kinded], costs: Mapping[str, int] | None = None,
           measured: Mapping[str, int] | None = None) -> Savings:
    # Ceiling view for non-ranking contexts (doctor, post-authoring): what a session
    # could prune at most, before the goal decides how much actually goes.
    prunable = _prunable(items)
    return Savings(dropped=0, total=len(prunable),
                   tokens=token_estimate(prunable, costs, measured))

def human_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    return f"{n / 1000:.1f}k".replace(".0k", "k")
