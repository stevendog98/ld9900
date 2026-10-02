"""Replays everything the streamer sends through a model of the firmware's Normal-mode
writer and checks the modelled screen always equals the intended framebuffer."""
import json
import random

import pytest

from ld9900 import face, stream as S


class FirmwareModel:
    def __init__(self):
        self.mem = bytearray(b" " * 40)
        self.cur = 0
        self.pending_pos = False
        self.mismatches = 0
        self.frames = 0

    def send(self, data):
        for b in data:
            if self.pending_pos:
                assert b < 40
                self.cur, self.pending_pos = b, False
            elif b == 0x10:
                self.pending_pos = True
            elif b >= 0x20:
                self.mem[self.cur] = b
                self.cur = (self.cur + 1) % 40
            else:
                raise AssertionError(f"control byte 0x{b:02x} sent as data")

    def bright(self, level):
        pass

    def show(self, buf):
        self.frames += 1
        if bytes(buf) != bytes(self.mem):
            self.mismatches += 1

    def close(self):
        pass


def run(engine, seconds, t0=0.0, dt=0.02):
    t = t0
    while t < t0 + seconds:
        engine.step(t)
        t += dt
    return t


@pytest.fixture
def fw():
    return FirmwareModel()


def test_framebuffer_random_diffs(fw):
    fb = S.Framebuffer(fw)
    rng = random.Random(7)
    for _ in range(500):
        for _ in range(rng.randint(0, 8)):
            fb.buf[rng.randrange(40)] = rng.randrange(0x20, 0x100)
        fb.flush()
    assert fw.mismatches == 0 and fw.frames > 0


def test_control_bytes_never_sent(fw):
    fb = S.Framebuffer(fw)
    fb.put(0, 0, b"\x10\x0d\x1b\x0aok")
    fb.flush()
    assert fw.mismatches == 0


def test_modes_and_ops(fw):
    e = S.Engine(fw, None, "marquee", speed=20)
    e.mode.feed("scrolling {heart} text that is longer than twenty cells")
    t = run(e, 4)
    e.op({"op": "mode", "mode": "list", "hold": 0.1})
    for i in range(6):
        e.op({"op": "text", "text": f"line {i} with some extra words to wrap"})
    t = run(e, 3, t)
    e.op({"op": "mode", "mode": "free"})
    e.op({"op": "put", "row": 0, "col": 0, "text": "CPU"})
    e.op({"op": "bar", "row": 1, "col": 0, "width": 20, "value": 0.37})
    e.op({"op": "spark", "row": 0, "col": 4, "values": [1, 5, 2, 9, 4]})
    t = run(e, 0.2, t)
    assert bytes(e.fb.buf[:3]) == b"CPU"
    for name in face.REACTIONS:
        e.op({"op": "react", "name": name})
        t = run(e, 2, t)
    assert fw.mismatches == 0


def test_free_canvas_survives_face_moves(fw):
    e = S.Engine(fw, None, "free")
    e.op({"op": "put", "row": 1, "col": 15, "text": "KEEP"})
    e.op({"op": "face", "x": 0, "expr": "happy"})
    run(e, 0.2)
    assert bytes(e.fb.buf[35:39]) == b"KEEP"
    assert fw.mismatches == 0


def test_bad_ops_do_not_kill_engine(fw):
    e = S.Engine(fw, None, "list")
    e.submit(json.dumps({"op": "react", "name": "no-such-reaction"}))
    e.submit("still alive")
    run(e, 0.5)
    assert b"still alive" in bytes(e.fb.buf)


def test_hbar_and_spark():
    assert len(S.hbar(0.5, 10)) == 10
    assert S.hbar(0, 4) == b"    "
    assert len(S.spark([1, 2, 3])) == 3
