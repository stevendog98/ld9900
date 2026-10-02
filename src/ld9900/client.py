#!/usr/bin/env python3
"""
ld9900.client - tiny client for the ld9900 dash HTTP API (stdlib only).

    from ld9900.client import LD
    ld = LD("http://display-host:8099", token="SECRET")   # token optional
    ld.react("laugh")
    ld.face("happy")
    ld.say("Build passed {check}", expr="joy")
    ld.mode("marquee", speed=8, header="{signal} NEWS"); ld.text("Ticker text...")
    ld.mode("list", hold=1); ld.text("a new line")
    with ld.batch() as b:                      # one request, validated all-or-nothing
        b.mode("free"); b.put(0, 0, "CPU"); b.bar(0, 4, 0.42, width=16)
    print(ld.state()["mode"])
    for st in ld.events(): ...                 # live state stream (SSE)

CLI:  ld9900 client [--url URL] [--token T] OP [key=value ...]   (or `state`)
      ld9900 client react name=love
      ld9900 client say text="hello there" expr=happy
"""
import json
import os
import sys
import urllib.error
import urllib.request


class LDError(RuntimeError):
    pass


class LD:
    def __init__(self, url=None, token=None, timeout=5):
        self.url = (url or os.environ.get("LD_URL", "http://127.0.0.1:8099")).rstrip("/")
        self.token = token or os.environ.get("LD_TOKEN")
        self.timeout = timeout

    # ---------- transport
    def _req(self, method, path, body=None, timeout=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", e.reason)
            except Exception:
                msg = e.reason
            raise LDError(f"{e.code}: {msg}") from None
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise LDError(f"cannot reach the dashboard at {self.url}: {reason}") from None

    def op(self, op, **fields):
        return self._req("POST", f"/api/{op}", fields)

    def ops(self, ops):
        return self._req("POST", "/api/op", {"ops": list(ops)})

    # ---------- reads
    def state(self):
        return self._req("GET", "/api/state")

    def meta(self):
        return self._req("GET", "/api/meta")

    def events(self):
        """Yield state dicts as the display changes (blocks; Server-Sent Events)."""
        req = urllib.request.Request(self.url + "/api/events")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req) as r:
            for raw in r:
                line = raw.decode().strip()
                if line.startswith("data: "):
                    yield json.loads(line[6:])

    # ---------- ops
    def face(self, expr=None, x=None):
        return self.op("face", **_clean(expr=expr, x=x))

    def face_off(self):
        return self.op("face", off=True)

    def react(self, name):
        return self.op("react", name=name)

    def say(self, text, expr=None, hold=None):
        return self.op("say", **_clean(text=text, expr=expr, hold=hold))

    def mode(self, mode, **opts):
        return self.op("mode", mode=mode, **opts)

    def text(self, text):
        return self.op("text", text=text)

    def header(self, text):
        return self.op("header", text=text)

    def put(self, row, col, text):
        return self.op("put", row=row, col=col, text=text)

    def clear(self):
        return self.op("clear")

    def bar(self, row, col, value, width=10):
        return self.op("bar", row=row, col=col, value=value, width=width)

    def spark(self, row, col, values, lo=None, hi=None):
        return self.op("spark", **_clean(row=row, col=col, values=list(values), lo=lo, hi=hi))

    def bright(self, level):
        return self.op("bright", level=level)

    def idle(self, on=True):
        return self.op("idle", on=on)

    def batch(self):
        return _Batch(self)


class _Batch:
    """Collects ops and sends them in one request on exit."""

    def __init__(self, ld):
        self.ld, self.items = ld, []

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if et is None and self.items:
            self.ld.ops(self.items)

    def __getattr__(self, name):
        target = getattr(LD, name)

        def rec(*a, **k):
            captured = []
            fake = type("F", (), {"op": lambda s, op, **f: captured.append(dict(op=op, **f))})()
            target(fake, *a, **k)
            self.items.extend(captured)
            return self
        return rec


def _clean(**kw):
    return {k: v for k, v in kw.items() if v is not None}


def main(argv=None):
    args = list(sys.argv if argv is None else argv)[1:]
    url = token = None
    while args and args[0].startswith("--"):
        if args[0] == "--url":
            url, args = args[1], args[2:]
        elif args[0] == "--token":
            token, args = args[1], args[2:]
        else:
            break
    if not args:
        print(__doc__)
        return 1
    ld = LD(url, token)
    op, fields = args[0], {}
    for kv in args[1:]:
        k, _, v = kv.partition("=")
        try:
            fields[k] = json.loads(v)
        except ValueError:
            fields[k] = v
    try:
        print(json.dumps(ld.state() if op == "state" else ld.op(op, **fields)))
    except LDError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
