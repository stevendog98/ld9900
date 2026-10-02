#!/usr/bin/env python3
"""
ld9900 dash - web dashboard + HTTP API for the LD9900 display (stdlib only).

One process owns the display. It serves:
  *  a dashboard        http://HOST:PORT/            live preview, modes, faces, glyph palette
  *  a REST API         http://HOST:PORT/api/...     for other services
  *  live events (SSE)  http://HOST:PORT/api/events  display contents + state as they change
  *  the JSON-lines socket used by `ld9900 stream send` (default 127.0.0.1:7440)

Run
  ld9900 dash                        # USB display, dashboard on http://127.0.0.1:8099
  ld9900 dash --sim                  # no hardware: preview only (and terminal render)
  ld9900 dash --host 0.0.0.0 --token SECRET     # expose on the LAN with a bearer token

API (all responses JSON; with --token send "Authorization: Bearer SECRET" or ?token=SECRET)
  GET  /api                       endpoint index
  GET  /api/state                 mode, face, brightness, the 40 cell codes
  GET  /api/meta                  modes, expressions, reactions, glyph names/codes
  GET  /api/font                  5x7 bitmaps for codes 0..255 (for client-side rendering)
  GET  /api/events                Server-Sent Events: {"state": ...} on every change
  GET  /api/ambient               ambient mode status, provider errors, current cards
  POST /api/op                    one op object, a list of ops, or {"ops":[...]}
  POST /api/<op>                  body = op fields, e.g. POST /api/react {"name":"love"}
  GET  /api/<op>?k=v              same via query string, e.g. /api/say?text=hello&expr=happy
  GET  /api/<op>/<value>          shorthand: /api/react/laugh  /api/face/happy  /api/mode/list
                                  /api/bright/2  /api/text/hello%20world

Ops: text, mode, header, put, clear, bar, spark, face, react, say, bright, idle, ambient, card
(see ld9900 stream for fields; /api/meta lists valid expressions and reactions).
"""
import argparse
import json
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import face as ldface
from . import glyphs as G
from . import sim as ldsim
from . import stream as S

MAX_BODY = 64 * 1024


# ============================================================== engine plumbing
class Tap:
    """Backend wrapper: forwards to the real backend and wakes event listeners on change."""

    def __init__(self, inner):
        self.inner = inner
        self.cond = threading.Condition()
        self.version = 0

    def send(self, data):
        self.inner.send(data)

    def bright(self, level):
        self.inner.bright(level)

    def show(self, buf):
        self.inner.show(buf)
        self.poke()

    def poke(self):
        with self.cond:
            self.version += 1
            self.cond.notify_all()

    def close(self):
        self.inner.close()


def engine_thread(engine, tap):
    def run():
        last_state = None
        while engine.running:
            try:
                engine.step(time.monotonic())
                st = json.dumps({k: v for k, v in engine.state().items() if k != "cells"})
                if st != last_state:            # mode/face changes with no visible change
                    last_state = st
                    tap.poke()
            except Exception as ex:
                print(f"engine error: {ex!r}", file=sys.stderr)
            time.sleep(1.0 / S.FPS)
    t = threading.Thread(target=run, daemon=True, name="engine")
    t.start()
    return t


def socket_server(engine, hostport):
    host, port = hostport.rsplit(":", 1)

    class H(socketserver.StreamRequestHandler):
        def handle(self):
            for line in self.rfile:
                engine.submit(line.decode("utf-8", "replace"))

    class TS(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    srv = TS((host, int(port)), H)
    threading.Thread(target=srv.serve_forever, daemon=True, name="jsonl").start()
    return srv


# ============================================================== validation
MODES = ("marquee", "list", "free")


class Bad(ValueError):
    pass


def _int(o, k, lo, hi, default=None):
    v = o.get(k, default)
    if v is None:
        raise Bad(f"'{k}' is required")
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise Bad(f"'{k}' must be an integer")
    if not lo <= v <= hi:
        raise Bad(f"'{k}' must be {lo}..{hi}")
    return v


def _num(o, k, lo, hi, default=None):
    v = o.get(k, default)
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise Bad(f"'{k}' must be a number")
    if not lo <= v <= hi:
        raise Bad(f"'{k}' must be {lo}..{hi}")
    return v


def _str(o, k, maxlen=500, required=True):
    v = o.get(k)
    if v is None:
        if required:
            raise Bad(f"'{k}' is required")
        return None
    if not isinstance(v, str):
        v = str(v)
    if len(v) > maxlen:
        raise Bad(f"'{k}' longer than {maxlen}")
    return v


def _bool(v):
    if isinstance(v, str):
        return v.lower() in ("1", "true", "yes", "on")
    return bool(v)


def validate(o):
    """Return a clean op dict or raise Bad."""
    if not isinstance(o, dict):
        raise Bad("op must be an object")
    k = o.get("op")
    out = {"op": k}
    if k == "text":
        out["text"] = _str(o, "text", 2000)
    elif k == "header":
        out["text"] = _str(o, "text", 200)
    elif k == "mode":
        m = o.get("mode")
        if m not in MODES:
            raise Bad(f"mode must be one of {MODES}")
        out["mode"] = m
        if "row" in o:
            out["row"] = _int(o, "row", 0, 1)
        sp = _num(o, "speed", 0.5, 60)
        if sp is not None:
            out["speed"] = sp
        h = _num(o, "hold", 0, 60)
        if h is not None:
            out["hold"] = h
        if "header" in o:
            out["header"] = _str(o, "header", 200)
        if "loop" in o:
            out["loop"] = _bool(o["loop"])
    elif k == "put":
        out.update(row=_int(o, "row", 0, 1, 0), col=_int(o, "col", 0, 19, 0), text=_str(o, "text", 200))
    elif k == "clear":
        pass
    elif k == "bar":
        out.update(row=_int(o, "row", 0, 1, 0), col=_int(o, "col", 0, 19, 0),
                   width=_int(o, "width", 1, 20, 10), value=_num(o, "value", 0, 1, 0))
    elif k == "spark":
        vals = o.get("values")
        if isinstance(vals, str):
            vals = [x for x in vals.split(",") if x.strip()]
        try:
            vals = [float(v) for v in (vals or [])]
        except (TypeError, ValueError):
            raise Bad("'values' must be numbers")
        if not 1 <= len(vals) <= 20:
            raise Bad("'values' needs 1..20 numbers")
        out.update(row=_int(o, "row", 0, 1, 0), col=_int(o, "col", 0, 19, 0), values=vals)
        for b in ("lo", "hi"):
            if o.get(b) is not None:
                out[b] = _num(o, b, -1e12, 1e12)
    elif k == "face":
        if _bool(o.get("off", False)):
            out["off"] = True
        else:
            e = o.get("expr")
            if e is not None and e not in ldface.EXPRESSIONS:
                raise Bad(f"unknown expr '{e}' (see /api/meta)")
            if e:
                out["expr"] = e
            if o.get("x") is not None:
                out["x"] = _int(o, "x", 0, 12)
    elif k == "react":
        n = o.get("name")
        if n not in ldface.REACTIONS:
            raise Bad(f"unknown reaction '{n}' (see /api/meta)")
        out["name"] = n
    elif k == "say":
        out["text"] = _str(o, "text", 300)
        e = o.get("expr")
        if e is not None and e not in ldface.EXPRESSIONS:
            raise Bad(f"unknown expr '{e}'")
        if e:
            out["expr"] = e
        h = _num(o, "hold", 0, 60)
        if h is not None:
            out["hold"] = h
    elif k == "bright":
        out["level"] = _int(o, "level", 1, 4)
    elif k == "idle":
        out["on"] = _bool(o.get("on", True))
    elif k == "ambient":
        if "on" in o:
            out["on"] = _bool(o["on"])
        if "enabled" in o:
            out["enabled"] = _bool(o["enabled"])
        if o.get("idle_after") is not None:
            out["idle_after"] = _num(o, "idle_after", 0, 86400)
    elif k == "card":
        out["id"] = _str(o, "id", 40)
        out["title"] = _str(o, "title", 60, required=False) or ""
        out["text"] = _str(o, "text", 500, required=False) or ""
        if o.get("lines") is not None:
            ln = o["lines"]
            if not (isinstance(ln, list) and len(ln) == 2 and all(isinstance(x, str) and len(x) <= 60 for x in ln)):
                raise Bad("'lines' must be 2 strings")
            out["lines"] = ln
        e = o.get("mood")
        if e is not None and e not in ldface.EXPRESSIONS:
            raise Bad(f"unknown mood '{e}'")
        if e:
            out["mood"] = e
        out["ttl"] = _num(o, "ttl", 1, 7 * 86400, 600)
        sec = _num(o, "seconds", 1, 120)
        if sec is not None:
            out["seconds"] = sec
    else:
        raise Bad(f"unknown op '{k}'")
    return out


# shorthand /api/<op>/<value> -> field name
SHORT = {"react": "name", "face": "expr", "mode": "mode", "bright": "level", "text": "text",
         "say": "text", "header": "text", "idle": "on", "ambient": "on"}


def _coerce(v):
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return v


# ============================================================== meta / font
def build_meta():
    exprs = {}
    for name in ldface.EXPRESSIONS:
        buf = bytearray(b" " * 40)
        ldface.draw(buf, name, x=0)
        exprs[name] = list(buf[0:8]) + list(buf[20:28])
    groups = {"eyes": [], "mouths": [], "accessories": [], "ui": []}
    acc = {"blush", "tear", "sweat", "anger", "z_small", "z_big", "heart", "heart_small", "sparkle",
           "sparkle_small", "exclaim", "question", "note", "bulb", "puff", "dots"}
    for name, codes in G.PIECES.items():
        g = ("eyes" if name.startswith("eye") else "mouths" if name.startswith("mouth")
             else "accessories" if name in acc else "ui")
        groups[g].append({"name": name, "codes": codes})
    return {
        "modes": list(MODES),
        "expressions": exprs,
        "reactions": ldface.REACTIONS,
        "pieces": groups,
        "page": G.PAGE,
    }


def build_font():
    rows = []
    for c in range(256):
        r = ldsim.cell_rows(c)
        rows.append([int(line[::-1].replace("#", "1").replace(".", "0"), 2) for line in r])
    return {"rows": rows, "note": "rows[code][y] = 5-bit mask, bit0 = leftmost dot"}


# ============================================================== HTTP
class Api(BaseHTTPRequestHandler):
    server_version = "lddash/1.0"
    engine = None
    tap = None
    token = None
    cors = False
    meta = None
    font = None

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/events"):
            return
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # ---------- helpers
    def _send(self, code, obj, ctype="application/json"):
        body = obj if isinstance(obj, (bytes, bytearray)) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _authed(self, q):
        if not self.token:
            return True
        h = self.headers.get("Authorization", "")
        if h == f"Bearer {self.token}":
            return True
        return q.get("token", [None])[0] == self.token

    def _submit(self, ops):
        clean = [validate(o) for o in ops]          # all-or-nothing
        for o in clean:
            self.engine.submit(json.dumps(o))
        return clean

    # ---------- verbs
    def do_OPTIONS(self):
        self.send_response(204)
        if self.cors:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._authed(q):
            return self._send(401, {"error": "unauthorized"})
        p = u.path.rstrip("/") or "/"
        if p == "/":
            return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        if p == "/api":
            return self._send(200, {"endpoints": __doc__.split("API (")[1].split("Ops:")[0].strip()})
        if p == "/api/state":
            return self._send(200, self.engine.state())
        if p == "/api/meta":
            return self._send(200, self.meta)
        if p == "/api/font":
            return self._send(200, self.font)
        if p == "/api/events":
            return self._events()
        if p == "/api/ambient":
            amb = self.engine.ambient
            if amb is None:
                return self._send(200, {"attached": False})
            return self._send(200, {"attached": True, "state": self.engine.state()["ambient"],
                                    "providers": amb.status(),
                                    "cards": [c.to_json() for c in amb.all_cards()]})
        if p.startswith("/api/"):
            parts = [unquote(x) for x in p[5:].split("/")]
            o = {"op": parts[0]}
            if len(parts) > 1 and parts[0] in SHORT:
                o[SHORT[parts[0]]] = _coerce(parts[1]) if parts[0] in ("bright", "idle", "ambient") else parts[1]
            for k, v in q.items():
                if k != "token":
                    o[k] = _coerce(v[0]) if k not in ("text", "header", "expr", "name", "mode") else v[0]
            try:
                return self._send(200, {"ok": True, "ops": self._submit([o])})
            except Bad as ex:
                return self._send(400, {"ok": False, "error": str(ex)})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._authed(q):
            return self._send(401, {"error": "unauthorized"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return self._send(413, {"error": "body too large"})
        raw = self.rfile.read(n) if n else b""
        try:
            body = json.loads(raw) if raw.strip() else {}
        except ValueError:
            # plain-text body: treat as text for the current mode
            body = {"op": "text", "text": raw.decode("utf-8", "replace")}
        p = u.path.rstrip("/")
        try:
            if p == "/api/op":
                ops = body.get("ops") if isinstance(body, dict) and "ops" in body else body
                ops = ops if isinstance(ops, list) else [ops]
            elif p.startswith("/api/"):
                name = p[5:].split("/")[0]
                if not isinstance(body, dict):
                    raise Bad("body must be a JSON object")
                o = dict(body)
                o["op"] = name
                ops = [o]
            else:
                return self._send(404, {"error": "not found"})
            if len(ops) > 100:
                raise Bad("at most 100 ops per request")
            return self._send(200, {"ok": True, "ops": self._submit(ops)})
        except Bad as ex:
            return self._send(400, {"ok": False, "error": str(ex)})

    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        if self.cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        last = None
        try:
            while True:
                with self.tap.cond:
                    self.tap.cond.wait(timeout=10)
                st = json.dumps(self.engine.state())
                if st != last:
                    last = st
                    self.wfile.write(b"data: " + st.encode() + b"\n\n")
                else:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


# ============================================================== main
def make_server(engine, tap, host="127.0.0.1", port=8099, token=None, cors=False):
    """Build (but don't start) the HTTP server for an engine whose backend is wrapped in `tap`."""
    handler = type("BoundApi", (Api,), {})
    handler.engine, handler.tap, handler.token, handler.cors = engine, tap, token, cors
    handler.meta, handler.font = build_meta(), build_font()
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    ap = argparse.ArgumentParser(prog="ld9900 dash", description="LD9900 dashboard + API")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--token", help="require this bearer token for page + API")
    ap.add_argument("--cors", action="store_true", help="send Access-Control-Allow-Origin: *")
    ap.add_argument("--sim", action="store_true", help="no hardware; terminal render")
    ap.add_argument("--headless-sim", action="store_true", help="no hardware, no terminal output")
    ap.add_argument("--page", type=int, default=G.PAGE)
    ap.add_argument("--tcp", default=S.DEFAULT_TCP, help="JSON-lines socket (or 'off')")
    ap.add_argument("--mode", default="list", choices=MODES)
    ap.add_argument("--face", nargs="?", const=0, type=int, help="start with a face at column X")
    ap.add_argument("--ambient", nargs="?", const="", metavar="CONFIG",
                    help="enable ambient idle mode (config JSON; default ~/.config/ld9900/ambient.json)")
    ap.add_argument("--idle-after", type=float, help="seconds of inactivity before ambient (overrides config)")
    a = ap.parse_args(argv[1:])

    if a.headless_sim:
        inner = NullBackend()
    elif a.sim:
        inner = S.SimBackend()
    else:
        inner = S.UsbBackend(a.page)
    tap = Tap(inner)
    engine = S.Engine(tap, a.face, a.mode)
    if a.ambient is not None:
        from .ambient import Ambient, load_config
        engine.attach_ambient(Ambient(load_config(a.ambient or None)), a.idle_after)
        print(f"ambient mode after {engine.idle_after:g}s idle", file=sys.stderr)
    engine_thread(engine, tap)
    if a.tcp and a.tcp != "off":
        socket_server(engine, a.tcp)
        print(f"JSON-lines socket on {a.tcp}", file=sys.stderr)

    httpd = make_server(engine, tap, a.host, a.port, a.token, a.cors)
    url = f"http://{a.host}:{a.port}/" + (f"?token={a.token}" if a.token else "")
    print(f"dashboard: {url}", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        engine.running = False
        tap.close()


class NullBackend:
    def send(self, data): pass
    def bright(self, level): pass
    def show(self, buf): pass
    def close(self): pass


# ============================================================== page
PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LD9900 Control</title>
<style>
:root{
  --bg:#080b0c; --panel:#10171a; --panel2:#0c1214; --line:#1d2a2e; --text:#d6e4e2; --dim:#7d918f;
  --vfd:#6effe1; --vfd-off:#13282a; --accent:#6effe1; --warn:#ffb86b; --bad:#ff6b6b;
  --r:10px; --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{display:flex;align-items:center;gap:14px;padding:14px 20px;border-bottom:1px solid var(--line);flex-wrap:wrap}
header h1{font:600 15px var(--mono);letter-spacing:.12em;margin:0;color:var(--vfd)}
.dot{width:9px;height:9px;border-radius:50%;background:var(--bad);box-shadow:0 0 8px var(--bad)}
.dot.on{background:var(--vfd);box-shadow:0 0 8px var(--vfd)}
.status{font:12px var(--mono);color:var(--dim)}
.spacer{flex:1}
main{max-width:1180px;margin:0 auto;padding:18px 16px 40px;display:grid;gap:16px;grid-template-columns:1fr 1fr;align-items:start}
.full{grid-column:1/-1}
@media (max-width:880px){main{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:16px}
.card h2{font:600 12px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--dim);margin:0 0 12px}
.screen{background:#030505;border-radius:8px;padding:14px;display:flex;justify-content:center;border:1px solid #0f1b1d}
#screen{width:100%;max-width:1000px;image-rendering:pixelated;cursor:crosshair}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}
label{color:var(--dim);font-size:12px}
input[type=text],textarea,select{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:7px;padding:8px 10px;font:13px var(--mono);outline:none}
input[type=text]:focus,textarea:focus{border-color:var(--vfd)}
input[type=text]{flex:1;min-width:120px}
textarea{width:100%;min-height:70px;resize:vertical}
input[type=range]{accent-color:var(--vfd)}
button{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:7px;padding:7px 12px;font:13px system-ui;cursor:pointer}
button:hover{border-color:var(--vfd)}
button.primary{background:var(--vfd);color:#04110f;border-color:var(--vfd);font-weight:600}
button.on{border-color:var(--vfd);color:var(--vfd);box-shadow:inset 0 0 0 1px var(--vfd)}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{border:0;border-radius:0;border-right:1px solid var(--line)}
.seg button:last-child{border-right:0}
.tabs{display:flex;gap:6px;margin-bottom:12px}
.tabs button{flex:1}
.pane{display:none}.pane.show{display:block}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(112px,1fr));gap:8px}
#amb-cards{grid-template-columns:repeat(auto-fill,minmax(170px,1fr))}
.tile{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:6px;cursor:pointer;text-align:center}
.tile:hover{border-color:var(--vfd)}
.tile.on{border-color:var(--vfd);box-shadow:0 0 0 1px var(--vfd)}
.tile canvas{width:100%;image-rendering:pixelated;display:block}
.tile span{display:block;font:11px var(--mono);color:var(--dim);margin-top:4px}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.palette{display:flex;flex-wrap:wrap;gap:5px}
.palette .tile{padding:4px}
.palette canvas{height:28px;width:auto}
.group{font:11px var(--mono);color:var(--dim);margin:10px 0 6px;letter-spacing:.1em;text-transform:uppercase}
pre{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:12px;overflow:auto;font:12px/1.5 var(--mono);color:#b9d3cf;margin:0}
.toast{position:fixed;right:16px;bottom:16px;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 14px;font:12px var(--mono);opacity:0;transition:opacity .2s;max-width:60vw}
.toast.show{opacity:1}.toast.bad{border-color:var(--bad);color:var(--bad)}
.hint{color:var(--dim);font-size:12px}
.switch{display:inline-flex;align-items:center;gap:8px;cursor:pointer;user-select:none}
.switch input{accent-color:var(--vfd);width:16px;height:16px}
</style></head><body>
<header>
  <div class="dot" id="live"></div><h1>LD9900</h1>
  <span class="status" id="status">connecting…</span>
  <div class="spacer"></div>
  <label>Brightness</label>
  <div class="seg" id="bright">
    <button data-l="1">20%</button><button data-l="2">40%</button><button data-l="3">60%</button><button data-l="4">100%</button>
  </div>
  <button onclick="api('clear')">Clear</button>
</header>
<main>
  <section class="card full">
    <div class="screen"><canvas id="screen"></canvas></div>
    <div class="row hint" id="cellinfo">Click a cell to pick a position for free mode.</div>
  </section>

  <section class="card">
    <h2>Mode</h2>
    <div class="tabs" id="tabs">
      <button data-m="marquee">Continuous</button><button data-m="list">List</button><button data-m="free">Freeflow</button>
    </div>

    <div class="pane" id="p-marquee">
      <div class="row"><input type="text" id="mq-text" placeholder="Ticker text…  {heart} tokens work"><button class="primary" onclick="mqStart()">Start ticker</button></div>
      <div class="row"><button onclick="mqAdd()">Append to tape</button></div>
      <div class="row"><label>Header</label><input type="text" id="mq-header" placeholder="static text on the other row"><button onclick="api('header',{text:v('mq-header')})">Set</button></div>
      <div class="row"><label>Speed</label><input type="range" id="mq-speed" min="1" max="25" value="7"><span class="status" id="mq-speed-v">7 cps</span>
        <label>Row</label><div class="seg" id="mq-row"><button data-r="0">Top</button><button data-r="1" class="on">Bottom</button></div>
        <label class="switch"><input type="checkbox" id="mq-loop" checked>Loop</label></div>
    </div>

    <div class="pane" id="p-list">
      <textarea id="ls-text" placeholder="One line per row. Long lines wrap."></textarea>
      <div class="row"><button class="primary" onclick="lsPush()">Push lines</button>
        <label>Hold</label><input type="range" id="ls-hold" min="0" max="5" step="0.1" value="0.8"><span class="status" id="ls-hold-v">0.8 s</span>
        <button onclick="api('mode',{mode:'list',hold:+v('ls-hold')})">Apply hold</button></div>
    </div>

    <div class="pane" id="p-free">
      <div class="row"><input type="text" id="fr-text" placeholder="Text at the selected cell"><button class="primary" onclick="frPut()">Put</button></div>
      <div class="row"><label>Bar</label><input type="range" id="fr-bar" min="0" max="100" value="50"><span class="status" id="fr-bar-v">50%</span>
        <label>Width</label><select id="fr-barw"></select><button onclick="frBar()">Draw bar</button></div>
      <div class="row"><input type="text" id="fr-spark" placeholder="Sparkline values: 3,5,2,8,6,9"><button onclick="frSpark()">Draw sparkline</button></div>
    </div>
    <p class="hint">Text boxes accept glyph tokens: click any glyph in the palette below to insert it.</p>
  </section>

  <section class="card">
    <h2>Face</h2>
    <div class="row">
      <label class="switch"><input type="checkbox" id="face-on">Show face</label>
      <div class="seg" id="face-pos"><button data-x="0">Left</button><button data-x="6">Center</button><button data-x="12">Right</button></div>
      <label class="switch"><input type="checkbox" id="face-idle" checked>Idle blinks</label>
    </div>
    <div class="row"><input type="text" id="say-text" placeholder="Make it say something…"><button class="primary" onclick="say()">Say</button></div>
    <div class="group">Reactions</div>
    <div class="chips" id="reactions"></div>
    <div class="group">Expressions</div>
    <div class="grid" id="exprs"></div>
  </section>

  <section class="card full" id="amb-card" style="display:none">
    <h2>Ambient</h2>
    <div class="row">
      <span class="status" id="amb-status">…</span>
      <div class="spacer"></div>
      <label class="switch"><input type="checkbox" id="amb-on">Enabled</label>
      <label>Idle after</label><input type="text" id="amb-idle" style="flex:0;width:70px"><label>s</label>
      <button onclick="api('ambient',{idle_after:+v('amb-idle'),enabled:true})">Set</button>
      <button class="primary" onclick="api('ambient',{on:true})">Show now</button>
      <button onclick="api('ambient',{on:false})">Exit</button>
    </div>
    <div class="row"><input type="text" id="card-text" placeholder="Push a card: text (title optional below)">
      <input type="text" id="card-title" placeholder="Title" style="flex:0 0 140px">
      <button onclick="pushCard()">Push card</button></div>
    <div class="group">Cards in rotation</div>
    <div class="grid" id="amb-cards"></div>
  </section>

  <section class="card full">
    <h2>Glyph palette</h2>
    <div id="palette"></div>
  </section>

  <section class="card full">
    <h2>API</h2>
    <pre id="apidoc"></pre>
  </section>
</main>
<div class="toast" id="toast"></div>
<script>
const TOKEN = new URLSearchParams(location.search).get('token');
const Q = TOKEN ? ('?token=' + encodeURIComponent(TOKEN)) : '';
const H = TOKEN ? {'Authorization':'Bearer '+TOKEN} : {};
let FONT=null, META=null, STATE=null, SEL={row:0,col:0}, lastInput=null;
const $ = id => document.getElementById(id);
const v = id => $(id).value;

function toast(msg, bad){const t=$('toast');t.textContent=msg;t.className='toast show'+(bad?' bad':'');clearTimeout(t._h);t._h=setTimeout(()=>t.className='toast',2200)}
async function api(op, body={}){
  try{
    const r = await fetch('/api/'+op, {method:'POST', headers:{'Content-Type':'application/json',...H}, body:JSON.stringify(body)});
    const j = await r.json();
    if(!j.ok){toast(j.error||'error',true)}
    return j;
  }catch(e){toast(String(e),true)}
}

// ---------- rendering
function drawCells(canvas, cells, cols, rows, opt={}){
  const s = opt.scale||4, gx=1, gy=opt.gapY??3, pad=2;
  const W=(cols*(5+gx)-gx+pad*2)*s, Hh=(rows*(7+gy)-gy+pad*2)*s;
  if(canvas.width!==W) canvas.width=W; if(canvas.height!==Hh) canvas.height=Hh;
  const c = canvas.getContext('2d');
  c.fillStyle='#030505'; c.fillRect(0,0,W,Hh);
  const on=getComputedStyle(document.documentElement).getPropertyValue('--vfd').trim()||'#6effe1';
  for(let i=0;i<cols*rows;i++){
    const code=cells[i]??32, glyph=FONT.rows[code], cx=(i%cols)*(5+gx)+pad, cy=Math.floor(i/cols)*(7+gy)+pad;
    for(let y=0;y<7;y++)for(let x=0;x<5;x++){
      const lit=(glyph[y]>>x)&1;
      c.fillStyle= lit? on : '#112325';
      c.beginPath(); c.arc((cx+x+.5)*s,(cy+y+.5)*s,s*.42,0,7); c.fill();
    }
    if(opt.sel && opt.sel.row*cols+opt.sel.col===i){
      c.strokeStyle='rgba(255,184,107,.8)'; c.lineWidth=Math.max(1,s/3);
      c.strokeRect((cx-.4)*s,(cy-.4)*s,5.8*s,7.8*s);
    }
  }
}
function renderScreen(){ if(STATE&&FONT) drawCells($('screen'), STATE.cells, 20, 2, {scale:8, sel: STATE.mode==='free'?SEL:null}); }

$('screen').addEventListener('click', e=>{
  const r=e.target.getBoundingClientRect(), s=e.target.width/r.width/8;
  const x=(e.clientX-r.left)*s-2, y=(e.clientY-r.top)*s-2;
  SEL={col:Math.max(0,Math.min(19,Math.floor(x/6))), row: y>8.5?1:0};
  $('cellinfo').textContent=`Selected row ${SEL.row}, column ${SEL.col}`;
  renderScreen();
});

// ---------- state sync
function applyState(st){
  STATE=st; renderScreen();
  document.querySelectorAll('#tabs button').forEach(b=>b.classList.toggle('on', b.dataset.m===st.mode));
  showPane(st.mode, false);
  document.querySelectorAll('#bright button').forEach(b=>b.classList.toggle('on', +b.dataset.l===st.brightness));
  $('face-on').checked = !!st.face;
  document.querySelectorAll('#face-pos button').forEach(b=>b.classList.toggle('on', st.face && +b.dataset.x===st.face.x));
  if(st.face) $('face-idle').checked = st.face.idle;
  document.querySelectorAll('#exprs .tile').forEach(t=>t.classList.toggle('on', st.face && t.dataset.e===st.face.expr));
  $('status').textContent = `${st.mode}${st.face? ' · face '+st.face.expr : ''}${st.face&&st.face.speaking?' · speaking':''}`;
  if(st.ambient){
    $('amb-card').style.display='';
    const a=st.ambient;
    $('amb-on').checked=a.enabled;
    if(document.activeElement!==$('amb-idle')) $('amb-idle').value=a.idle_after;
    $('amb-status').textContent = a.active ? ('active'+(a.quiet?' · quiet hours':'')) : (a.enabled ? `idle in ${a.idle_in}s` : 'disabled');
  }
}
function connect(){
  const es = new EventSource('/api/events'+Q);
  es.onopen=()=>$('live').classList.add('on');
  es.onmessage=e=>applyState(JSON.parse(e.data));
  es.onerror=()=>{$('live').classList.remove('on'); $('status').textContent='reconnecting…'};
}

// ---------- modes
let paneShown=null;
function showPane(m, user=true){
  if(!user && paneShown) return;      // don't yank the pane while the user is browsing tabs
  paneShown=m;
  if(!['marquee','list','free'].includes(m)) return;
  ['marquee','list','free'].forEach(x=>$('p-'+x).classList.toggle('show', x===m));
}
document.querySelectorAll('#tabs button').forEach(b=>b.onclick=()=>{showPane(b.dataset.m); api('mode', modeOpts(b.dataset.m));});
function modeOpts(m){
  if(m==='marquee') return {mode:m, speed:+v('mq-speed'), row:mqRow, loop:$('mq-loop').checked, header:v('mq-header')};
  if(m==='list') return {mode:m, hold:+v('ls-hold')};
  return {mode:m};
}
let mqRow=1;
document.querySelectorAll('#mq-row button').forEach(b=>b.onclick=()=>{mqRow=+b.dataset.r;document.querySelectorAll('#mq-row button').forEach(x=>x.classList.toggle('on',x===b))});
$('mq-speed').oninput=()=>$('mq-speed-v').textContent=v('mq-speed')+' cps';
$('ls-hold').oninput=()=>$('ls-hold-v').textContent=v('ls-hold')+' s';
$('fr-bar').oninput=()=>$('fr-bar-v').textContent=v('fr-bar')+'%';
for(let i=1;i<=20;i++){const o=document.createElement('option');o.value=i;o.textContent=i;if(i===12)o.selected=true;$('fr-barw').appendChild(o)}
async function mqStart(){ await fetch('/api/op',{method:'POST',headers:{'Content-Type':'application/json',...H},body:JSON.stringify({ops:[{op:'mode',...modeOpts('marquee')},{op:'text',text:v('mq-text')||' '}]})}).then(r=>r.json()).then(j=>{if(!j.ok)toast(j.error,true)}); }
function mqAdd(){ api('text',{text:v('mq-text')}); }
async function lsPush(){
  const lines=v('ls-text').split('\n').filter(x=>x.trim());
  if(STATE.mode!=='list') await api('mode',modeOpts('list'));
  if(lines.length) await fetch('/api/op',{method:'POST',headers:{'Content-Type':'application/json',...H},body:JSON.stringify(lines.map(t=>({op:'text',text:t})))});
  $('ls-text').value='';
}
async function frPut(){ if(STATE.mode!=='free') await api('mode',{mode:'free'}); api('put',{row:SEL.row,col:SEL.col,text:v('fr-text')}); }
async function frBar(){ if(STATE.mode!=='free') await api('mode',{mode:'free'}); api('bar',{row:SEL.row,col:SEL.col,width:+v('fr-barw'),value:v('fr-bar')/100}); }
async function frSpark(){ if(STATE.mode!=='free') await api('mode',{mode:'free'}); api('spark',{row:SEL.row,col:SEL.col,values:v('fr-spark')}); }
['mq-text','fr-text','say-text','mq-header'].forEach(id=>$(id).addEventListener('keydown',e=>{if(e.key==='Enter'){({'mq-text':mqStart,'fr-text':frPut,'say-text':say,'mq-header':()=>api('header',{text:v('mq-header')})})[id]()}}));
document.querySelectorAll('input[type=text],textarea').forEach(el=>el.addEventListener('focus',()=>lastInput=el));

// ---------- ambient
$('amb-on').onchange=e=>api('ambient', e.target.checked ? {enabled:true, idle_after:+v('amb-idle')||120} : {enabled:false});
async function pushCard(){
  const t=v('card-text'); if(!t) return;
  await api('card',{id:'dash-'+Date.now().toString(36), title:v('card-title')||'{bell}Note', text:t, ttl:3600});
  $('card-text').value=''; loadCards();
}
async function loadCards(){
  const j=await fetch('/api/ambient'+Q,{headers:H}).then(r=>r.json()).catch(()=>null);
  if(!j||!j.attached) return;
  const box=$('amb-cards'); box.innerHTML='';
  for(const c of j.cards){
    const rows=c.lines||[c.title||'',c.text||''];
    const cells=[...expandTokens(rows[0]).slice(0,12).concat(Array(12).fill(32)).slice(0,12),
                 ...expandTokens(rows[1]).slice(0,12).concat(Array(12).fill(32)).slice(0,12)];
    const t=document.createElement('div'); t.className='tile'; t.title=rows.join(' / ');
    const cv=document.createElement('canvas'); drawCells(cv,cells,12,2,{scale:3});
    const s=document.createElement('span'); s.textContent=c.source+(c.mood?' · '+c.mood:'');
    t.append(cv,s); box.appendChild(t);
  }
  const errs=Object.entries(j.providers).filter(([k,p])=>p.error).map(([k,p])=>k+': '+p.error);
  if(errs.length) toast(errs.join(' | '), true);
}
function expandTokens(text){
  const out=[]; const re=/\{([^}]+)\}|([\s\S])/g; let m;
  while((m=re.exec(text||''))){
    if(m[1]!==undefined){
      const name=m[1]; let codes=null;
      for(const g of Object.values(META.pieces)) for(const it of g){
        if(it.name===name) codes=it.codes;
        else if(name.startsWith(it.name+'.')){const i=+name.slice(it.name.length+1); if(it.codes[i]!==undefined) codes=[it.codes[i]];}
      }
      if(codes) out.push(...codes); else for(const ch of m[0]) out.push(ch.charCodeAt(0));
    } else { const c=m[2].charCodeAt(0); out.push(c<128?c:63); }
  }
  return out;
}
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'})[c])}
setInterval(loadCards, 15000);

// ---------- face
$('face-on').onchange=e=> e.target.checked ? api('face',{expr:(STATE.face&&STATE.face.expr)||'neutral'}) : api('face',{off:true});
document.querySelectorAll('#face-pos button').forEach(b=>b.onclick=()=>api('face',{x:+b.dataset.x}));
$('face-idle').onchange=e=>api('idle',{on:e.target.checked});
document.querySelectorAll('#bright button').forEach(b=>b.onclick=()=>api('bright',{level:+b.dataset.l}));
function say(){ const t=v('say-text'); if(t) api('say',{text:t, expr:STATE.face?STATE.face.expr:undefined}); $('say-text').value=''; }

function buildMeta(){
  const ex=$('exprs');
  for(const [name,cells] of Object.entries(META.expressions)){
    const t=document.createElement('div'); t.className='tile'; t.dataset.e=name; t.title=name;
    const c=document.createElement('canvas'); drawCells(c,cells,8,2,{scale:3});
    const s=document.createElement('span'); s.textContent=name;
    t.append(c,s); t.onclick=()=>api('face',{expr:name}); ex.appendChild(t);
  }
  for(const r of META.reactions){
    const b=document.createElement('button'); b.textContent=r; b.onclick=()=>api('react',{name:r}); $('reactions').appendChild(b);
  }
  const pal=$('palette');
  for(const [g,items] of Object.entries(META.pieces)){
    const h=document.createElement('div'); h.className='group'; h.textContent=g; pal.appendChild(h);
    const wrap=document.createElement('div'); wrap.className='palette';
    for(const it of items){
      const t=document.createElement('div'); t.className='tile'; t.title='{'+it.name+'}';
      const c=document.createElement('canvas'); drawCells(c,it.codes,it.codes.length,1,{scale:4,gapY:0});
      t.appendChild(c);
      t.onclick=()=>{
        const el=lastInput||$('fr-text'); const tok='{'+it.name+'}';
        const p=el.selectionStart??el.value.length; el.value=el.value.slice(0,p)+tok+el.value.slice(el.selectionEnd??p);
        el.focus(); el.selectionStart=el.selectionEnd=p+tok.length;
      };
      wrap.appendChild(t);
    }
    pal.appendChild(wrap);
  }
  const base=location.origin, auth=TOKEN?` -H "Authorization: Bearer ${TOKEN}"`:'';
  $('apidoc').textContent=
`# react / set a face / talk
curl${auth} -X POST ${base}/api/react -d '{"name":"laugh"}'
curl${auth} ${base}/api/face/happy
curl${auth} -X POST ${base}/api/say -d '{"text":"Build passed {check}","expr":"joy"}'

# modes
curl${auth} -X POST ${base}/api/mode -d '{"mode":"marquee","speed":8,"header":"{signal} NEWS"}'
curl${auth} -X POST ${base}/api/text -d '{"text":"Ticker text appended to the tape"}'
curl${auth} -X POST ${base}/api/mode -d '{"mode":"list","hold":1}'
echo "a plain-text body becomes a line" | curl${auth} -X POST ${base}/api/text --data-binary @-

# free addressing, several ops in one request (validated all-or-nothing)
curl${auth} -X POST ${base}/api/op -d '[{"op":"mode","mode":"free"},
  {"op":"put","row":0,"col":0,"text":"CPU"},{"op":"bar","row":0,"col":4,"width":16,"value":0.42},
  {"op":"spark","row":1,"col":4,"values":[3,5,2,8,6,9,4,7]}]'

# read state / follow live updates
curl${auth} ${base}/api/state
curl${auth} -N ${base}/api/events

# expressions: ${Object.keys(META.expressions).join(' ')}
# reactions:   ${META.reactions.join(' ')}`;
}

(async()=>{
  [FONT, META] = await Promise.all([fetch('/api/font'+Q).then(r=>r.json()), fetch('/api/meta'+Q).then(r=>r.json())]);
  buildMeta();
  applyState(await fetch('/api/state'+Q).then(r=>r.json()));
  showPane(STATE.mode==='ambient'?'list':STATE.mode);
  connect();
  loadCards();
})();
</script>
</body></html>
"""

if __name__ == "__main__":
    sys.exit(main())
