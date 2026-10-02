from __future__ import annotations
from collections.abc import Iterable, Mapping
from typing import NamedTuple, Protocol

class _Kinded(Protocol):
    kind: str
    id: str

# Fallback estimates of the context each pruned item injects into a session, used only when
# nothing better is known. Skills and plugins normally carry a footprint (the characters of their
# listing lines, read from disk by inventory), which is converted at ~4 characters per token. An
# MCP server's real cost depends on its tool set, which loadout can't see without connecting to
# it (`claude-loadout measure`). Override the fallbacks per kind with [token_costs] in config.toml.
DEFAULT_TOKEN_COSTS: dict[str, int] = {"mcp": 1200, "plugin": 600, "skill": 50}

# Kinds that a scoped session can actually remove. Standalone user skills join mcp and
# plugins here: they are dropped via skillOverrides "off" (see compose), so they count
# toward savings. A measured cost (measured[id], from `claude-loadout measure`) wins, then the
# item's footprint, then the flat per-kind fallback.
PRUNABLE = frozenset(DEFAULT_TOKEN_COSTS)

# How a dropped item's cost lands in a session. EAGER kinds (skill/plugin descriptions and the
# agent lists they carry) sit in the system prompt from the first turn, so dropping them trims
# context up front. DEFERRED kinds (MCP tool schemas) load on demand — Claude Code lists them as
# "loaded on-demand", so their cost materializes only if a tool is actually used. Dropping them
# avoids that potential cost and blocks the invocation, but frees ~nothing up front.
# `memory` is deliberately absent from PRUNABLE and from the cost table: recalled memory is a
# cost the session pays, tracked as Savings.injected, not an item that pruning can remove.
EAGER_KINDS = frozenset({"skill", "plugin"})
_CHARS_PER_TOKEN = 4    # same heuristic as measure.CHARS_PER_TOKEN; labelled an estimate everywhere
DEFERRED_KINDS = frozenset({"mcp"})

class Savings(NamedTuple):
    dropped: int        # prunable items pruned this session
    total: int          # prunable items in scope (kept + dropped)
    tokens: int         # estimated tokens (eager + deferred)
    eager: int = 0      # trimmed from context up front (skill + plugin)
    deferred: int = 0   # on-demand cost avoided only if the tool is used (mcp)
    injected: int = 0   # resident tokens this session *spends* on recalled memory

    @property
    def net(self) -> int:
        # What the session actually gains up front. Deferred savings are excluded on purpose:
        # they free ~nothing until a tool is used, so folding them in would let a hypothetical
        # saving mask a real cost. Negative means recall spent more than pruning saved.
        return self.eager - self.injected

def _prunable(items: Iterable[_Kinded]) -> list[_Kinded]:
    return [i for i in items if i.kind in PRUNABLE]

def _split_by_load(items: Iterable[_Kinded], costs: Mapping[str, int],
                   measured: Mapping[str, int] | None) -> tuple[int, int]:
    items = list(items)
    eager = sum(_item_cost(i, costs, measured) for i in items if i.kind in EAGER_KINDS)
    deferred = sum(_item_cost(i, costs, measured) for i in items if i.kind in DEFERRED_KINDS)
    return eager, deferred

def _item_cost(item: _Kinded, costs: Mapping[str, int],
               measured: Mapping[str, int] | None) -> int:
    # A real measured cost for this exact server (from `claude-loadout measure`) wins, then
    # the text the item actually puts in context, then the flat per-kind estimate.
    if measured and item.id in measured:
        return measured[item.id]
    footprint = getattr(item, "footprint", None)
    if footprint is not None:                       # 0 is a real answer: it lists nothing
        return -(-footprint // _CHARS_PER_TOKEN)    # ceiling division
    return costs.get(item.kind, 0)

def token_estimate(items: Iterable[_Kinded], costs: Mapping[str, int] | None = None,
                   measured: Mapping[str, int] | None = None) -> int:
    costs = costs or DEFAULT_TOKEN_COSTS
    return sum(_item_cost(i, costs, measured) for i in items if i.kind in PRUNABLE)

def estimate_savings(kept: Iterable[_Kinded], dropped: Iterable[_Kinded],
                     costs: Mapping[str, int] | None = None,
                     measured: Mapping[str, int] | None = None,
                     injected: int = 0) -> Savings:
    # kept/dropped are the plain Item lists (callers strip any score tuples first).
    kept_p, dropped_p = _prunable(kept), _prunable(dropped)
    eager, deferred = _split_by_load(dropped_p, costs or DEFAULT_TOKEN_COSTS, measured)
    return Savings(dropped=len(dropped_p), total=len(kept_p) + len(dropped_p),
                   tokens=eager + deferred, eager=eager, deferred=deferred, injected=injected)

def budget(items: Iterable[_Kinded], costs: Mapping[str, int] | None = None,
           measured: Mapping[str, int] | None = None) -> Savings:
    # Ceiling view for non-ranking contexts (doctor, post-authoring): what a session
    # could prune at most, before the goal decides how much actually goes.
    prunable = _prunable(items)
    eager, deferred = _split_by_load(prunable, costs or DEFAULT_TOKEN_COSTS, measured)
    return Savings(dropped=0, total=len(prunable),
                   tokens=eager + deferred, eager=eager, deferred=deferred)

def human_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    return f"{n / 1000:.1f}k".replace(".0k", "k")
