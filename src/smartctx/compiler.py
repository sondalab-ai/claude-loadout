from __future__ import annotations
import json
from typing import Callable
from smartctx.inventory import Item
from smartctx.rules import Predicate

_VALID = {"keep_if", "drop_if", "always_keep", "always_drop"}

def build_prompt(nl: str, item: Item) -> str:
    return (
        "Translate the exclusion rule into a JSON predicate. Output ONLY JSON.\n"
        'Schema: {"action": one of ["keep_if","drop_if","always_keep","always_drop"], '
        '"match": [strings], "match_mode": "any"|"all"}\n'
        "keep_if: keep the item only when the session context matches; "
        "drop_if: drop only when it matches.\n"
        f"Item id: {item.id}\nItem kind: {item.kind}\nItem description: {item.description}\n"
        f"Rule (natural language): {nl}\nJSON:"
    )

def _parse(text: str) -> Predicate | None:
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        data = json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return None
    action = data.get("action")
    match = data.get("match", [])
    if action not in _VALID or not isinstance(match, list):
        return None
    if action in ("keep_if", "drop_if") and not match:
        return None
    mode = data.get("match_mode", "any")
    return Predicate(action=action, match=tuple(str(m) for m in match),
                     match_mode="all" if mode == "all" else "any")

def compile_rule(nl: str, item: Item, compile_fn: Callable[[str], str]) -> Predicate | None:
    prompt = build_prompt(nl, item)
    for _ in range(2):                         # initial try + one retry
        pred = _parse(compile_fn(prompt))
        if pred is not None:
            return pred
    return None

def make_local_instruct(model_path: str) -> Callable[[str], str]:
    from llama_cpp import Llama                 # lazy: base install has no llama-cpp
    llm = Llama(model_path=model_path, n_ctx=2048, verbose=False)
    def compile_fn(prompt: str) -> str:
        out = llm(prompt, max_tokens=128, temperature=0.0, stop=["\n\n"])
        return out["choices"][0]["text"]
    return compile_fn
