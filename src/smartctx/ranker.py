from __future__ import annotations
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Callable
import numpy as np
from smartctx.inventory import Item

@dataclass(frozen=True)
class Selection:
    kept: tuple[Item, ...]
    dropped: tuple[tuple[Item, float], ...]

def _cosine(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    an = a / (np.linalg.norm(a) + 1e-9)
    bn = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-9)
    return bn @ an

class Ranker:
    def __init__(self, embed: Callable[[list[str]], np.ndarray]):
        self._embed = embed

    def rank(self, goal: str, items: list[Item], threshold: float,
             always_keep: tuple[str, ...]) -> Selection:
        if not items:
            return Selection(kept=(), dropped=())
        vecs = self._embed([goal] + [f"{i.name}. {i.description}" for i in items])
        goal_vec, item_vecs = vecs[0], vecs[1:]
        scores = _cosine(goal_vec, item_vecs)
        kept, dropped = [], []
        for item, score in zip(items, scores):
            forced = any(fnmatch(item.id, pat) for pat in always_keep)
            if forced or score >= threshold:
                kept.append(item)
            else:
                dropped.append((item, float(score)))
        return Selection(kept=tuple(kept), dropped=tuple(dropped))

def make_model2vec_embed(model_name: str) -> Callable[[list[str]], np.ndarray]:
    from model2vec import StaticModel
    model = StaticModel.from_pretrained(model_name)
    return lambda texts: np.asarray(model.encode(texts), dtype=float)

def keyword_embed(texts: list[str]) -> np.ndarray:
    # offline fallback: bag-of-words hashing into a fixed space
    dim = 256
    out = np.zeros((len(texts), dim))
    for r, t in enumerate(texts):
        for tok in t.lower().split():
            out[r, hash(tok) % dim] += 1.0
    return out
