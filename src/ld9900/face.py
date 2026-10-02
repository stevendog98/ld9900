#!/usr/bin/env python3
"""
ld9900.face - expressive faces and animated reactions for the LD9900 (needs custom page 8).

A face occupies 8 columns x 2 rows starting at column x:

    top:    [tl ][ eyeL x2 ][ mid x2 ][ eyeR x2 ][tr ]
    bottom: [bl ][cheekL][   mouth  x4   ][cheekR][br ]

The other 12 columns are free for speech / status text.

    ld9900 face preview DIR       PNG of every expression + GIF of every reaction
"""
import random
import re

from . import glyphs as G

COLS = 20

# expression = eyes (L, R) piece names, mouth piece name, extras {slot: glyph}
EXPRESSIONS = {
    "neutral":    dict(eyes=("eye_open", "eye_open"), mouth="mouth_flat"),
    "happy":      dict(eyes=("eye_happy", "eye_happy"), mouth="mouth_smile"),
    "joy":        dict(eyes=("eye_happy", "eye_happy"), mouth="mouth_grin",
                       extras={"cheekL": "blush", "cheekR": "blush"}),
    "laugh":      dict(eyes=("eyeL_squint", "eyeR_squint"), mouth="mouth_grin"),
    "smile":      dict(eyes=("eye_open", "eye_open"), mouth="mouth_smile"),
    "love":       dict(eyes=("eye_heart", "eye_heart"), mouth="mouth_smile",
                       extras={"tr": "heart"}),
    "kiss":       dict(eyes=("eye_closed", "eye_closed"), mouth="mouth_small_o",
                       extras={"tr": "heart_small"}),
    "sad":        dict(eyes=("eyeL_sad", "eyeR_sad"), mouth="mouth_frown"),
    "cry":        dict(eyes=("eyeL_sad", "eyeR_sad"), mouth="mouth_frown",
                       extras={"cheekL": "tear", "cheekR": "tear"}),
    "angry":      dict(eyes=("eyeL_angry", "eyeR_angry"), mouth="mouth_frown",
                       extras={"tr": "anger"}),
    "furious":    dict(eyes=("eyeL_angry", "eyeR_angry"), mouth="mouth_grimace",
                       extras={"tr": "anger", "tl": "puff"}),
    "surprised":  dict(eyes=("eye_wide", "eye_wide"), mouth="mouth_o"),
    "shocked":    dict(eyes=("eye_wide", "eye_wide"), mouth="mouth_o",
                       extras={"tr": "exclaim"}),
    "confused":   dict(eyes=("eye_open", "eye_half"), mouth="mouth_wavy",
                       extras={"tr": "question"}),
    "thinking":   dict(eyes=("eye_up", "eye_up"), mouth="mouth_flat",
                       extras={"tr": "dots"}),
    "idea":       dict(eyes=("eye_wide", "eye_wide"), mouth="mouth_smile",
                       extras={"tr": "bulb"}),
    "sleepy":     dict(eyes=("eye_sleepy", "eye_sleepy"), mouth="mouth_small_o"),
    "asleep":     dict(eyes=("eye_closed", "eye_closed"), mouth="mouth_flat",
                       extras={"tr": "z_big", "mid": "z_small"}),
    "dead":       dict(eyes=("eye_x", "eye_x"), mouth="mouth_tongue"),
    "dizzy":      dict(eyes=("eye_dizzy", "eye_dizzy"), mouth="mouth_wavy"),
    "starstruck": dict(eyes=("eye_star", "eye_star"), mouth="mouth_grin",
                       extras={"tl": "sparkle", "tr": "sparkle_small"}),
    "smug":       dict(eyes=("eye_half", "eye_half"), mouth="mouth_smirk"),
    "wink":       dict(eyes=("eye_open", "eye_closed"), mouth="mouth_smile"),
    "playful":    dict(eyes=("eye_happy", "eye_closed"), mouth="mouth_tongue"),
    "cat":        dict(eyes=("eye_happy", "eye_happy"), mouth="mouth_cat"),
    "embarrassed": dict(eyes=("eye_happy", "eye_happy"), mouth="mouth_wavy",
                        extras={"cheekL": "blush", "cheekR": "blush", "tr": "sweat"}),
    "nervous":    dict(eyes=("eye_open", "eye_open"), mouth="mouth_grimace",
                       extras={"tr": "sweat"}),
    "singing":    dict(eyes=("eye_closed", "eye_closed"), mouth="mouth_small_o",
                       extras={"tr": "note"}),
    "bored":      dict(eyes=("eye_half", "eye_half"), mouth="mouth_flat"),
    "look_left":  dict(eyes=("eye_left", "eye_left"), mouth="mouth_flat"),
    "look_right": dict(eyes=("eye_right", "eye_right"), mouth="mouth_flat"),
    "look_up":    dict(eyes=("eye_up", "eye_up"), mouth="mouth_flat"),
    "look_down":  dict(eyes=("eye_down", "eye_down"), mouth="mouth_flat"),
    "talk":       dict(eyes=("eye_open", "eye_open"), mouth="mouth_talk"),
}

SLOTS = {  # slot -> (row, column offset within the face)
    "tl": (0, 0), "mid": (0, 3), "mid2": (0, 4), "tr": (0, 7),
    "bl": (1, 0), "cheekL": (1, 1), "cheekR": (1, 6), "br": (1, 7),
}


def draw(buf, expr="neutral", x=6, eyes=None, mouth=None, extras=None, clear=True):
    """Draw a face into a 40-byte bytearray. Overrides replace parts of the expression."""
    e = EXPRESSIONS[expr] if isinstance(expr, str) else expr
    eyes = eyes or e["eyes"]
    mouth = mouth or e["mouth"]
    ex = dict(e.get("extras", {}))
    if extras:
        ex.update(extras)
    x = max(0, min(COLS - 8, x))
    if clear:
        for r in range(2):
            buf[r * COLS + x:r * COLS + x + 8] = b" " * 8
    L, R = G.piece(eyes[0]), G.piece(eyes[1])
    buf[x + 1:x + 3] = L
    buf[x + 5:x + 7] = R
    m = G.piece(mouth)
    base = COLS + x + 2
    if len(m) == 4:
        buf[base:base + 4] = m
    elif len(m) == 2:
        buf[base + 1:base + 3] = m
    else:                                   # 1-cell repeating mouth
        buf[base:base + 4] = m * 4
    for slot, glyph in ex.items():
        if glyph is None:
            continue
        r, c = SLOTS[slot]
        buf[r * COLS + x + c] = G.code(glyph)
    return buf


# ---------------------------------------------------------------- reactions
# A reaction is a list of steps (frame_kwargs, seconds). frame_kwargs go to draw().

def _seq(*steps):
    return list(steps)


def reaction(name, base="neutral", text=None):
    """Return [(draw-kwargs, seconds), ...] for a named reaction."""
    b = base
    if name == "blink":
        return _seq((dict(expr=b, eyes=("eye_half", "eye_half")), .06),
                    (dict(expr=b, eyes=("eye_closed", "eye_closed")), .10),
                    (dict(expr=b, eyes=("eye_half", "eye_half")), .06),
                    (dict(expr=b), .0))
    if name == "look_around":
        return _seq((dict(expr="look_left"), .6), (dict(expr="neutral"), .25),
                    (dict(expr="look_right"), .6), (dict(expr="neutral"), .25),
                    (dict(expr="look_up"), .5), (dict(expr=b), .0))
    if name == "nod":        # yes: eyes dip down/up
        return _seq(*[s for _ in range(2) for s in
                      ((dict(expr="smile", eyes=("eye_down", "eye_down")), .18),
                       (dict(expr="smile", eyes=("eye_up", "eye_up")), .18))],
                    (dict(expr="smile"), .0))
    if name == "shake":      # no: eyes left/right
        return _seq(*[s for _ in range(3) for s in
                      ((dict(expr="look_left", mouth="mouth_frown"), .15),
                       (dict(expr="look_right", mouth="mouth_frown"), .15))],
                    (dict(expr="neutral", mouth="mouth_frown"), .0))
    if name == "laugh":
        return _seq(*[s for _ in range(4) for s in
                      ((dict(expr="laugh", extras={"tr": "sparkle_small"}), .18),
                       (dict(expr="laugh", mouth="mouth_talk", extras={"tl": "sparkle_small"}), .18))],
                    (dict(expr="joy"), .0))
    if name == "love":
        return _seq(*[s for _ in range(4) for s in
                      ((dict(expr="love", extras={"tr": "heart"}), .35),
                       (dict(expr="love", extras={"tr": "heart_small", "tl": "heart_small"}), .35))],
                    (dict(expr="love"), .0))
    if name == "angry":
        return _seq((dict(expr="angry"), .3),
                    *[s for _ in range(3) for s in
                      ((dict(expr="furious", dx=-1), .07), (dict(expr="furious", dx=1), .07))],
                    (dict(expr="angry"), .0))
    if name == "surprise":
        return _seq((dict(expr="neutral"), .15), (dict(expr="surprised"), .25),
                    (dict(expr="shocked"), .25), (dict(expr="surprised"), .25),
                    (dict(expr="shocked"), .6), (dict(expr="surprised"), .0))
    if name == "cry":
        return _seq(*[s for _ in range(3) for s in
                      ((dict(expr="sad", extras={"cheekL": "tear"}), .35),
                       (dict(expr="sad", extras={"cheekR": "tear", "bl": "tear"}), .35))],
                    (dict(expr="cry"), .0))
    if name == "sleep":
        return _seq((dict(expr="sleepy"), .8), (dict(expr="bored", eyes=("eye_closed", "eye_closed")), .5),
                    *[s for _ in range(3) for s in
                      ((dict(expr="asleep", extras={"tr": None, "mid": "z_small"}), .5),
                       (dict(expr="asleep", extras={"mid": "z_small", "tr": "z_big"}), .5),
                       (dict(expr="asleep", extras={"mid": None, "tr": "z_big"}), .5))],
                    (dict(expr="asleep"), .0))
    if name == "dizzy":
        return _seq(*[s for _ in range(4) for s in
                      ((dict(expr="dizzy", dx=-1), .15), (dict(expr="dizzy", eyes=("eye_x", "eye_x")), .15),
                       (dict(expr="dizzy", dx=1), .15))],
                    (dict(expr="dizzy"), .0))
    if name == "think":
        return _seq((dict(expr="thinking", extras={"tr": None}), .4),
                    *[s for _ in range(3) for s in
                      ((dict(expr="thinking"), .35), (dict(expr="thinking", extras={"tr": None}), .35))],
                    (dict(expr="idea"), .8), (dict(expr="happy"), .0))
    if name == "excited":
        return _seq(*[s for _ in range(4) for s in
                      ((dict(expr="starstruck"), .2),
                       (dict(expr="starstruck", extras={"tl": "sparkle_small", "tr": "sparkle"}), .2))],
                    (dict(expr="joy"), .0))
    if name == "wink":
        return _seq((dict(expr="smile"), .2), (dict(expr="wink"), .5), (dict(expr="smile"), .0))
    if name == "nervous":
        return _seq(*[s for _ in range(3) for s in
                      ((dict(expr="nervous", eyes=("eye_left", "eye_left")), .3),
                       (dict(expr="nervous", eyes=("eye_right", "eye_right")), .3))],
                    (dict(expr="nervous"), .0))
    if name == "sing":
        return _seq(*[s for _ in range(4) for s in
                      ((dict(expr="singing"), .3),
                       (dict(expr="singing", mouth="mouth_o", extras={"tr": None, "tl": "note"}), .3))],
                    (dict(expr="happy"), .0))
    raise KeyError(name)


REACTIONS = ["blink", "look_around", "nod", "shake", "laugh", "love", "angry", "surprise",
             "cry", "sleep", "dizzy", "think", "excited", "wink", "nervous", "sing"]

TALK_MOUTHS = ["mouth_talk", "mouth_small_o", "mouth_o", "mouth_flat", "mouth_talk", "mouth_smile"]


def talk_frames(text, expr="neutral", x=0, speed=0.06):
    """Mouth flaps while `text` types out to the right of the face (≤12 cols per line, 2 lines)."""
    units = re.findall(r"\{[^}]+\}|.", text)     # keep {glyph} tokens whole
    steps = []
    shown = ""
    for i, u in enumerate(units):
        shown += u
        mouth = "mouth_flat" if u in " .,!?" else TALK_MOUTHS[i % len(TALK_MOUTHS)]
        steps.append((dict(expr=expr, mouth=mouth, x=x, speech=shown), speed))
    steps.append((dict(expr=expr, x=x, speech=shown), 0.0))
    return steps


def render_step(buf, kw, x_default=6):
    """Apply one reaction step to a 40-byte buffer.

    The face owns exactly columns x..x+7. Shakes (dx) are drawn offset and clipped to
    that band, so they never touch neighbouring text. Speech goes in the 12 free columns.
    """
    kw = dict(kw)
    x = max(0, min(COLS - 8, kw.pop("x", x_default)))
    dx = kw.pop("dx", 0)
    speech = kw.pop("speech", None)
    tmp = bytearray(b" " * (2 * COLS))
    fx = x + dx
    if 0 <= fx <= COLS - 8:
        draw(tmp, x=fx, **kw)
    else:                                   # shaking against the screen edge: no offset
        draw(tmp, x=x, **kw)
    for r in range(2):
        buf[r * COLS + x:r * COLS + x + 8] = tmp[r * COLS + x:r * COLS + x + 8]
    if speech is not None:
        sx = x + 8 if x + 8 <= COLS - 4 else 0
        width = COLS - sx if sx else x
        lines = wrap(G.expand(speech), width)[-2:]
        for r in range(2):
            line = lines[r] if r < len(lines) else b""
            buf[r * COLS + sx:r * COLS + sx + width] = line.ljust(width)[:width]
    return buf


def wrap(data, width):
    """Word-wrap display bytes into lines of at most `width` cells."""
    lines, cur = [], b""
    for w in data.split(b" "):
        while len(w) > width:                    # hard-split long words
            if cur:
                lines.append(cur)
                cur = b""
            lines.append(w[:width])
            w = w[width:]
        if len(cur) + len(w) + (1 if cur else 0) <= width:
            cur = (cur + b" " + w) if cur else w
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


class Idle:
    """Background life: random blinks and glances on top of a base expression."""

    def __init__(self, base="neutral", rng=None):
        self.base = base
        self.rng = rng or random.Random()
        self.next_at = 0.0

    def due(self, now):
        return now >= self.next_at

    def pick(self, now):
        self.next_at = now + self.rng.uniform(2.0, 6.0)
        r = self.rng.random()
        if r < 0.75:
            return reaction("blink", self.base)
        eyes = self.rng.choice([("eye_left",) * 2, ("eye_right",) * 2, ("eye_up",) * 2])
        return [(dict(expr=self.base, eyes=eyes), self.rng.uniform(.4, 1.0)), (dict(expr=self.base), 0)]


# ---------------------------------------------------------------- preview
def main(argv=None):
    import os
    import sys

    from . import sim as ldsim
    argv = list(sys.argv if argv is None else argv)
    if len(argv) < 3 or argv[1] != "preview":
        print(__doc__)
        return 1
    out = argv[2]
    os.makedirs(out, exist_ok=True)
    from PIL import Image, ImageDraw
    tiles = []
    for name in EXPRESSIONS:
        buf = bytearray(b" " * 40)
        draw(buf, name, x=0)
        buf[8:20] = name[:12].ljust(12).encode()
        tiles.append(ldsim.render_png(buf, scale=4))
    tw, th = tiles[0].size
    cols = 2
    sheet = Image.new("RGB", (tw * cols, th * ((len(tiles) + cols - 1) // cols)), (0, 0, 0))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % cols) * tw, (i // cols) * th))
    sheet.save(os.path.join(out, "faces.png"))
    for name in REACTIONS:
        frames, durs = [], []
        buf = bytearray(b" " * 40)
        for kw, sec in reaction(name):
            render_step(buf, kw, x_default=6)
            frames.append(bytes(buf))
            durs.append(max(60, int(sec * 1000)) if sec else 900)
        imgs = [ldsim.render_png(f, scale=4) for f in frames]
        imgs[0].save(os.path.join(out, f"react_{name}.gif"), save_all=True,
                     append_images=imgs[1:], duration=durs, loop=0)
    buf = bytearray(b" " * 40)
    frames = []
    for kw, sec in talk_frames("Hi Steven! Flash worked {heart}", expr="happy", x=0):
        render_step(buf, kw, x_default=0)
        frames.append(bytes(buf))
    imgs = [ldsim.render_png(f, scale=4) for f in frames]
    imgs[0].save(os.path.join(out, "react_talk.gif"), save_all=True, append_images=imgs[1:],
                 duration=[80] * (len(imgs) - 1) + [1500], loop=0)
    print(f"wrote faces.png and {len(REACTIONS) + 1} reaction GIFs to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
