import json

import pytest

from ld9900 import ambient as A, dash, glyphs as G, stream as S
from test_stream import FirmwareModel, run

OPEN_METEO = {
    "latitude": 33.45, "longitude": -112.07, "timezone": "America/Phoenix",
    "current_units": {"temperature_2m": "°F", "weather_code": "wmo code"},
    "current": {"time": "2026-10-02T13:00", "interval": 900, "temperature_2m": 103.4,
                "weather_code": 0, "is_day": 1, "relative_humidity_2m": 9, "wind_speed_10m": 7.8},
    "daily_units": {"temperature_2m_max": "°F"},
    "daily": {"time": ["2026-10-02"], "temperature_2m_max": [105.1], "temperature_2m_min": [79.3]},
}

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Feed</title>
<item><title>First \xe2\x80\x9cquoted\xe2\x80\x9d headline</title><link>x</link></item>
<item><title>Second headline</title></item><item><title>Third</title></item></channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>F</title>
<entry><title>Atom one</title></entry><entry><title>Atom two</title></entry></feed>"""


def cfg(**kw):
    c = json.loads(json.dumps(A.DEFAULT_CONFIG))
    c.update({"cards": ["clock", "facts", "pushed"], "quiet_hours": None, "idle_after": 5})
    c.update(kw)
    return c


def test_weather_card_from_open_meteo():
    w = A.WeatherProvider(cfg())
    w.data = OPEN_METEO
    p = w.parse()
    assert p["temp"] == 103 and p["mood"] == "nervous"      # >=100F: sweating
    cards = w.cards(None)
    row0 = G.expand(cards[0].lines[0])
    assert len(row0) <= 12 and b"103" in row0
    assert cards[0].lines[1] == "H105 L79"
    assert all(len(G.expand(x)) <= 12 for c in cards for x in c.lines)


def test_weather_missing_fields_is_safe():
    w = A.WeatherProvider(cfg())
    w.data = {"current": {}}
    assert w.cards(None) == []


@pytest.mark.parametrize("raw,expected", [(RSS, 3), (ATOM, 2)])
def test_feed_parsing(raw, expected):
    titles = A.NewsProvider.parse_feed(raw, 10)
    assert len(titles) == expected
    assert A._ascii(titles[0]).isascii()


def test_ascii_folding_and_no_tokens():
    assert A._ascii("“hi” — {x}") == '"hi" - (x)'


def test_pushed_cards_expire():
    p = A.PushedProvider(cfg())
    p.push("a", "T", "text", ttl=1000)
    p.push("b", "T", "gone", ttl=-1)
    assert [c.text for c in p.cards(None)] == ["text"]
    p.push("a", "", "")                                      # empty text deletes
    assert p.cards(None) == []


def make_engine(mode="list"):
    fw = FirmwareModel()
    e = S.Engine(fw, None, mode)
    e.attach_ambient(A.Ambient(cfg(), start_threads=False))
    return e, fw


def test_idle_enters_and_activity_restores():
    e, fw = make_engine("free")
    e.op({"op": "put", "row": 0, "col": 0, "text": "KEEP ME"})
    t = run(e, 4.0)
    assert not e.ambient_active
    t = run(e, 2.0, t)
    assert e.ambient_active and e.face.x == 12 and e.mode_name == "ambient"
    assert bytes(e.fb.buf[0:7]) != b"KEEP ME"
    e.submit(json.dumps({"op": "card", "id": "x", "text": "pushed"}))   # passive: stays ambient
    t = run(e, 0.2, t)
    assert e.ambient_active
    e.submit(json.dumps({"op": "put", "row": 1, "col": 0, "text": "wake up"}))  # activity -> restore
    t = run(e, 0.2, t)
    assert not e.ambient_active and e.mode_name == "free" and e.face is None
    assert bytes(e.fb.buf[20:27]) == b"wake up"
    assert bytes(e.fb.buf[0:7]) == b"KEEP ME"
    assert fw.mismatches == 0


def test_face_state_restored():
    e, fw = make_engine("list")
    e.op({"op": "face", "x": 0, "expr": "love"})
    t = run(e, 6.0)
    assert e.ambient_active and e.face.x == 12
    e.submit(json.dumps({"op": "text", "text": "hello"}))
    run(e, 1.0, t)
    assert e.face.x == 0 and e.face.expr == "love" and not e.ambient_active


def test_manual_on_off_and_disable():
    e, fw = make_engine()
    e.op({"op": "ambient", "on": True})
    assert e.ambient_active
    e.op({"op": "ambient", "on": False})
    assert not e.ambient_active
    e.op({"op": "ambient", "enabled": False})
    run(e, 20)
    assert not e.ambient_active
    st = e.state()["ambient"]
    assert st["enabled"] is False


def test_quiet_hours_dim_and_sleep(monkeypatch):
    e, fw = make_engine()
    sent = []
    fw.bright = lambda lvl: sent.append(lvl)
    e.ambient.cfg["quiet_hours"] = {"start": "00:00", "end": "23:59", "brightness": 1}
    monkeypatch.setattr(e.ambient, "quiet", lambda now_dt=None: True)
    t = run(e, 6)
    assert e.ambient_active and sent[:1] == [1] and e.face.expr == "asleep"
    e.submit("hi")
    run(e, 0.2, t)
    assert sent[-1] == e.brightness


def test_long_text_scrolls_in_viewport():
    e, fw = make_engine()
    e.ambient.pushed.push("n", "{bell}News", "A headline that is much longer than twelve cells", ttl=999)
    e.ambient.cfg["cards"] = ["pushed"]
    e.op({"op": "ambient", "on": True})
    seen = set()
    t = 0.0
    for _ in range(300):
        e.step(t)
        seen.add(bytes(e.fb.buf[20:32]))
        assert e.face.x == 12                                # face stays right-justified
        t += 0.05
    assert len(seen) > 5                                     # it moved
    assert fw.mismatches == 0


def test_validate_new_ops():
    assert dash.validate({"op": "ambient", "on": "true"})["on"] is True
    assert dash.validate({"op": "card", "id": "a", "text": "x", "mood": "happy"})["ttl"] == 600
    with pytest.raises(dash.Bad):
        dash.validate({"op": "card", "text": "no id"})
    with pytest.raises(dash.Bad):
        dash.validate({"op": "card", "id": "a", "mood": "grumpy"})
    with pytest.raises(dash.Bad):
        dash.validate({"op": "ambient", "idle_after": -5})
