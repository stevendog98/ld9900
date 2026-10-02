"""
Ambient mode: when nothing has been sent to the display for a while, the face moves to the
right edge and the left 12 columns rotate through info cards (clock, weather, news, facts,
system, and cards pushed by other services). Any new activity restores the previous screen.

Config: JSON file (see `ld9900 ambient init`), default ~/.config/ld9900/ambient.json

    ld9900 ambient init                 write an example config
    ld9900 ambient test [CONFIG]        fetch every provider once and print the cards
    ld9900 ambient preview [CONFIG]     run ambient mode immediately (add --sim for terminal)

Cards are pushed from other services with the `card` op:
    {"op":"card","id":"garage","title":"Garage","text":"Door open {warn}","mood":"nervous","ttl":600}
"""
import json
import os
import platform
import random
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

from . import glyphs as G

CONFIG_PATH = os.path.expanduser("~/.config/ld9900/ambient.json")
WIDTH = 12

DEFAULT_CONFIG = {
    "idle_after": 120,                    # seconds without activity before ambient starts (0 = off)
    "face_x": 12,                         # right-justified face
    "card_seconds": 8,                    # minimum time per card
    "scroll_speed": 6,                    # cells/s for long text
    "cards": ["clock", "weather", "news", "facts", "system", "pushed"],
    "location": {"name": "Phoenix", "lat": 33.45, "lon": -112.07, "units": "fahrenheit"},
    "weather_refresh": 900,
    "feeds": [
        {"name": "NPR", "url": "https://feeds.npr.org/1001/rss.xml", "max": 5},
    ],
    "news_refresh": 1200,
    "facts": [
        "Arizona doesn't observe daylight saving time (except the Navajo Nation).",
        "VFDs glow when electrons from a hot filament hit phosphor-coated anodes.",
        "Octopuses have three hearts.",
        "A day on Venus is longer than its year.",
        "Bananas are berries; strawberries are not.",
    ],
    "facts_file": None,                   # optional text file, one fact per line
    "quiet_hours": {"start": "23:00", "end": "06:00", "brightness": 1},
    "user_agent": "ld9900-ambient/0.1 (+https://github.com/YOUR_GITHUB_USER/ld9900)",
}


def load_config(path=None):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    path = path or (CONFIG_PATH if os.path.exists(CONFIG_PATH) else None)
    if path:
        with open(path) as f:
            user = json.load(f)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


# =============================================================== cards
class Card:
    """title: row 0 (static, <=12 cells); text: row 1 (scrolls if longer than 12 cells).
    If `lines` is given (2 strings) both rows are static. `render` (callable -> (row0,row1))
    makes a live card (re-rendered every second, e.g. the clock)."""

    def __init__(self, title="", text="", mood=None, seconds=None, lines=None, render=None,
                 source=""):
        self.title, self.text, self.mood = title, text, mood
        self.seconds, self.lines, self.render, self.source = seconds, lines, render, source

    def to_json(self):
        if self.render:
            a, b = self.render()
            return {"source": self.source, "lines": [a, b], "mood": self.mood}
        return {"source": self.source, "title": self.title, "text": self.text,
                "lines": self.lines, "mood": self.mood}


# =============================================================== providers
def _get(url, ua, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class Provider:
    name = "base"
    refresh = 0          # seconds between fetches; 0 = no fetching

    def __init__(self, cfg):
        self.cfg = cfg
        self.error = None
        self.updated = 0.0

    def fetch(self):
        pass

    def cards(self, now_dt):
        return []


class ClockProvider(Provider):
    name = "clock"

    def cards(self, now_dt):
        def render():
            t = datetime.now()
            return t.strftime("%a %b %d").replace(" 0", "  "), t.strftime("%I:%M %p").lstrip("0").rjust(8)
        return [Card(render=render, source="clock")]


WMO = {  # code -> (label <=6 chars so "{icon}104{deg} Label" fits 12 cells, glyph, mood)
    0: ("Clear", "sun", "happy"), 1: ("Fair", "sun", "happy"), 2: ("P.cldy", "cloud", "smile"),
    3: ("Cloudy", "cloud", "neutral"), 45: ("Fog", "cloud", "confused"), 48: ("Fog", "cloud", "confused"),
    51: ("Drzl", "cloud", "neutral"), 53: ("Drzl", "cloud", "neutral"), 55: ("Drzl", "cloud", "sad"),
    56: ("FzDrzl", "cloud", "sad"), 57: ("FzDrzl", "cloud", "sad"),
    61: ("Rain", "cloud", "sad"), 63: ("Rain", "cloud", "sad"), 65: ("HvRain", "cloud", "cry"),
    66: ("FzRain", "cloud", "sad"), 67: ("FzRain", "cloud", "sad"),
    71: ("Snow", "cloud", "surprised"), 73: ("Snow", "cloud", "surprised"), 75: ("HvSnow", "cloud", "shocked"),
    77: ("Snow", "cloud", "surprised"), 80: ("Shwrs", "cloud", "sad"), 81: ("Shwrs", "cloud", "sad"),
    82: ("Dnpour", "cloud", "cry"), 85: ("SnShwr", "cloud", "surprised"), 86: ("SnShwr", "cloud", "surprised"),
    95: ("Storm", "warn", "nervous"), 96: ("Storm", "warn", "shocked"), 99: ("Storm", "warn", "shocked"),
}


class WeatherProvider(Provider):
    """Open-Meteo (free, no API key)."""
    name = "weather"

    def __init__(self, cfg):
        super().__init__(cfg)
        self.refresh = cfg.get("weather_refresh", 900)
        self.data = None

    def url(self):
        loc = self.cfg["location"]
        q = {
            "latitude": loc["lat"], "longitude": loc["lon"],
            "current": "temperature_2m,weather_code,is_day,relative_humidity_2m,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min",
            "temperature_unit": loc.get("units", "fahrenheit"),
            "wind_speed_unit": "mph" if loc.get("units", "fahrenheit") == "fahrenheit" else "kmh",
            "timezone": "auto", "forecast_days": 1,
        }
        return "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(q)

    def fetch(self):
        self.data = json.loads(_get(self.url(), self.cfg["user_agent"]))

    def parse(self):
        d = self.data or {}
        cur, daily = d.get("current") or {}, d.get("daily") or {}
        if "temperature_2m" not in cur:
            return None
        code = int(cur.get("weather_code", 0))
        text, icon, mood = WMO.get(code, ("?", "cloud", "neutral"))
        if not cur.get("is_day", 1) and icon == "sun":
            icon, mood = "clock", "sleepy"
        temp = round(cur["temperature_2m"])
        hot = 100 if self.cfg["location"].get("units", "fahrenheit") == "fahrenheit" else 38
        cold = 40 if hot == 100 else 4
        if temp >= hot:
            mood = "nervous"          # sweating
        elif temp <= cold:
            mood = "surprised"
        hi = daily.get("temperature_2m_max", [None])[0]
        lo = daily.get("temperature_2m_min", [None])[0]
        return {"temp": temp, "text": text, "icon": icon, "mood": mood, "hi": hi, "lo": lo,
                "hum": cur.get("relative_humidity_2m"), "wind": cur.get("wind_speed_10m")}

    def cards(self, now_dt):
        w = self.parse()
        if not w:
            return []
        name = self.cfg["location"].get("name", "")
        row0 = f"{{{w['icon']}}}{w['temp']}{{deg}} {w['text']}"
        hilo = f"H{round(w['hi'])} L{round(w['lo'])}" if w["hi"] is not None else ""
        cards = [Card(lines=[row0, hilo], mood=w["mood"], source="weather")]
        extra = []
        if w["hum"] is not None:
            extra.append(f"{round(w['hum'])}%RH")
        if w["wind"] is not None:
            unit = "mph" if self.cfg["location"].get("units", "fahrenheit") == "fahrenheit" else "kmh"
            extra.append(f"{round(w['wind'])}{unit}")
        if extra:
            cards.append(Card(lines=[name[:WIDTH], " ".join(extra)], mood=w["mood"],
                              seconds=5, source="weather"))
        return cards


class NewsProvider(Provider):
    """RSS 2.0 / Atom feeds."""
    name = "news"

    def __init__(self, cfg):
        super().__init__(cfg)
        self.refresh = cfg.get("news_refresh", 1200)
        self.items = []          # (source, title)

    @staticmethod
    def parse_feed(raw, limit):
        root = ET.fromstring(raw)
        titles = []
        for item in root.iter():
            tag = item.tag.rsplit("}", 1)[-1]
            if tag in ("item", "entry"):
                for child in item:
                    if child.tag.rsplit("}", 1)[-1] == "title" and (child.text or "").strip():
                        titles.append(" ".join(child.text.split()))
                        break
            if len(titles) >= limit:
                break
        return titles

    def fetch(self):
        items, errors = [], []
        for feed in self.cfg.get("feeds", []):
            try:
                raw = _get(feed["url"], self.cfg["user_agent"])
                for t in self.parse_feed(raw, int(feed.get("max", 5))):
                    items.append((feed.get("name", "News"), t))
            except Exception as e:                       # one bad feed shouldn't drop the rest
                errors.append(f"{feed.get('name', feed.get('url'))}: {e}")
        self.error = "; ".join(errors) or None
        if items:
            self.items = items

    def cards(self, now_dt):
        return [Card(title=f"{{bell}}{src}"[:24], text=_ascii(t), mood="thinking", source="news")
                for src, t in self.items]


class FactsProvider(Provider):
    name = "facts"

    def __init__(self, cfg):
        super().__init__(cfg)
        self.facts = list(cfg.get("facts", []))
        if cfg.get("facts_file"):
            with open(os.path.expanduser(cfg["facts_file"])) as f:
                self.facts += [l.strip() for l in f if l.strip() and not l.startswith("#")]
        self.rng = random.Random()

    def cards(self, now_dt):
        if not self.facts:
            return []
        return [Card(title="{bulb}Fun fact", text=_ascii(self.rng.choice(self.facts)),
                     mood="idea", source="facts")]


class SystemProvider(Provider):
    name = "system"

    def cards(self, now_dt):
        host = platform.node().split(".")[0][:WIDTH]
        try:
            with open("/proc/uptime") as f:
                up = float(f.read().split()[0])
            load = os.getloadavg()[0]
        except (OSError, AttributeError):
            return []
        d, h = int(up // 86400), int(up % 86400 // 3600)
        return [Card(lines=[host, f"up {d}d{h}h {load:.1f}"], source="system")]


class PushedProvider(Provider):
    """Cards pushed by other services through the `card` op."""
    name = "pushed"

    def __init__(self, cfg):
        super().__init__(cfg)
        self.items = {}          # id -> (expires_at, Card)
        self.lock = threading.Lock()

    def push(self, cid, title="", text="", mood=None, ttl=600, seconds=None, lines=None):
        with self.lock:
            if not text and not lines:
                self.items.pop(cid, None)                # empty text deletes the card
                return
            self.items[cid] = (time.time() + float(ttl),
                               Card(title=title, text=text, mood=mood, seconds=seconds,
                                    lines=lines, source=f"card:{cid}"))

    def cards(self, now_dt):
        now = time.time()
        with self.lock:
            for k in [k for k, (exp, _) in self.items.items() if exp < now]:
                del self.items[k]
            return [c for _, c in self.items.values()]


PROVIDERS = {"clock": ClockProvider, "weather": WeatherProvider, "news": NewsProvider,
             "facts": FactsProvider, "system": SystemProvider, "pushed": PushedProvider}


def _ascii(s):
    """Fold typographic punctuation the ROM font can't show into ASCII."""
    table = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-",
             "—": "-", "…": "...", " ": " "}
    s = "".join(table.get(ch, ch) for ch in s)
    return s.encode("ascii", "replace").decode().replace("{", "(").replace("}", ")")


# =============================================================== coordinator
class Ambient:
    def __init__(self, cfg=None, start_threads=True):
        self.cfg = cfg or load_config()
        self.providers = {}
        for name in self.cfg.get("cards", []):
            if name in PROVIDERS:
                self.providers[name] = PROVIDERS[name](self.cfg)
        if "pushed" not in self.providers:
            self.providers["pushed"] = PushedProvider(self.cfg)
        self.queue = []
        self._stop = threading.Event()
        if start_threads:
            threading.Thread(target=self._refresher, daemon=True, name="ambient").start()

    @property
    def pushed(self):
        return self.providers["pushed"]

    def _refresher(self):
        while not self._stop.is_set():
            for p in self.providers.values():
                if p.refresh and time.time() - p.updated >= p.refresh:
                    try:
                        p.fetch()
                        p.error = p.error if p.name == "news" else None
                    except Exception as e:
                        p.error = str(e)
                        print(f"ambient: {p.name} fetch failed: {e}", file=sys.stderr)
                    p.updated = time.time() if not p.error else time.time() - p.refresh + 60
            self._stop.wait(5)

    def fetch_all(self):
        for p in self.providers.values():
            if p.refresh:
                try:
                    p.fetch()
                    p.updated = time.time()
                except Exception as e:
                    p.error = str(e)

    def stop(self):
        self._stop.set()

    def quiet(self, now_dt=None):
        q = self.cfg.get("quiet_hours")
        if not q:
            return False
        t = (now_dt or datetime.now()).strftime("%H:%M")
        a, b = q["start"], q["end"]
        return (a <= t < b) if a < b else (t >= a or t < b)

    def all_cards(self, now_dt=None):
        now_dt = now_dt or datetime.now()
        out = []
        for name in self.cfg.get("cards", []):
            p = self.providers.get(name)
            if p:
                out.extend(p.cards(now_dt))
        return out

    def next_card(self):
        if self.quiet():
            return self.providers.get("clock", ClockProvider(self.cfg)).cards(None)[0]
        if not self.queue:
            self.queue = self.all_cards()
            if not self.queue:
                self.queue = [Card(lines=["", ""], source="empty")]
        return self.queue.pop(0)

    def status(self):
        return {name: {"error": p.error, "updated": p.updated} for name, p in self.providers.items()}


# =============================================================== display mode
class AmbientMode:
    """A stream Mode that rotates cards in its viewport. Duck-types ld9900.stream.Mode."""

    def __init__(self, fb, c0, width, ambient, on_mood=None):
        self.fb, self.c0, self.w = fb, c0, width
        self.ambient, self.on_mood = ambient, on_mood
        self.card = None
        self.until = 0.0
        self.tape = b""
        self.off = 0
        self.acc = 0.0
        self.last = None
        self.last_render = 0.0

    def set_viewport(self, c0, width):
        self.c0, self.w = c0, width
        self.redraw()

    def feed(self, text):
        pass

    def _start(self, now):
        cfg = self.ambient.cfg
        self.card = self.ambient.next_card()
        self.off, self.acc, self.last = 0, 0.0, now
        base = float(self.card.seconds or cfg.get("card_seconds", 8))
        self.tape = b""
        if self.card.render is None and self.card.lines is None:
            text = G.expand(self.card.text)
            if len(text) > self.w:
                self.tape = text + b" " * self.w
                base = max(base, len(self.tape) / float(cfg.get("scroll_speed", 6)) + 1.5)
        self.until = now + base
        if self.on_mood:
            self.on_mood("asleep" if self.ambient.quiet() else (self.card.mood or "neutral"))
        self.redraw()

    def tick(self, now):
        if self.card is None or now >= self.until:
            self._start(now)
            return
        if self.tape:
            self.acc += (now - self.last) * float(self.ambient.cfg.get("scroll_speed", 6))
            self.last = now
            steps = int(self.acc)
            if steps:
                self.acc -= steps
                self.off = min(self.off + steps, max(0, len(self.tape) - self.w))
                self.redraw()
        elif self.card.render and now - self.last_render >= 1.0:
            self.last_render = now
            self.redraw()

    def redraw(self):
        c = self.card
        if c is None:
            return
        if c.render:
            rows = [G.expand(x) for x in c.render()]
        elif c.lines is not None:
            rows = [G.expand(x) for x in c.lines]
        else:
            body = self.tape[self.off:self.off + self.w] if self.tape else G.expand(c.text)
            rows = [G.expand(c.title), body]
        for r in range(2):
            self.fb.put(r, self.c0, bytes(rows[r][:self.w]).ljust(self.w))


# =============================================================== CLI
def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    cmd = argv[1] if len(argv) > 1 else ""
    path = argv[2] if len(argv) > 2 and not argv[2].startswith("-") else None
    if cmd == "init":
        dst = path or CONFIG_PATH
        if os.path.exists(dst):
            print(f"{dst} already exists; not overwriting")
            return 1
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        print(f"wrote {dst}: set your location and feeds, then run `ld9900 ambient test`")
        return 0
    if cmd == "test":
        amb = Ambient(load_config(path), start_threads=False)
        amb.fetch_all()
        for name, st in amb.status().items():
            print(f"{name:8s} {'ERROR: ' + st['error'] if st['error'] else 'ok'}")
        print()
        for c in amb.all_cards():
            j = c.to_json()
            rows = j["lines"] if j.get("lines") else [j.get("title", ""), j.get("text", "")]
            print(f"[{j['source']}] mood={j.get('mood')}\n  | {rows[0]}\n  | {rows[1]}")
        return 0
    if cmd == "preview":
        from . import stream as S
        sim = "--sim" in argv
        backend = S.SimBackend() if sim else S.UsbBackend()
        engine = S.Engine(backend, None, "list")
        engine.attach_ambient(Ambient(load_config(path)), idle_after=0.1)
        try:
            engine.run()
        finally:
            engine.close()
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
