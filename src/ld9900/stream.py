#!/usr/bin/env python3
"""
ld9900 stream - stream content to the LD9900 2x20 VFD.

Modes
  marquee   continuous ticker: text scrolls right-to-left along one row; new input is
            appended to the tape (optional static header on the other row)
  list      line by line: each new line pushes the previous one up (long lines wrap)
  free      free addressing: JSON ops put text/glyphs/bars anywhere on the 40 cells

Face overlay (any mode): --face [X] puts an 8x2 face at column X (default 0); the
mode then uses the remaining 12 columns. Faces idle (blink/glance) and play reactions.

Glyph tokens work everywhere: "CPU {vbar5} {heart} 21{deg}C".

Usage
  ld9900 stream [--sim] [--face [X]] marquee [--row 0|1] [--speed CPS] [--header T] [--loop] [TEXT...]
  ld9900 stream [--sim] [--face [X]] list [--hold SEC] [TEXT...]
  ld9900 stream [--sim] free                      JSON ops on stdin
  ld9900 stream [--sim] face EXPR [--say TEXT]    show an expression (Ctrl-C to quit)
  ld9900 stream [--sim] react NAME [--base EXPR]  play one reaction
  ld9900 stream [--sim] demo                      tour of everything
  ld9900 stream [--sim] serve [--tcp HOST:PORT] [--unix PATH] [--mode list] [--face [X]]
                              [--ambient [CONFIG]] [--idle-after SEC]
  ld9900 stream send [--tcp HOST:PORT | --unix PATH] JSON-or-TEXT ...
  Without TEXT, marquee/list read lines from stdin (e.g. `tail -f log | ld9900 stream list`).

  --sim           draw in the terminal instead of USB
  --record F.gif  also record what was shown to an animated GIF (works with or without --sim)
  --page N        font page holding the custom glyphs (default 8)

JSON ops (stdin in any mode, or over the server; one object per line)
  {"op":"mode","mode":"marquee|list|free", ...mode options...}
  {"op":"text","text":"..."}                 feed the current mode (plain text lines do the same)
  {"op":"header","text":"..."}               marquee: static text on the other row
  {"op":"put","row":0,"col":3,"text":"hi {heart}"}
  {"op":"clear"}
  {"op":"bar","row":1,"col":8,"width":12,"value":0.42}         horizontal bar (5 steps/cell)
  {"op":"spark","row":0,"col":8,"values":[3,5,2,8,...]}        vertical-bar sparkline
  {"op":"face","expr":"happy"} | {"op":"face","x":12} | {"op":"face","off":true}
  {"op":"react","name":"laugh"}              reactions: see ldface.REACTIONS
  {"op":"say","text":"hello!","expr":"happy"}   mouth animates while text types out
  {"op":"bright","level":1-4}
"""
import argparse
import asyncio
import collections
import json
import os
import socket
import sys
import threading
import time

from . import face as ldface
from . import glyphs as G

COLS, ROWS, CELLS = 20, 2, 40
DEFAULT_TCP = "127.0.0.1:7440"
FPS = 50


# =============================================================== backends
class UsbBackend:
    def __init__(self, page=G.PAGE):
        from . import ctl as ldctl
        self.d = ldctl.Display()
        # cursor off, Normal (addressable) mode, select custom font page
        self.d.write(bytes([0x14, 0x11, 0x1B, 0x25, page]))

    def send(self, data):
        self.d.write(data)

    def bright(self, level):
        self.d.write(bytes([0x04, {1: 0x20, 2: 0x40, 3: 0x60, 4: 0xFF}[level]]))

    def show(self, buf):
        pass

    def close(self):
        pass


class SimBackend:
    def __init__(self):
        from . import sim as ldsim
        self.sim = ldsim
        sys.stdout.write("\x1b[2J")

    def send(self, data):
        pass

    def bright(self, level):
        pass

    def show(self, buf):
        sys.stdout.write("\x1b[H" + self.sim.render_term(buf) + "\n")
        sys.stdout.flush()

    def close(self):
        pass


class Recorder:
    """Wraps a backend and records shown frames (with timing) to an animated GIF."""

    def __init__(self, inner, path):
        self.inner, self.path = inner, path
        self.frames = []          # (t, bytes)

    def send(self, data):
        self.inner.send(data)

    def bright(self, level):
        self.inner.bright(level)

    def show(self, buf):
        self.inner.show(buf)
        self.frames.append((time.monotonic(), bytes(buf)))

    def close(self):
        self.inner.close()
        if not self.frames:
            return
        from . import sim as ldsim
        imgs, durs = [], []
        for i, (t, b) in enumerate(self.frames):
            nxt = self.frames[i + 1][0] if i + 1 < len(self.frames) else t + 1.5
            imgs.append(ldsim.render_png(b, scale=4))
            durs.append(max(20, int((nxt - t) * 1000)))
        imgs[0].save(self.path, save_all=True, append_images=imgs[1:], duration=durs, loop=0)
        print(f"recorded {len(imgs)} frames -> {self.path}", file=sys.stderr)


# =============================================================== framebuffer
class Framebuffer:
    """Target picture + shadow of what the display shows; flush() sends only changes."""

    def __init__(self, backend):
        self.backend = backend
        self.buf = bytearray(b" " * CELLS)
        self.shadow = bytearray(b"\x00" * CELLS)   # force first full paint

    def put(self, row, col, data):
        if isinstance(data, str):
            data = G.expand(data)
        data = bytes(b if b >= 0x20 else 0x20 for b in data)    # never send control codes
        i = row * COLS + col
        for k, b in enumerate(data):
            if 0 <= col + k < COLS:
                self.buf[i + k] = b

    def fill(self, row, col, width, ch=b" "):
        self.put(row, col, ch * width)

    def clear(self):
        self.buf[:] = b" " * CELLS

    def flush(self):
        if self.buf == self.shadow:
            return
        out = bytearray()
        i = 0
        while i < CELLS:
            if self.buf[i] == self.shadow[i]:
                i += 1
                continue
            j = i
            # extend the run; bridge gaps of <=2 unchanged cells (cheaper than re-positioning)
            while j < CELLS and (self.buf[j] != self.shadow[j] or
                                 any(self.buf[k] != self.shadow[k] for k in range(j + 1, min(CELLS, j + 3)))):
                j += 1
            out += bytes([0x10, i]) + self.buf[i:j]
            i = j
        self.backend.send(bytes(out))
        self.shadow[:] = self.buf
        self.backend.show(self.buf)


# =============================================================== modes
class Mode:
    def __init__(self, fb, c0=0, width=COLS):
        self.fb, self.c0, self.w = fb, c0, width

    def set_viewport(self, c0, width):
        self.c0, self.w = c0, width
        self.redraw()

    def feed(self, text):
        pass

    def tick(self, now):
        pass

    def redraw(self):
        pass


class Marquee(Mode):
    """Ticker: a tape of cells slides left one cell per step; text enters at the right edge."""

    def __init__(self, fb, c0=0, width=COLS, row=1, speed=6.0, header="", loop=False, gap="   "):
        super().__init__(fb, c0, width)
        self.row, self.speed, self.loop, self.gap = row, float(speed), loop, G.expand(gap)
        self.header = G.expand(header)
        self.tape = bytearray(b" " * width)   # window = tape[off:off+w]
        self.off = 0
        self.acc = 0.0
        self.last = None
        self.history = b""

    def _pending(self):
        return len(self.tape) - (self.off + self.w)     # cells not yet on screen

    def feed(self, text):
        data = G.expand(text)
        self.history = data
        if self._pending() > 0:
            self.tape += self.gap                        # separate from queued text
        else:
            self.tape += b" " * (-self._pending())     # tape exhausted: enter at right edge
        self.tape += data

    def set_header(self, text):
        self.header = G.expand(text)
        self.redraw()

    def tick(self, now):
        if self.last is None:
            self.last = now
        self.acc += (now - self.last) * self.speed
        self.last = now
        steps = int(self.acc)
        if not steps:
            return
        self.acc -= steps
        for _ in range(steps):
            if self._pending() <= 0:
                if self.loop and self.history:
                    self.tape += self.gap + self.history
                elif all(c == 0x20 for c in self.tape[self.off:self.off + self.w]):
                    break                                   # idle: nothing left to scroll
                else:
                    self.tape += b" "
            self.off += 1
        if self.off > 256:                                  # keep the tape small
            del self.tape[:self.off]
            self.off = 0
        self.redraw()

    def set_viewport(self, c0, width):
        if width > self.w:
            self.tape += b" " * (width - self.w)
        self.c0, self.w = c0, width
        self.redraw()

    def redraw(self):
        win = bytes(self.tape[self.off:self.off + self.w]).ljust(self.w)
        self.fb.put(self.row, self.c0, win)
        self.fb.put(1 - self.row, self.c0, self.header[:self.w].ljust(self.w))


class ListMode(Mode):
    def __init__(self, fb, c0=0, width=COLS, hold=0.8):
        super().__init__(fb, c0, width)
        self.hold = float(hold)
        self.queue = collections.deque()
        self.lines = [b"", b""]
        self.next_at = 0.0

    def feed(self, text):
        for line in ldface.wrap(G.expand(text.rstrip("\r\n")), self.w):
            self.queue.append(line)

    def tick(self, now):
        if self.queue and now >= self.next_at:
            self.lines = [self.lines[1], self.queue.popleft()]
            self.next_at = now + (self.hold if self.queue else 0)
            self.redraw()

    def set_viewport(self, c0, width):
        super().set_viewport(c0, width)

    def redraw(self):
        for r in range(2):
            self.fb.put(r, self.c0, self.lines[r][:self.w].ljust(self.w))


class FreeMode(Mode):
    """Nothing animates; ops draw directly. Keeps its own canvas so it can repaint after
    speech or a face move. Plain text lines fill row 0 then row 1 of the viewport."""

    def __init__(self, fb, c0=0, width=COLS):
        super().__init__(fb, c0, width)
        self.next_row = 0
        self.canvas = bytearray(b" " * CELLS)

    def draw(self, row, col, data):
        if isinstance(data, str):
            data = G.expand(data)
        data = bytes(b if b >= 0x20 else 0x20 for b in data)
        for k, b in enumerate(data):
            if 0 <= col + k < COLS:
                self.canvas[row * COLS + col + k] = b
        self.fb.put(row, col, data)

    def clear(self):
        self.canvas[:] = b" " * CELLS

    def feed(self, text):
        line = G.expand(text.rstrip("\r\n"))[:self.w].ljust(self.w)
        self.draw(self.next_row, self.c0, line)
        self.next_row ^= 1

    def redraw(self):
        for r in range(2):
            self.fb.put(r, self.c0, self.canvas[r * COLS + self.c0:r * COLS + self.c0 + self.w])


# =============================================================== face layer
class FaceLayer:
    def __init__(self, fb, x=0, expr="neutral", idle=True):
        self.fb, self.x, self.expr = fb, x, expr
        self.idle = ldface.Idle(expr) if idle else None
        self.steps = []
        self.step_until = 0.0
        self.speaking_until = 0.0
        self.work = bytearray(b" " * CELLS)
        self.active_kw = dict(expr=expr)

    @property
    def region(self):          # columns the face occupies
        return self.x, 8

    def set(self, expr):
        self.expr = expr
        if self.idle:
            self.idle.base = expr
        self.steps = []
        self._show(dict(expr=expr))

    def play(self, steps):
        self.steps = list(steps)
        self.step_until = 0.0

    def react(self, name, hold=1.2):
        steps = ldface.reaction(name, self.expr)
        kw, _ = steps[-1]
        steps[-1] = (kw, hold)                       # linger on the final pose...
        steps.append((dict(expr=self.expr), 0.0))    # ...then return to the base expression
        self.play(steps)

    def say(self, text, expr=None, hold=2.5):
        steps = ldface.talk_frames(text, expr=expr or self.expr, x=self.x)
        self.play(steps)
        self.speaking_until = time.monotonic() + sum(s for _, s in steps) + hold

    @property
    def speaking(self):
        return time.monotonic() < self.speaking_until

    def _show(self, kw):
        kw = dict(kw)
        kw.setdefault("x", self.x)
        self.active_kw = kw
        self.work[:] = self.fb.buf
        ldface.render_step(self.work, kw, x_default=self.x)
        # copy only the face band (+ speech area while speaking)
        lo, hi = self.x, self.x + 8
        if "speech" in kw:
            lo, hi = 0, COLS
        for r in range(2):
            self.fb.buf[r * COLS + lo:r * COLS + hi] = self.work[r * COLS + lo:r * COLS + hi]

    def tick(self, now):
        if self.steps and now >= self.step_until:
            kw, sec = self.steps.pop(0)
            self._show(kw)
            self.step_until = now + sec
            return
        if not self.steps and self.idle and not self.speaking and self.idle.due(now):
            self.play(self.idle.pick(now))

    def redraw(self):
        self._show(self.active_kw)


# =============================================================== engine
class Engine:
    def __init__(self, backend, face_x=None, mode="list", **mode_opts):
        self.backend = backend
        self.fb = Framebuffer(backend)
        self.face = None
        self.mode = None
        self.inbox = collections.deque()
        self.lock = threading.Lock()
        self.running = True
        self.brightness = 4
        self._was_speaking = False
        # ambient (idle) mode
        self.ambient = None
        self.idle_after = 0
        self.ambient_active = False
        self.last_activity = None
        self._saved = None
        self._dimmed = False
        self._now = 0.0
        self.mode_name, self.mode_opts = mode, {}
        self.set_mode(mode, **mode_opts)
        if face_x is not None:
            self.set_face(face_x)

    # ----- layout
    def viewport(self):
        if not self.face:
            return 0, COLS
        x = self.face.x
        if x <= 6:
            return x + 8, COLS - (x + 8)
        return 0, x

    def set_mode(self, name, **opts):
        c0, w = self.viewport()
        cls = {"marquee": Marquee, "list": ListMode, "free": FreeMode}[name]
        allowed = {"marquee": {"row", "speed", "header", "loop"}, "list": {"hold"}, "free": set()}[name]
        opts = {k: v for k, v in opts.items() if k in allowed and v is not None}
        self.mode = cls(self.fb, c0, w, **opts)
        self.mode_name, self.mode_opts = name, opts
        self.fb.fill(0, c0, w)
        self.fb.fill(1, c0, w)
        self.mode.redraw()

    def set_face(self, x=0, expr=None, off=False):
        if off:
            self.face = None
            self.fb.clear()
        else:
            if self.face:
                self.face.x = x
            else:
                self.face = FaceLayer(self.fb, x, expr or "neutral")
            self.fb.clear()
            if expr:
                self.face.set(expr)
        c0, w = self.viewport()
        self.mode.set_viewport(c0, w)
        if self.face:
            self.face.redraw()

    # ----- input
    def submit(self, line):
        with self.lock:
            self.inbox.append(line)

    PASSIVE_OPS = {"card", "ambient", "bright", "idle"}     # don't count as activity

    def _handle(self, line):
        line = line.rstrip("\r\n")
        if not line:
            return
        if line.lstrip().startswith("{\""):
            try:
                op = json.loads(line)
            except json.JSONDecodeError:
                self._activity()
                self.mode.feed(line)
                return
            if op.get("op") not in self.PASSIVE_OPS:
                self._activity()
            self.op(op)
        else:
            self._activity()
            self.mode.feed(line)

    # ----- ambient (idle) mode
    def attach_ambient(self, ambient, idle_after=None):
        """Enable ambient mode: after `idle_after` s without activity, show info cards."""
        self.ambient = ambient
        self.idle_after = float(ambient.cfg.get("idle_after", 120) if idle_after is None else idle_after)

    def _activity(self):
        self.last_activity = self._now
        if self.ambient_active:
            self.exit_ambient()

    def enter_ambient(self):
        if self.ambient is None or self.ambient_active:
            return
        from .ambient import AmbientMode
        f = self.face
        self._saved = {
            "mode": self.mode, "mode_name": self.mode_name, "mode_opts": self.mode_opts,
            "face": None if not f else {"x": f.x, "expr": f.expr, "idle": f.idle is not None},
        }
        if not self.face:
            self.face = FaceLayer(self.fb, 0, "neutral")
        self.face.x = int(self.ambient.cfg.get("face_x", 12))
        self.face.idle = ldface.Idle("neutral")
        self.face.steps = []
        c0, w = self.viewport()
        self.mode = AmbientMode(self.fb, c0, w, self.ambient, on_mood=self._ambient_mood)
        self.mode_name, self.mode_opts = "ambient", {}
        self.ambient_active = True
        self.fb.clear()
        self.face.set("neutral")

    def _ambient_mood(self, expr):
        quiet = self.ambient.quiet()
        q = self.ambient.cfg.get("quiet_hours") or {}
        if quiet and not self._dimmed:
            self.backend.bright(int(q.get("brightness", 1)))
            self._dimmed = True
        elif not quiet and self._dimmed:
            self.backend.bright(self.brightness)
            self._dimmed = False
        if self.face and expr != self.face.expr and expr in ldface.EXPRESSIONS:
            self.face.set(expr)

    def exit_ambient(self):
        if not self.ambient_active:
            return
        if self._dimmed:
            self.backend.bright(self.brightness)
            self._dimmed = False
        s = self._saved
        self.ambient_active = False
        self.mode, self.mode_name, self.mode_opts = s["mode"], s["mode_name"], s["mode_opts"]
        if s["face"] is None:
            self.face = None
        else:
            self.face.x, self.face.expr = s["face"]["x"], s["face"]["expr"]
            self.face.idle = ldface.Idle(self.face.expr) if s["face"]["idle"] else None
            self.face.steps = []
        self.fb.clear()
        c0, w = self.viewport()
        self.mode.set_viewport(c0, w)
        if self.face:
            self.face.set(self.face.expr)

    def op(self, o):
        k = o.get("op")
        if k == "text":
            self.mode.feed(o.get("text", ""))
        elif k == "mode":
            opts = {x: o.get(x) for x in ("row", "speed", "header", "loop", "hold")}
            self.set_mode(o["mode"], **opts)
        elif k == "header" and isinstance(self.mode, Marquee):
            self.mode.set_header(o.get("text", ""))
        elif k == "put":
            self._draw(int(o.get("row", 0)), int(o.get("col", 0)), o.get("text", ""))
        elif k == "clear":
            self.fb.clear()
            if isinstance(self.mode, FreeMode):
                self.mode.clear()
            self.mode.redraw()
            if self.face:
                self.face.redraw()
        elif k == "bar":
            self._draw(int(o.get("row", 0)), int(o.get("col", 0)),
                       hbar(float(o.get("value", 0)), int(o.get("width", 10))))
        elif k == "spark":
            self._draw(int(o.get("row", 0)), int(o.get("col", 0)),
                       spark(o.get("values", []), o.get("lo"), o.get("hi")))
        elif k == "face":
            if o.get("off"):
                self.set_face(off=True)
            else:
                self.set_face(int(o.get("x", self.face.x if self.face else 0)), o.get("expr"))
        elif k == "react":
            if not self.face:
                self.set_face(0)
            self.face.react(o["name"])
        elif k == "say":
            if not self.face:
                self.set_face(0)
            self.face.say(o.get("text", ""), o.get("expr"), float(o.get("hold", 2.5)))
        elif k == "bright":
            self.brightness = int(o.get("level", 4))
            self.backend.bright(self.brightness)
        elif k == "idle":
            if self.face:
                on = bool(o.get("on", True))
                self.face.idle = ldface.Idle(self.face.expr) if on else None
        elif k == "ambient":
            if self.ambient is None:
                return
            if "idle_after" in o:
                self.idle_after = float(o["idle_after"])
            if "enabled" in o and not o["enabled"]:
                self.idle_after = 0
                self.exit_ambient()
            if o.get("on") is True:
                self.enter_ambient()
            elif o.get("on") is False:
                self.last_activity = self._now
                self.exit_ambient()
        elif k == "card":
            if self.ambient is not None:
                self.ambient.pushed.push(o["id"], o.get("title", ""), o.get("text", ""), o.get("mood"),
                                         o.get("ttl", 600), o.get("seconds"), o.get("lines"))
        elif k == "quit":
            self.running = False

    def _draw(self, row, col, data):
        if isinstance(self.mode, FreeMode):
            self.mode.draw(row, col, data)       # remembered, survives speech/face moves
        else:
            self.fb.put(row, col, data)          # transient overlay; the mode may repaint it

    # ----- main loop
    def step(self, now):
        self._now = now
        if self.last_activity is None:
            self.last_activity = now
        with self.lock:
            items = list(self.inbox)
            self.inbox.clear()
        for it in items:
            try:
                self._handle(it)
            except Exception as ex:                      # a bad op must not kill the loop
                print(f"op error: {ex!r} in {str(it)[:120]}", file=sys.stderr)
        speaking = self.face is not None and self.face.speaking
        if (self.ambient and not self.ambient_active and self.idle_after > 0 and not speaking
                and now - self.last_activity >= self.idle_after):
            self.enter_ambient()
        if not speaking:
            self.mode.tick(now)
        if self.face:
            self.face.tick(now)
            # speech just ended (compare with the previous step, not within this one)
            if self._was_speaking and not self.face.speaking:
                self.face.steps = [st for st in self.face.steps if "speech" not in st[0]]
                self.face.active_kw.pop("speech", None)
                c0, w = self.viewport()
                self.fb.fill(0, c0, w)
                self.fb.fill(1, c0, w)
                self.mode.redraw()
                self.face.redraw()
        self._was_speaking = self.face is not None and self.face.speaking
        self.fb.flush()

    def run(self, until=None):
        """Run the update loop (until a deadline, a quit op, or Ctrl-C)."""
        try:
            while self.running and (until is None or time.monotonic() < until):
                self.step(time.monotonic())
                time.sleep(1.0 / FPS)
        except KeyboardInterrupt:
            self.running = False

    def close(self):
        self.backend.close()

    def state(self):
        """Snapshot for dashboards / APIs."""
        f = self.face
        return {
            "mode": self.mode_name,
            "mode_opts": self.mode_opts,
            "face": None if not f else {"expr": f.expr, "x": f.x, "idle": f.idle is not None,
                                        "speaking": f.speaking},
            "brightness": self.brightness,
            "ambient": None if self.ambient is None else {
                "enabled": self.idle_after > 0, "active": self.ambient_active,
                "idle_after": self.idle_after,
                "idle_in": None if self.ambient_active or self.idle_after <= 0 or self.last_activity is None
                else max(0, round(self.idle_after - (self._now - self.last_activity))),
                "quiet": self.ambient.quiet(),
            },
            "viewport": list(self.viewport()),
            "cells": list(self.fb.buf),
        }

    def run_for(self, seconds):
        self.run(time.monotonic() + seconds)


# =============================================================== helpers
def hbar(value, width):
    units = max(0, min(width * 5, round(value * width * 5)))
    out = bytearray()
    for _ in range(width):
        n = min(5, units)
        out.append(G.code(f"hbar{n}") if n else 0x20)
        units -= n
    return bytes(out)


def spark(values, lo=None, hi=None):
    if not values:
        return b""
    lo = min(values) if lo is None else lo
    hi = max(values) if hi is None else hi
    span = (hi - lo) or 1
    return bytes(G.code(f"vbar{max(1, min(7, 1 + round((v - lo) / span * 6)))}") for v in values)


def stdin_feeder(engine, eof_quit_after=None):
    def run():
        for line in sys.stdin:
            engine.submit(line)
        if eof_quit_after is not None:
            time.sleep(eof_quit_after)
            engine.submit('{"op":"quit"}')
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


# =============================================================== server
def serve(engine, tcp=None, unix=None):
    async def handle(reader, writer):
        while True:
            line = await reader.readline()
            if not line:
                break
            engine.submit(line.decode("utf-8", "replace"))
        writer.close()

    async def main():
        servers = []
        if tcp:
            host, port = tcp.rsplit(":", 1)
            servers.append(await asyncio.start_server(handle, host, int(port)))
            print(f"listening on tcp {tcp}", file=sys.stderr)
        if unix:
            if os.path.exists(unix):
                os.unlink(unix)
            servers.append(await asyncio.start_unix_server(handle, unix))
            print(f"listening on unix {unix}", file=sys.stderr)
        while engine.running:
            engine.step(time.monotonic())
            await asyncio.sleep(1.0 / FPS)
        for s in servers:
            s.close()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


def send(lines, tcp=None, unix=None):
    if unix:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(unix)
    else:
        host, port = (tcp or DEFAULT_TCP).rsplit(":", 1)
        s = socket.create_connection((host, int(port)))
    with s:
        for l in lines:
            s.sendall(l.rstrip("\n").encode() + b"\n")


# =============================================================== demo
def demo(engine):
    E = engine
    E.set_face(0, "happy")
    E.face.say("Hi! I'm your display. {heart}", "happy")
    E.run_for(5)
    for name in ["laugh", "love", "surprise", "think", "angry", "cry", "dizzy", "sleep"]:
        E.op({"op": "react", "name": name})
        E.op({"op": "put", "row": 0, "col": 8, "text": "react:".ljust(12)})
        E.op({"op": "put", "row": 1, "col": 8, "text": name.ljust(12)})
        E.run_for(3.2)
    E.set_face(off=True)
    E.set_mode("list", hold=0.6)
    for l in ["List mode:", "each new line", "pushes the last", "one up. Long lines wrap to fit."]:
        E.op({"op": "text", "text": l})
    E.run_for(4.5)
    E.set_mode("marquee", row=1, speed=8, header="{signal} Marquee {note}")
    E.op({"op": "text", "text": "Continuous ticker text scrolls in from the right {sparkle} new input is appended..."})
    E.run_for(9)
    E.set_mode("free")
    E.op({"op": "clear"})
    E.op({"op": "put", "row": 0, "col": 0, "text": "CPU"})
    E.op({"op": "put", "row": 1, "col": 0, "text": "MEM"})
    import math
    for t in range(60):
        cpu = [50 + 40 * math.sin((t + k) / 3) for k in range(12)]
        E.op({"op": "spark", "row": 0, "col": 4, "values": cpu, "lo": 0, "hi": 100})
        E.op({"op": "put", "row": 0, "col": 17, "text": f"{int(cpu[-1]):3d}"})
        mem = 0.5 + 0.3 * math.sin(t / 10)
        E.op({"op": "bar", "row": 1, "col": 4, "width": 12, "value": mem})
        E.op({"op": "put", "row": 1, "col": 17, "text": f"{int(mem * 100):2d}%"})
        E.run_for(0.1)
    E.set_face(12, "wink")
    E.run_for(2)


# =============================================================== CLI
def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--record")
    ap.add_argument("--page", type=int, default=G.PAGE)
    ap.add_argument("--face", nargs="?", const=0, type=int)
    ap.add_argument("-h", "--help", action="store_true")
    g, rest = ap.parse_known_args(argv[1:])
    if g.help or not rest:
        print(__doc__)
        return 0
    cmd, rest = rest[0], rest[1:]

    if cmd == "send":
        sp = argparse.ArgumentParser()
        sp.add_argument("--tcp")
        sp.add_argument("--unix")
        sp.add_argument("items", nargs="*")
        a = sp.parse_args(rest)
        send(a.items or sys.stdin.readlines(), a.tcp, a.unix)
        return 0

    backend = SimBackend() if g.sim else UsbBackend(g.page)
    if g.record:
        backend = Recorder(backend, g.record)
    try:
        return run_cmd(cmd, rest, g, backend)
    finally:
        backend.close()


def run_cmd(cmd, rest, g, backend):

    if cmd == "marquee":
        sp = argparse.ArgumentParser()
        sp.add_argument("--row", type=int, default=1)
        sp.add_argument("--speed", type=float, default=6)
        sp.add_argument("--header", default="")
        sp.add_argument("--loop", action="store_true")
        sp.add_argument("text", nargs="*")
        a = sp.parse_args(rest)
        e = Engine(backend, g.face, "marquee", row=a.row, speed=a.speed, header=a.header,
                   loop=a.loop or bool(a.text))
        if a.text:
            e.mode.feed(" ".join(a.text))
        else:
            stdin_feeder(e)
        e.run()
    elif cmd == "list":
        sp = argparse.ArgumentParser()
        sp.add_argument("--hold", type=float, default=0.8)
        sp.add_argument("text", nargs="*")
        a = sp.parse_args(rest)
        e = Engine(backend, g.face, "list", hold=a.hold)
        if a.text:
            for t in a.text:
                e.mode.feed(t)
        else:
            stdin_feeder(e)
        e.run()
    elif cmd == "free":
        e = Engine(backend, g.face, "free")
        stdin_feeder(e)
        e.run()
    elif cmd == "face":
        sp = argparse.ArgumentParser()
        sp.add_argument("expr", nargs="?", default="neutral")
        sp.add_argument("--say")
        sp.add_argument("--x", type=int, default=g.face if g.face is not None else 0)
        a = sp.parse_args(rest)
        e = Engine(backend, None, "free")
        e.set_face(a.x, a.expr)
        if a.say:
            e.face.say(a.say)
        stdin_feeder(e)
        e.run()
    elif cmd == "react":
        sp = argparse.ArgumentParser()
        sp.add_argument("name")
        sp.add_argument("--base", default="neutral")
        sp.add_argument("--x", type=int, default=g.face if g.face is not None else 6)
        a = sp.parse_args(rest)
        e = Engine(backend, None, "free")
        e.set_face(a.x, a.base)
        e.face.idle = None
        e.face.react(a.name)
        e.run_for(sum(s for _, s in ldface.reaction(a.name)) + 1.5)
    elif cmd == "demo":
        demo(Engine(backend, None, "free"))
    elif cmd == "serve":
        sp = argparse.ArgumentParser()
        sp.add_argument("--tcp", default=None)
        sp.add_argument("--unix", default=None)
        sp.add_argument("--mode", default="list", choices=["marquee", "list", "free"])
        sp.add_argument("--ambient", nargs="?", const="", metavar="CONFIG")
        sp.add_argument("--idle-after", type=float)
        a = sp.parse_args(rest)
        tcp = a.tcp or (None if a.unix else DEFAULT_TCP)
        e = Engine(backend, g.face, a.mode)
        if a.ambient is not None:
            from .ambient import Ambient, load_config
            e.attach_ambient(Ambient(load_config(a.ambient or None)), a.idle_after)
        serve(e, tcp, a.unix)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
