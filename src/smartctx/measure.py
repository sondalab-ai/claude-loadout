from __future__ import annotations
import json, os, re, selectors, shlex, subprocess, time, urllib.error, urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

# On-demand, opt-in measurement of an MCP server's real tool set. smartctx speaks the
# MCP JSON-RPC handshake itself (initialize -> tools/list), serialises the tool
# definitions the way a client sees them, and estimates tokens with a ~4 chars/token
# heuristic — labelled as such, since it is not Claude's own tokenizer. Anything that
# needs auth we don't hold, hangs, or errors is reported "unmeasured", never zero.

_PROTOCOL = "2024-11-05"
_CLIENT = {"name": "smartctx", "version": "0"}
CHARS_PER_TOKEN = 4                              # rough; the label everywhere says "heuristic"

class MeasureError(Exception):
    """Handshake could not complete; carries a short human reason."""

class Server(NamedTuple):
    id: str                                     # name as `claude mcp list` reports it
    transport: str                              # "http" | "stdio"
    target: str                                 # url (http) or command string (stdio)
    status: str                                 # health line from `claude mcp list`

@dataclass(frozen=True)
class Result:
    id: str
    tokens: int | None                          # None => unmeasured
    reason: str                                 # "" when measured, else why not
    method: str = "heuristic ~4 chars/token"

def tokens_of(tools: list) -> int:
    blob = json.dumps(tools, separators=(",", ":"), ensure_ascii=False)
    return max(1, len(blob) // CHARS_PER_TOKEN)

# ---- discovery -------------------------------------------------------------------

_LIST_LINE = re.compile(r"^(?P<name>.+?): (?P<target>.+?) - (?P<status>.+)$")

def discover(run=subprocess.run) -> list[Server]:
    # `claude mcp list` is the only source that also sees claude.ai connectors (they
    # live outside .claude.json). Parsing is best-effort: unrecognised lines are skipped.
    try:
        proc = run(["claude", "mcp", "list"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MeasureError(f"could not run `claude mcp list` ({exc})")
    servers: list[Server] = []
    for line in (proc.stdout or "").splitlines():
        m = _LIST_LINE.match(line.strip())
        if not m:
            continue
        target = m["target"].strip()
        transport = "http" if target.startswith(("http://", "https://")) else "stdio"
        servers.append(Server(m["name"].strip(), transport, target, m["status"].strip()))
    return servers

def _unhealthy_reason(status: str) -> str | None:
    low = status.lower()
    if "auth" in low:
        return "needs auth"
    if "fail" in low or "pending" in low or "not connected" in low:
        return "not connected"
    return None

# ---- transports ------------------------------------------------------------------

def _parse_sse(body: str, want_id: int | None = None) -> dict | None:
    # A stream can carry progress notifications before the result frame; when we know
    # the request id, return the frame that answers it, not merely the first one.
    first = None
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if want_id is None or obj.get("id") == want_id:
            return obj
        first = first or obj
    return first if want_id is None else None

def _http_rpc(url: str, payload: dict, session: str | None, timeout: float) -> tuple[dict | None, str | None]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    req.add_header("MCP-Protocol-Version", _PROTOCOL)
    if session:
        req.add_header("Mcp-Session-Id", session)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        sid = r.headers.get("Mcp-Session-Id") or session
        ctype = r.headers.get("Content-Type", "")
        body = r.read().decode("utf-8", "ignore")
    obj = _parse_sse(body, payload.get("id")) if "text/event-stream" in ctype \
        else (json.loads(body) if body.strip() else None)
    return obj, sid

def measure_http(url: str, timeout: float = 15.0) -> int:
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": _PROTOCOL, "capabilities": {}, "clientInfo": _CLIENT}}
    try:
        resp, sid = _http_rpc(url, init, None, timeout)
        _http_rpc(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid, timeout)
        resp, _ = _http_rpc(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, sid, timeout)
    except urllib.error.HTTPError as exc:
        raise MeasureError("needs auth" if exc.code in (401, 403) else f"http {exc.code}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise MeasureError(f"unreachable ({exc})")
    tools = ((resp or {}).get("result") or {}).get("tools")
    if tools is None:
        raise MeasureError("no tools/list result")
    return tokens_of(tools)

def _stdio_read(proc: subprocess.Popen, want_id: int, deadline: float) -> dict:
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    while time.monotonic() < deadline:
        if not sel.select(timeout=max(0.0, deadline - time.monotonic())):
            continue
        line = proc.stdout.readline()
        if not line:
            raise MeasureError("server closed the connection")
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:            # some servers log to stdout; ignore noise
            continue
        if obj.get("id") == want_id:
            return obj
    raise MeasureError("timed out")

def measure_stdio(argv: list[str], env: dict | None, timeout: float = 15.0) -> int:
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True,
                                env={**os.environ, **(env or {})})
    except OSError as exc:
        raise MeasureError(f"could not start ({exc})")
    deadline = time.monotonic() + timeout
    try:
        def send(msg):
            proc.stdin.write(json.dumps(msg) + "\n"); proc.stdin.flush()
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": _PROTOCOL, "capabilities": {}, "clientInfo": _CLIENT}})
        _stdio_read(proc, 1, deadline)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        resp = _stdio_read(proc, 2, deadline)
    except (OSError, BrokenPipeError) as exc:
        raise MeasureError(f"i/o error ({exc})")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    tools = ((resp or {}).get("result") or {}).get("tools")
    if tools is None:
        raise MeasureError("no tools/list result")
    return tokens_of(tools)

# ---- orchestration + cache -------------------------------------------------------

def measure_server(s: Server, timeout: float = 15.0) -> Result:
    reason = _unhealthy_reason(s.status)
    if reason:
        return Result(s.id, None, reason)
    try:
        tokens = measure_http(s.target, timeout) if s.transport == "http" \
            else measure_stdio(shlex.split(s.target), None, timeout)
        return Result(s.id, tokens, "")
    except MeasureError as exc:
        return Result(s.id, None, str(exc))

def costs_path(config_root: Path) -> Path:
    return config_root / "smartctx" / "costs.json"

def load_costs(config_root: Path) -> dict[str, int]:
    # id -> measured tokens, for savings to prefer over the per-kind constant.
    try:
        data = json.loads(costs_path(config_root).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: int(v["tokens"]) for k, v in (data.get("servers") or {}).items()
            if isinstance(v, dict) and isinstance(v.get("tokens"), int)}

def connector_costs(cache: dict[str, int], mcp_ids) -> dict[str, int]:
    # claude.ai account connectors: measured servers that scoped sessions drop unconditionally
    # (via --strict-mcp-config) and that smartctx can't keep selectively. They are neither
    # plugin-bundled (`plugin:` prefix) nor declared in .claude.json/.mcp.json (mcp_ids).
    ids = set(mcp_ids)
    return {k: v for k, v in cache.items() if not k.startswith("plugin:") and k not in ids}

def save_costs(config_root: Path, results: list[Result]) -> None:
    path = costs_path(config_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    servers = {}
    for r in results:
        if r.tokens is not None:                # never cache an unmeasured server as a number
            servers[r.id] = {"tokens": r.tokens, "method": r.method, "measured_at": now}
    path.write_text(json.dumps({"servers": servers}, indent=2))
