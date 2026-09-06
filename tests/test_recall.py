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
    assert "9 entries" in text and f"{len(_ON)} of 9" in text

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

def test_the_command_the_payload_names_actually_returns_entries(tmp_path):
    # Acceptance criterion 6, asserted the way it is worded: run the exact string the session is
    # handed, against a store, and require entries back. Under pytest `sys.argv[0]` is the test
    # runner, so the command is built the way a launch builds it instead of being read off argv.
    import os, shlex, subprocess, sys
    root, repo = tmp_path / "root", tmp_path / "repo"
    mem = repo / "docs" / "memory"; mem.mkdir(parents=True)
    (mem / "note.md").write_text(
        "---\nname: scoping-note\ndescription: how skills are pruned per session\n---\nbody\n")
    (root / "skills").mkdir(parents=True)               # deliberately empty: nothing installed
    command = f"{sys.executable} -m ccloadout"
    done = subprocess.run(shlex.split(command) + ["recall", "how are skills pruned"],
                          cwd=repo, capture_output=True, text=True,
                          env={**os.environ, "CLAUDE_CONFIG_DIR": str(root)})
    assert done.returncode == 0, done.stderr
    assert "scoping-note" in done.stdout

# --- staleness (Slice 2) ------------------------------------------------------

def _anchored(name, desc, anchors, sha=None):
    return Entry(id=f"memory:{name}", kind="memory", name=name, description=desc,
                 path=Path(f"/store/{name}.md"), scope="repo",
                 anchors=tuple(anchors), content_sha=sha)

def test_anchor_state_reports_missing_changed_and_matching(tmp_path):
    from ccloadout.recall import anchor_state, sha_of
    src = tmp_path / "src" / "cli.py"
    src.parent.mkdir(parents=True); src.write_text("print('one')\n")
    assert anchor_state(_anchored("a", "d", []), tmp_path) == "none"
    assert anchor_state(_anchored("a", "d", ["src/nope.py"]), tmp_path) == "missing"
    assert anchor_state(_anchored("a", "d", ["src/cli.py"]), tmp_path) == "unverified"
    recorded = sha_of([src])
    assert anchor_state(_anchored("a", "d", ["src/cli.py"], recorded), tmp_path) == "fresh"
    src.write_text("print('two')\n")                 # the code moved on, the note did not
    assert anchor_state(_anchored("a", "d", ["src/cli.py"], recorded), tmp_path) == "changed"

def test_symbol_anchors_are_checked_at_path_level_only(tmp_path):
    from ccloadout.recall import anchor_state
    (tmp_path / "src").mkdir(); (tmp_path / "src" / "cli.py").write_text("x = 1\n")
    assert anchor_state(_anchored("a", "d", ["src/cli.py#missing_symbol"]), tmp_path) == "unverified"

def test_missing_anchor_demotes_but_changed_sha_does_not(tmp_path):
    from ccloadout.ranker import make_model2vec_embed
    embed = make_model2vec_embed("minishlab/potion-base-8M")
    (tmp_path / "src").mkdir(); (tmp_path / "src" / "cli.py").write_text("x = 1\n")
    live = _anchored("live", "how the embedding ranker scores skills", ["src/cli.py"], "deadbeef")
    gone = _anchored("gone", "how the embedding ranker scores plugins", ["src/nope.py"])
    chosen = select([live, gone], _GOAL, embed, threshold=0.0, budget_tokens=10_000, root=tmp_path)
    assert [e.name for e in chosen][0] == "live"      # the demoted one sinks below its twin

def test_payload_flags_entries_whose_anchor_changed(tmp_path):
    from ccloadout.recall import build_payload
    (tmp_path / "src").mkdir(); (tmp_path / "src" / "cli.py").write_text("x = 1\n")
    changed = _anchored("changed", "a note", ["src/cli.py"], "not-the-current-sha")
    text = build_payload([changed], exe="/x/cld", total=1, root=tmp_path)
    assert "possibly stale" in text

def test_payload_without_a_root_makes_no_staleness_claim(tmp_path):
    from ccloadout.recall import build_payload
    changed = _anchored("changed", "a note", ["src/cli.py"], "sha")
    assert "possibly stale" not in build_payload([changed], exe="/x/cld", total=1)

# --- promotion and decay (Slice 2) -------------------------------------------

def _usage(uses, days_ago=0):
    from datetime import date, timedelta
    from ccloadout.usage import Usage
    return Usage(uses=uses, last_used=(date.today() - timedelta(days=days_ago)).isoformat())

def test_a_promoted_entry_is_admitted_before_better_ranked_ones():
    from ccloadout.ranker import make_model2vec_embed
    embed = make_model2vec_embed("minishlab/potion-base-8M")
    entries = _ON + _OFF
    plain = select(entries, _GOAL, embed, threshold=0.0, budget_tokens=10_000)
    assert plain[0].name != "off-3"
    promoted = select(entries, _GOAL, embed, threshold=0.0, budget_tokens=10_000,
                      usage={"memory:off-3": _usage(5)}, promote_after=3)
    assert promoted[0].name == "off-3"

def test_promotion_cannot_take_more_than_half_the_budget():
    from ccloadout.ranker import make_model2vec_embed
    from ccloadout.recall import build_payload, estimate_tokens
    embed = make_model2vec_embed("minishlab/potion-base-8M")
    entries = _ON + _OFF
    usage = {f"memory:{e.name}": _usage(9) for e in _OFF}      # every off-topic entry promoted
    budget = 260
    chosen = select(entries, _GOAL, embed, threshold=0.0, budget_tokens=budget,
                    usage=usage, promote_after=3)
    pinned = [e for e in chosen if e.name.startswith("off-")]
    assert estimate_tokens(build_payload(pinned, exe="/x/cld", total=len(entries))) <= budget // 2
    assert any(not e.name.startswith("off-") for e in chosen)  # ranked recall still gets in

def test_decay_demotes_an_entry_not_delivered_for_a_long_time():
    from ccloadout.ranker import make_model2vec_embed
    embed = make_model2vec_embed("minishlab/potion-base-8M")
    fresh, stale = _ON[0], _ON[1]
    usage = {f"memory:{stale.name}": _usage(1, days_ago=400)}
    order = select([fresh, stale], _GOAL, embed, threshold=0.0, budget_tokens=10_000,
                   usage=usage, decay_days=90, decay_factor=0.1)
    assert order[-1].name == stale.name

def test_decay_leaves_recently_delivered_entries_alone():
    from ccloadout.ranker import make_model2vec_embed
    embed = make_model2vec_embed("minishlab/potion-base-8M")
    baseline = select(_ON, _GOAL, embed, threshold=0.0, budget_tokens=10_000)
    usage = {f"memory:{e.name}": _usage(1, days_ago=5) for e in _ON}
    assert [e.name for e in select(_ON, _GOAL, embed, threshold=0.0, budget_tokens=10_000,
                                   usage=usage, decay_days=90)] == [e.name for e in baseline]

# --- assessment: why an entry did or did not make it (audit) ------------------

def test_assess_explains_every_entry_not_only_the_admitted_ones():
    from ccloadout.ranker import make_model2vec_embed
    from ccloadout.recall import assess
    embed = make_model2vec_embed("minishlab/potion-base-8M")
    verdicts = assess(_ON + _OFF, _GOAL, embed, threshold=0.24, budget_tokens=10_000)
    assert len(verdicts) == len(_ON) + len(_OFF)
    by_name = {v.entry.name: v for v in verdicts}
    assert all(by_name[e.name].admitted for e in _ON)
    assert all("below-threshold" in by_name[e.name].reasons for e in _OFF)
    assert by_name[_ON[0].name].base > 0            # the raw relevance is kept for the audit view

def test_assess_names_the_budget_as_the_reason(tmp_path):
    from ccloadout.ranker import make_model2vec_embed
    from ccloadout.recall import assess
    embed = make_model2vec_embed("minishlab/potion-base-8M")
    verdicts = assess(_ON, _GOAL, embed, threshold=0.0, budget_tokens=130)
    assert any("over-budget" in v.reasons for v in verdicts if not v.admitted)

def test_assess_reports_resolved_and_flagged_entries(tmp_path):
    from ccloadout.flags import Flag
    from ccloadout.ranker import make_model2vec_embed
    from ccloadout.recall import assess
    embed = make_model2vec_embed("minishlab/potion-base-8M")
    done = Entry(id="debt:done", kind="debt", name="done", description="a resolved shim",
                 path=Path("/s/done.md"), scope="repo", status="resolved")
    flagged = _ON[0]
    verdicts = {v.entry.name: v for v in assess(
        _ON + [done], _GOAL, embed, threshold=0.0, budget_tokens=10_000,
        flags={flagged.id: Flag(reason="says the fix is where it no longer is", at="2026-09-06")})}
    assert verdicts["done"].reasons == ("resolved",) and not verdicts["done"].admitted
    assert "flagged" in verdicts[flagged.name].reasons
    assert verdicts[flagged.name].score < verdicts[flagged.name].base   # flagging demotes

def test_an_anchor_outside_the_repository_is_treated_as_missing(tmp_path):
    from ccloadout.recall import anchor_state
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "cli.py").write_text("x = 1\n")
    for escape in ("/etc/passwd", "../../etc/passwd"):
        assert anchor_state(_anchored("a", "d", [escape]), tmp_path) == "missing"
    assert anchor_state(_anchored("a", "d", ["src/cli.py"]), tmp_path) == "unverified"
