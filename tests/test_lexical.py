from pathlib import Path
from ccloadout.lexical import Bm25, rank_lexically, tokenize
from ccloadout.memory import Entry

def _e(name, desc):
    return Entry(id=f"memory:{name}", kind="memory", name=name, description=desc,
                 path=Path(f"/s/{name}.md"), scope="repo")

_ON = [_e("ranker-threshold", "how the embedding ranker scores skills and plugins"),
       _e("skill-scoping", "per-session skill pruning through the settings overlay"),
       _e("mcp-connectors", "scoped sessions drop claude.ai mcp connectors")]
_OFF = [_e(f"off-{i}", d) for i, d in enumerate(
    ["sourdough starter hydration ratios", "tuning a nylon string guitar",
     "pruning fruit trees in late winter", "tide tables for the north sea",
     "espresso grind size and extraction", "knitting a raglan sleeve"])]

def test_stopwords_are_dropped_and_tokens_are_stemmed():
    assert tokenize("How is the MCP server pruned?") == ["mcp", "server", "prun"]
    assert tokenize("skills") == tokenize("skill") and tokenize("pruning") == tokenize("pruned")

def test_a_natural_prompt_finds_the_right_entry():
    ranked = rank_lexically(_ON + _OFF, "how are skills pruned for a session?")
    assert ranked[0][0].name == "skill-scoping"

def test_off_topic_entries_score_zero_rather_than_noise():
    ranked = dict((e.name, s) for e, s in rank_lexically(_ON + _OFF, "mcp connectors"))
    assert ranked["mcp-connectors"] > 0
    assert all(ranked[e.name] == 0 for e in _OFF)      # crc32 hashing scored these; idf does not

def test_shared_words_do_not_beat_the_specific_entry():
    # "pruning" appears in an off-topic entry too; the specific one must still win.
    ranked = rank_lexically(_ON + _OFF, "pruning skills per session")
    assert ranked[0][0].name == "skill-scoping"

def test_an_empty_store_scores_nothing():
    assert rank_lexically([], "anything") == []
    assert Bm25([]).scores("anything") == []

def test_singular_and_plural_forms_meet():
    for one, many in (("process", "processes"), ("class", "classes"), ("cache", "caches"),
                      ("address", "addresses"), ("change", "changes")):
        assert tokenize(one) == tokenize(many), (one, many)

def test_a_plural_query_finds_a_singular_entry():
    entries = [_e("proc", "how the process pool is drained on exit")]
    assert rank_lexically(entries, "draining processes")[0][1] > 0

def test_non_ascii_prompts_are_tokenised():
    assert tokenize("perché il processo è bloccato") != []
    assert tokenize("процесс") != []

def test_idf_and_not_term_frequency_decides_the_winner():
    # Every entry mentions "session"; only one mentions "overlay". Without inverse document
    # frequency the long common term dominates and the specific entry loses.
    common = [_e(f"c{i}", "session session session session handling notes") for i in range(6)]
    specific = _e("specific", "session overlay")
    ranked = rank_lexically(common + [specific], "session overlay")
    assert ranked[0][0].name == "specific"
