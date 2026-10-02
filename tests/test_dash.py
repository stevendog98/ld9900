import threading

import pytest

from ld9900 import dash, stream as S
from ld9900.client import LD, LDError


@pytest.mark.parametrize("op", [
    {"op": "react", "name": "laugh"},
    {"op": "face", "expr": "happy", "x": 12},
    {"op": "face", "off": True},
    {"op": "say", "text": "hi {heart}", "expr": "joy"},
    {"op": "mode", "mode": "marquee", "speed": 8, "row": 0, "loop": "true"},
    {"op": "put", "row": 1, "col": 19, "text": "x"},
    {"op": "bar", "value": 0.5, "width": 20},
    {"op": "spark", "values": "1,2,3"},
    {"op": "bright", "level": 3},
])
def test_validate_accepts(op):
    assert dash.validate(op)["op"] == op["op"]


@pytest.mark.parametrize("op", [
    {"op": "react", "name": "nope"},
    {"op": "face", "expr": "grumpy"},
    {"op": "mode", "mode": "banana"},
    {"op": "put", "row": 2, "text": "x"},
    {"op": "put", "col": 20, "text": "x"},
    {"op": "bar", "value": 1.5},
    {"op": "spark", "values": []},
    {"op": "bright", "level": 9},
    {"op": "quit"},
    {"op": "text", "text": "x" * 5000},
    "not an object",
])
def test_validate_rejects(op):
    with pytest.raises(dash.Bad):
        dash.validate(op)


@pytest.fixture
def server():
    tap = dash.Tap(dash.NullBackend())
    engine = S.Engine(tap, None, "list")
    dash.engine_thread(engine, tap)
    httpd = dash.make_server(engine, tap, "127.0.0.1", 0, token="t0k")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", engine
    engine.running = False
    httpd.shutdown()


def test_api_end_to_end(server):
    url, engine = server
    with pytest.raises(LDError, match="401"):
        LD(url).react("love")
    ld = LD(url, token="t0k")
    assert ld.react("love")["ok"]
    with pytest.raises(LDError, match="400"):
        ld.face("grumpy")
    with ld.batch() as b:
        b.mode("free")
        b.put(0, 10, "HELLO")
    import time
    for _ in range(50):
        st = ld.state()
        if bytes(st["cells"][10:15]) == b"HELLO":
            break
        time.sleep(0.05)
    assert st["mode"] == "free" and bytes(st["cells"][10:15]) == b"HELLO"
    assert "expressions" in ld.meta()
    ev = next(ld.events())
    assert ev["mode"] == "free"
