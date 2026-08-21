import json
import pytest
from ccloadout import measure

def _list_output():
    return (
        "Checking MCP server health…\n"
        "\n"
        "claude.ai Context7: https://mcp.context7.com/mcp - ✔ Connected\n"
        "claude.ai S&P Global: https://kfinance.example/mcp - ! Needs authentication\n"
        "local-proj: node ./server.mjs --flag - ✔ Connected\n"
        "broken: node ./x.mjs - ✘ Failed to connect — CONNECTION_CLOSED\n"
    )

def test_discover_parses_transport_and_status():
    run = lambda *a, **k: type("P", (), {"stdout": _list_output(), "returncode": 0})()
    servers = measure.discover(run=run)
    by_id = {s.id: s for s in servers}
    assert by_id["claude.ai Context7"].transport == "http"
    assert by_id["local-proj"].transport == "stdio"
    assert by_id["local-proj"].target == "node ./server.mjs --flag"
    assert "Checking MCP server health" not in by_id   # noise line skipped

def test_discover_surfaces_run_failure():
    def _boom(*a, **k):
        raise OSError("claude not found")
    with pytest.raises(measure.MeasureError):
        measure.discover(run=_boom)

def test_unhealthy_status_short_circuits_to_unmeasured(monkeypatch):
    monkeypatch.setattr(measure, "measure_http", lambda *a, **k: pytest.fail("must not connect"))
    s = measure.Server("x", "http", "https://y", "! Needs authentication")
    r = measure.measure_server(s)
    assert r.tokens is None and r.reason == "needs auth"

def test_measure_server_uses_transport(monkeypatch):
    monkeypatch.setattr(measure, "measure_http", lambda url, timeout=15.0: 1234)
    r = measure.measure_server(measure.Server("h", "http", "https://y", "✔ Connected"))
    assert r.tokens == 1234 and r.reason == ""

def test_measure_error_becomes_unmeasured(monkeypatch):
    def _fail(*a, **k):
        raise measure.MeasureError("unreachable (timeout)")
    monkeypatch.setattr(measure, "measure_stdio", _fail)
    r = measure.measure_server(measure.Server("s", "stdio", "node x.mjs", "✔ Connected"))
    assert r.tokens is None and "unreachable" in r.reason

def test_tokens_of_is_positive_and_scales():
    small = measure.tokens_of([{"name": "a", "description": "x"}])
    big = measure.tokens_of([{"name": "a", "description": "x" * 4000}])
    assert small >= 1 and big > small

def test_parse_sse_extracts_json():
    body = "event: message\ndata: {\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{}}\n\n"
    assert measure._parse_sse(body) == {"jsonrpc": "2.0", "id": 2, "result": {}}

def test_parse_sse_matches_requested_id_over_earlier_frames():
    body = ('data: {"jsonrpc":"2.0","method":"notifications/progress"}\n'
            'data: {"jsonrpc":"2.0","id":2,"result":{"tools":[]}}\n')
    assert measure._parse_sse(body, 2)["result"] == {"tools": []}   # skips the progress frame
    assert measure._parse_sse(body, 99) is None                     # no frame answers id 99

def test_cache_roundtrip_skips_unmeasured(tmp_path):
    root = tmp_path / "root"; (root / "loadout").mkdir(parents=True)
    results = [measure.Result("A", 1200, ""), measure.Result("B", None, "needs auth")]
    measure.save_costs(root, results)
    on_disk = json.loads(measure.costs_path(root).read_text())["servers"]
    assert "A" in on_disk and "B" not in on_disk   # never persist an unmeasured server as a number
    assert measure.load_costs(root) == {"A": 1200}

def test_load_costs_absent_is_empty(tmp_path):
    assert measure.load_costs(tmp_path) == {}

def test_connector_costs_excludes_plugin_and_declared_servers():
    cache = {"claude.ai Gmail": 13200, "Proj": 500, "plugin:pw:pw": 4600}
    conns = measure.connector_costs(cache, mcp_ids={"Proj"})
    assert conns == {"claude.ai Gmail": 13200}   # plugin-bundled + declared .claude.json excluded
