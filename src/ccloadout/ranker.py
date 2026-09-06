from __future__ import annotations
import zlib
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable
import numpy as np
from ccloadout.inventory import Item

# The default embedding model is vendored inside the package (models/potion-base-8M),
# so a fresh install ranks offline with no first-run download.
_DEFAULT_MODEL_ID = "minishlab/potion-base-8M"

def bundled_model_path() -> Path:
    return Path(__file__).parent / "models" / "potion-base-8M"

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

    def score(self, goal: str, items: list[Item]) -> list[tuple[Item, float]]:
        # Item order is preserved; callers that need rank order sort on the score themselves.
        if not items:
            return []
        vecs = self._embed([goal] + [f"{i.name}. {i.description}" for i in items])
        return [(item, float(s)) for item, s in zip(items, _cosine(vecs[0], vecs[1:]))]

    def rank(self, goal: str, items: list[Item], threshold: float,
             always_keep: tuple[str, ...]) -> Selection:
        if not items:
            return Selection(kept=(), dropped=())
        kept, dropped = [], []
        for item, score in self.score(goal, items):
            forced = any(fnmatch(item.id, pat) for pat in always_keep)
            if forced or score >= threshold:
                kept.append(item)
            else:
                dropped.append((item, float(score)))
        return Selection(kept=tuple(kept), dropped=tuple(dropped))

def resolve_model_source(model_name: str) -> str:
    # A local directory path wins; the default id maps to the vendored copy;
    # anything else is treated as a Hub id (fetched on demand).
    candidate = Path(model_name)
    if candidate.is_dir():
        return str(candidate)
    if model_name in (_DEFAULT_MODEL_ID, "potion-base-8M") and bundled_model_path().is_dir():
        return str(bundled_model_path())
    return model_name

def make_model2vec_embed(model_name: str) -> Callable[[list[str]], np.ndarray]:
    from model2vec import StaticModel
    model = StaticModel.from_pretrained(resolve_model_source(model_name))
    return lambda texts: np.asarray(model.encode(texts), dtype=float)

def keyword_embed(texts: list[str]) -> np.ndarray:
    # offline fallback: bag-of-words hashing into a fixed space
    dim = 256
    out = np.zeros((len(texts), dim))
    for r, t in enumerate(texts):
        for tok in t.lower().split():
            out[r, zlib.crc32(tok.encode()) % dim] += 1.0   # deterministic (spec §4)
    return out
