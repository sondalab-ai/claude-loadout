import numpy as np
from smartctx.inventory import Item
from smartctx.ranker import Ranker, keyword_embed, make_model2vec_embed, bundled_model_path

def test_default_model_is_vendored_and_embeds_offline():
    # The default model ships inside the package: a fresh install ranks with no download.
    path = bundled_model_path()
    assert path.is_dir() and (path / "model.safetensors").is_file()
    embed = make_model2vec_embed("minishlab/potion-base-8M")   # resolves to the vendored copy
    vecs = embed(["astro imaging", "gmail email"])
    assert vecs.shape[0] == 2 and vecs.shape[1] > 0

def test_keyword_embed_is_deterministic():
    texts = ["astro sky imaging goal", "Gmail email server"]
    a = keyword_embed(texts)
    b = keyword_embed(texts)
    assert np.array_equal(a, b)                          # crc32, not PYTHONHASHSEED-randomized hash

def _stub_embed(texts):
    # deterministic: map keyword -> orthogonal-ish vectors
    table = {"astro": [1, 0, 0], "gmail": [0, 1, 0], "goal": [1, 0.2, 0]}
    return np.array([table.get(next((k for k in table if k in t.lower()), "goal"),
                              [0, 0, 1]) for t in texts], dtype=float)

def test_keeps_relevant_drops_irrelevant():
    items = [Item("astro", "skill", "astro", "astro sky imaging"),
             Item("Gmail", "mcp", "Gmail", "gmail email")]
    r = Ranker(embed=_stub_embed)
    sel = r.rank(goal="astro imaging goal", items=items, threshold=0.5, always_keep=())
    kept = {i.id for i in sel.kept}
    assert "astro" in kept and "Gmail" not in kept

def test_always_keep_glob_survives_low_score():
    items = [Item("Gmail", "mcp", "Gmail", "gmail email")]
    r = Ranker(embed=_stub_embed)
    sel = r.rank(goal="astro", items=items, threshold=0.99, always_keep=("Gm*",))
    assert {i.id for i in sel.kept} == {"Gmail"}
