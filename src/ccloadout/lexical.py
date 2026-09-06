from __future__ import annotations
import math, re
from typing import Iterable, Sequence

# A dependency-free scorer for the prompt hook, where loading the embedding model (~520 ms
# measured) blows the latency budget. BM25 with IDF, not the crc32 bag-of-words of `ranker`:
# hashing collides and weights every token alike, which is why that fallback cannot rank memories.
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset("""a an and are as at be by for from has have how i in is it its of on or that
the this to was what when where which who why with you your""".split())
_K1, _B = 1.5, 0.75

# Crude suffix stripping, not linguistics: a prompt says "how are skills pruned", the entry says
# "per-session skill pruning". Without it the two never meet on a shared term.
_SUFFIXES = ("ing", "ed", "es", "s")

def _stem(token: str) -> str:
    for suffix in _SUFFIXES:
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token

def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in _TOKEN.findall(text.lower())
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
