from pathlib import Path
import numpy as np
from ccloadout.memory import Entry
from ccloadout.ranker import keyword_embed
from ccloadout.recall import build_payload, select

def _e(name: str, desc: str, kind: str = "memory", scope: str = "repo") -> Entry:
    return Entry(id=f"{kind}:{name}", kind=kind, name=name, description=desc,
                 path=Path(f"/store/{name}.md"), scope=scope)

_ON = [_e("ranker-threshold", "how the embedding ranker scores skills and plugins"),
       _e("skill-scoping", "per-session skill pruning through the settings overlay"),
       _e("mcp-connectors", "scoped sessions drop claude.ai mcp connectors")]
_OFF = [_e(f"off-{i}", d) for i, d in enumerate(
    ["sourdough starter hydration ratios", "tuning a nylon string guitar",
     "pruning fruit trees in late winter", "tide tables for the north sea",
     "espresso grind size and extraction", "knitting a raglan sleeve"])]
_GOAL = "goal-aware launcher that prunes skills, plugins and mcp servers for a claude code session"

def test_relevant_entries_are_admitted_before_irrelevant_ones():
    # Acceptance criterion 3: quality, not mechanism. Uses the real (vendored) embedder — the
    # crc32 fallback does not separate these, which is characterised below.
    from ccloadout.ranker import make_model2vec_embed
    embed = make_model2vec_embed("minishlab/potion-base-8M")
    ordered = select(_ON + _OFF, _GOAL, embed, threshold=0.0, budget_tokens=10_000)
    assert {e.name for e in ordered[:3]} == {e.name for e in _ON}

def test_default_threshold_separates_on_topic_from_off_topic_entries():
    from ccloadout.config import DEFAULT_THRESHOLD
    from ccloadout.ranker import make_model2vec_embed
    embed = make_model2vec_embed("minishlab/potion-base-8M")
    kept = select(_ON + _OFF, _GOAL, embed, threshold=DEFAULT_THRESHOLD, budget_tokens=10_000)
    assert {e.name for e in kept} == {e.name for e in _ON}

def test_keyword_fallback_does_not_rank_memories_reliably():
    # Documented, not aspirational: the dependency-free embedder shares tokens across unrelated
    # one-line descriptions, so an off-topic entry outranks on-topic ones. It is fine for the
    # coarse capability pruning it was written for, and unfit for memory recall.
    ordered = select(_ON + _OFF, _GOAL, keyword_embed, threshold=0.0, budget_tokens=10_000)
    assert {e.name for e in ordered[:3]} != {e.name for e in _ON}

def test_budget_truncates_in_rank_order():
    ordered = select(_ON + _OFF, _GOAL, keyword_embed, threshold=0.0, budget_tokens=10_000)
    tight = select(_ON + _OFF, _GOAL, keyword_embed, threshold=0.0, budget_tokens=40)
    assert len(tight) < len(ordered)
    assert [e.name for e in tight] == [e.name for e in ordered[:len(tight)]]

def test_threshold_drops_entries_below_it():
    everything = select(_ON + _OFF, _GOAL, keyword_embed, threshold=0.0, budget_tokens=10_000)
    strict = select(_ON + _OFF, _GOAL, keyword_embed, threshold=0.99, budget_tokens=10_000)
    assert len(strict) < len(everything)

def test_payload_names_the_executable_by_absolute_path():
    text = build_payload(_ON, exe="/opt/venv/bin/cld", total=9)
    assert "/opt/venv/bin/cld recall" in text
    assert " cld recall" not in text.replace("/opt/venv/bin/cld recall", "")

def test_payload_labels_provenance_and_untrusted_status():
    text = build_payload(_ON, exe="/x/cld", total=9)
    assert "untrusted" in text.lower()
    assert "memory · repo" in text
    assert "9 entries" in text and "3" in text

def test_payload_is_empty_when_nothing_was_selected():
    assert build_payload([], exe="/x/cld", total=0) == ""

def test_payload_fits_the_budget_it_was_selected_for():
    from ccloadout.recall import estimate_tokens
    budget = 120
    chosen = select(_ON + _OFF, _GOAL, keyword_embed, threshold=0.0, budget_tokens=budget)
    assert estimate_tokens(build_payload(chosen, exe="/x/cld", total=9)) <= budget

def test_selection_is_empty_for_an_empty_store():
    assert select([], _GOAL, keyword_embed, threshold=0.0, budget_tokens=1000) == []

def test_search_reaches_an_entry_the_index_excluded_on_relevance():
    # Acceptance criterion 4: T2 earns its keep only if it finds what T1 dropped for being
    # off-goal — so search applies no threshold of its own.
    from ccloadout.ranker import make_model2vec_embed
    from ccloadout.recall import search
    embed = make_model2vec_embed("minishlab/potion-base-8M")
    off_goal = _e("guitar-tuning", "tuning a nylon string guitar by ear")
    entries = _ON + [off_goal]
    injected = select(entries, _GOAL, embed, threshold=0.24, budget_tokens=10_000)
    assert off_goal.name not in {e.name for e in injected}          # T1 drops it
    found = search(entries, "how do I tune a guitar", embed, limit=1)
    assert [e.name for e, _ in found] == [off_goal.name]            # T2 still reaches it

def test_recall_command_is_runnable_without_path_luck():
    import subprocess
    from ccloadout.recall import recall_command
    cmd = recall_command()
    assert Path(cmd.split()[0]).is_absolute()
    assert subprocess.run(cmd.split() + ["--version"], capture_output=True).returncode == 0
