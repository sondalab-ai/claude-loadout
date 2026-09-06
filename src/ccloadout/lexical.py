from __future__ import annotations
import math, re
from typing import Iterable, Sequence

# A dependency-free scorer for the prompt hook, where loading the embedding model (~520 ms
# measured) blows the latency budget. BM25 with IDF, not the crc32 bag-of-words of `ranker`:
# hashing collides and weights every token alike, which is why that fallback cannot rank memories.
_TOKEN = re.compile(r"\w+", re.UNICODE)
_STOP = frozenset("""a an and are as at be by for from has have how i in is it its of on or that
the this to was what when where which who why with you your""".split())
_K1, _B = 1.5, 0.75

# Crude suffix stripping, not linguistics: a prompt says "how are skills pruned", the entry says
# "per-session skill pruning". Without it the two never meet on a shared term.
_SUFFIXES = ("ing", "ed", "s")
_ES_AFTER = ("s", "x", "z", "ch", "sh")             # only these take "-es": boxes, matches, buses

def _stem(token: str) -> str:
    # `es` first, and only where English actually adds it, so `processes` -> `process` while
    # `process` is left alone. Stripping it blindly gave `proces` vs `process`: two forms of the
    # same word that could never match each other.
    if len(token) > 4 and token.endswith("es") and token[:-2].endswith(_ES_AFTER):
        return token[:-2]
    for suffix in _SUFFIXES:
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            stem = token[: -len(suffix)]
            if suffix == "s" and stem.endswith("s"):    # `class` must not become `clas`
                return token
            return _drop_final_e(stem)
    return _drop_final_e(token)

def _drop_final_e(token: str) -> str:
    # The last thing that keeps pairs apart: `caches` loses `es` to the rule above and `cache`
    # keeps its `e`. Dropping a trailing `e` from both is over-stemming, and it is consistent —
    # which is all BM25 needs, since the stem is only ever compared with other stems.
    return token[:-1] if len(token) > 4 and token.endswith("e") else token

def tokenize(text: str) -> list[str]:
    # casefold, not lower: the prompt hook is the only path that sees free text, and this project's
    # user writes in Italian as often as English.
    return [_stem(t) for t in _TOKEN.findall(text.casefold())
            if len(t) > 1 and t not in _STOP]

class Bm25:
    def __init__(self, documents: Sequence[str]):
        self._docs = [tokenize(d) for d in documents]
        self._len = [len(d) for d in self._docs]
        self._avg = (sum(self._len) / len(self._len)) if self._docs else 0.0
        self._df: dict[str, int] = {}
        for doc in self._docs:
            for term in set(doc):
                self._df[term] = self._df.get(term, 0) + 1

    def _idf(self, term: str) -> float:
        n = len(self._docs)
        df = self._df.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def scores(self, query: str) -> list[float]:
        terms = tokenize(query)
        out = []
        for doc, length in zip(self._docs, self._len):
            if not doc:
                out.append(0.0); continue
            norm = _K1 * (1 - _B + _B * (length / self._avg if self._avg else 1.0))
            total = 0.0
            for term in terms:
                tf = doc.count(term)
                if tf:
                    total += self._idf(term) * tf / (tf + norm)
            out.append(total)
        return out

def rank_lexically(entries: Iterable, query: str) -> list[tuple[object, float]]:
    items = list(entries)
    scores = Bm25([f"{e.name} {e.description}" for e in items]).scores(query)
    return sorted(zip(items, scores), key=lambda pair: -pair[1])
