"""Tools installed after a repo was seeded: the launch names them, `update` asks about them."""
import json
import tomllib
import ccloadout.cli as cli


class _RC:
    def __init__(self, code): self.returncode = code
    stdout = ""


def _seeded_repo(tmp_path, monkeypatch):
    # A profile with one MCP server and one plugin, and a repo seeded with rules for both.
    root = tmp_path / "root"; root.mkdir()
    (root / "settings.json").write_text('{"enabledPlugins": {"figma@x": true}}')
    (root / ".claude.json").write_text('{"mcpServers": {"Gmail": {"command": "g"}}}')
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.setattr(cli, "make_model2vec_embed", lambda name: cli.keyword_embed)
    repos = tmp_path / "repos"; r1 = repos / "proj1"; r1.mkdir(parents=True)
    cli.main(["init", str(repos), "--yes"])
    return root, r1


def _add_plugin(root, plugin_id):
    # Install a plugin after the repo was seeded: init wrote no rule for it.
    path = root / "settings.json"
    data = json.loads(path.read_text())
    data["enabledPlugins"][plugin_id] = True
    path.write_text(json.dumps(data))


def _fake_stdin(monkeypatch, *, tty):
    monkeypatch.setattr(cli.sys, "stdin", type("S", (), {"isatty": lambda self: tty})())


def _launch_seeded(monkeypatch, r1):
    monkeypatch.chdir(r1)
    monkeypatch.setattr(cli, "_interactive", lambda p: True)
    monkeypatch.setattr(cli, "_LAUNCH_PAUSE_S", 0)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: _RC(0))
    return cli.main([])


def _actions(path):
    if not path.exists():
        return {}
    return {x["target"]: x["predicate"]["action"]
            for x in tomllib.loads(path.read_text()).get("rule", [])}


def test_launch_seeded_names_tools_added_after_seeding(tmp_path, monkeypatch, capsys):
    root, r1 = _seeded_repo(tmp_path, monkeypatch)
    _add_plugin(root, "newbie@x")
    capsys.readouterr()
    assert _launch_seeded(monkeypatch, r1) == 0
    err = capsys.readouterr().err
    assert "1 new tool since this repo was seeded" in err
    assert "newbie (" in err                                      # with the automatic keep/drop
    assert "claude-loadout update" in err                         # how to decide
    assert "Gmail" not in err and "figma" not in err              # seeded tools are not new


def test_launch_seeded_without_new_tools_names_none(tmp_path, monkeypatch, capsys):
    _root, r1 = _seeded_repo(tmp_path, monkeypatch)
    capsys.readouterr()
    assert _launch_seeded(monkeypatch, r1) == 0
    assert "new tool" not in capsys.readouterr().err


def test_launch_new_tool_with_profile_rule_is_not_new(tmp_path, monkeypatch, capsys):
    root, r1 = _seeded_repo(tmp_path, monkeypatch)
    _add_plugin(root, "newbie@x")
    (root / "loadout").mkdir(exist_ok=True)
    (root / "loadout" / "rules.toml").write_text(
        '[[rule]]\ntarget = "newbie@x"\nnl = ""\n[rule.predicate]\n'
        'action = "always_drop"\nmatch = []\nmatch_mode = "any"\n')
    capsys.readouterr()
    assert _launch_seeded(monkeypatch, r1) == 0
    assert "new tool" not in capsys.readouterr().err              # a profile rule already decides it


def test_launch_unseeded_repo_names_no_new_tools(tmp_path, monkeypatch, capsys):
    root, r1 = _seeded_repo(tmp_path, monkeypatch)
    other = tmp_path / "other"; other.mkdir()                     # never seeded: everything is ranked
    capsys.readouterr()
    assert _launch_seeded(monkeypatch, other) == 0
    assert "new tool" not in capsys.readouterr().err


def test_explain_lists_tools_added_after_seeding(tmp_path, monkeypatch, capsys):
    root, r1 = _seeded_repo(tmp_path, monkeypatch)
    _add_plugin(root, "newbie@x")
    monkeypatch.chdir(r1)
    capsys.readouterr()
    assert cli.main(["--explain"]) == 0
    out = capsys.readouterr().out
    assert "new since seeding (1)" in out.lower() and "newbie@x" in out


def _update_with_new_tool(tmp_path, monkeypatch, scope, picked_new):
    # Seed, install newbie@x, run an interactive update. The first review shows the new tools only.
    root, r1 = _seeded_repo(tmp_path, monkeypatch)
    _add_plugin(root, "newbie@x")
    monkeypatch.chdir(r1)
    _fake_stdin(monkeypatch, tty=True)
    monkeypatch.setattr(cli, "_discover_profiles", lambda active: [active])
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    reviews = []
    def _stub(repo, prunable, kept, label=""):
        reviews.append(({i.id for i in prunable}, set(kept)))
        return set(picked_new) if len(reviews) == 1 else set(kept)
    monkeypatch.setattr(cli, "_review_keep_drop", _stub)
    monkeypatch.setattr(cli, "_review_renders", lambda prunable: True)
    monkeypatch.setattr(cli, "_ask_new_tool_scope", lambda: scope)
    assert cli.main(["update"]) == 0
    return root, r1, reviews


def test_update_asks_new_tools_first_and_saves_to_repo(tmp_path, monkeypatch):
    root, r1, reviews = _update_with_new_tool(tmp_path, monkeypatch, "repo", {"newbie@x"})
    assert reviews[0][0] == {"newbie@x"}                          # first review: new tools only
    assert "newbie@x" in reviews[1][1]                            # full review preset carries the answer
    assert _actions(r1 / ".loadout" / "rules.toml")["newbie@x"] == "always_keep"
    assert "newbie@x" not in _actions(root / "loadout" / "rules.toml")


def test_update_new_tool_answer_can_apply_to_the_profile(tmp_path, monkeypatch):
    root, r1, reviews = _update_with_new_tool(tmp_path, monkeypatch, "profile", set())
    assert _actions(root / "loadout" / "rules.toml") == {"newbie@x": "always_drop"}
    assert "newbie@x" not in reviews[1][1]
    assert "newbie@x" not in _actions(r1 / ".loadout" / "rules.toml")   # the profile rule decides it


def test_update_quit_on_new_tools_skips_the_repo(tmp_path, monkeypatch):
    root, r1 = _seeded_repo(tmp_path, monkeypatch)
    before = (r1 / ".loadout" / "rules.toml").read_text()
    _add_plugin(root, "newbie@x")
    monkeypatch.chdir(r1)
    _fake_stdin(monkeypatch, tty=True)
    monkeypatch.setattr(cli, "_discover_profiles", lambda active: [active])
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    monkeypatch.setattr(cli, "_review_keep_drop", lambda repo, prunable, kept, label="": None)
    monkeypatch.setattr(cli, "_review_renders", lambda prunable: True)
    assert cli.main(["update"]) == 0
    assert (r1 / ".loadout" / "rules.toml").read_text() == before


def test_update_never_asks_scope_when_the_review_cannot_render(tmp_path, monkeypatch):
    # Regression: the picker fell back to the ranking silently, then still asked where to apply it,
    # so `p` turned unseen guesses into profile-wide rules.
    root, r1 = _seeded_repo(tmp_path, monkeypatch)
    _add_plugin(root, "newbie@x")
    monkeypatch.chdir(r1)
    _fake_stdin(monkeypatch, tty=True)
    monkeypatch.setattr(cli, "_discover_profiles", lambda active: [active])
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    monkeypatch.setattr(cli, "_can_raw", lambda: False)
    monkeypatch.setattr(cli._t, "accessible", lambda: False)
    def _boom():
        raise AssertionError("asked where to apply choices the user never saw")
    monkeypatch.setattr(cli, "_ask_new_tool_scope", _boom)
    assert cli.main(["update"]) == 0
    assert "newbie@x" not in _actions(root / "loadout" / "rules.toml")
    assert "newbie@x" in _actions(r1 / ".loadout" / "rules.toml")   # ranking frozen for this repo only


def test_update_yes_skips_the_new_tool_step(tmp_path, monkeypatch):
    root, r1 = _seeded_repo(tmp_path, monkeypatch)
    _add_plugin(root, "newbie@x")
    monkeypatch.chdir(r1)
    def _boom():
        raise AssertionError("--yes must not ask")
    monkeypatch.setattr(cli, "_ask_new_tool_scope", _boom)
    assert cli.main(["update", "--yes"]) == 0
    assert "newbie@x" in _actions(r1 / ".loadout" / "rules.toml")   # automatic decision frozen
