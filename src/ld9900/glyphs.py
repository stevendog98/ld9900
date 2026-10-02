#!/usr/bin/env python3
"""
ld9900.glyphs - the custom character page for the LD9900 (font page 8, codes 0x80-0xFF).

Single source of truth for:
  * the glyph art (5 x 7 dots per cell; multi-cell pieces are drawn as one picture
    and sliced into cells),
  * the name -> character code map used by ld9900.face / ld9900 stream,
  * exporting the page in ld9900 font's text format (ld9900 glyphs export DIR).

Standard ASCII 0x20-0x7F lives in the display's boot ROM and is untouched.

Inline tokens: anywhere ldstream accepts text, "{name}" inserts glyph `name`,
e.g. "temp 21{deg}C {heart}". Multi-cell pieces are addressed per cell:
"{eye_open.0}{eye_open.1}".
"""
import math
import os
import sys

PAGE = 8                 # firmware font page this set replaces (ESC % 8)
FIRST_CODE = 0x80
W, H = 5, 7

# ----------------------------------------------------------------------------
# helpers


def _norm(rows, width):
    rows = list(rows)
    if len(rows) != H:
        raise ValueError(f"need {H} rows, got {len(rows)}")
    for r in rows:
        if len(r) != width:
            raise ValueError(f"row {r!r} is not {width} wide")
    return rows


def slice_cells(rows):
    """Picture (7 rows x 5n cols) -> list of n cells (each 7 rows x 5)."""
    width = len(rows[0])
    if width % W:
        raise ValueError("picture width must be a multiple of 5")
    rows = _norm(rows, width)
    return [[r[c * W:(c + 1) * W] for r in rows] for c in range(width // W)]


def mirror(rows):
    return [r[::-1] for r in rows]


def flip_v(rows):
    return list(reversed(rows))


def blank(width=W):
    return ["." * width] * H


def from_fn(width, fn):
    return ["".join("#" if fn(x, y) else "." for x in range(width)) for y in range(H)]


# ----------------------------------------------------------------------------
# EYES: 2 cells (10 x 7). Robot-style filled eyes read best on a VFD.

EYES = {}

EYES["open"] = [
    "..######..",
    ".########.",
    ".########.",
    ".########.",
    ".########.",
    ".########.",
    "..######..",
]
EYES["left"] = [
    ".######...",
    "########..",
    "########..",
    "########..",
    "########..",
    "########..",
    ".######...",
]
EYES["right"] = mirror(EYES["left"])
EYES["up"] = [
    "..######..",
    ".########.",
    ".########.",
    ".########.",
    "..######..",
    "..........",
    "..........",
]
EYES["down"] = flip_v(EYES["up"])
EYES["half"] = [
    "..........",
    "..........",
    "..........",
    ".########.",
    ".########.",
    ".########.",
    "..######..",
]
EYES["closed"] = [
    "..........",
    "..........",
    "..........",
    "..........",
    ".########.",
    "..######..",
    "..........",
]
EYES["happy"] = [
    "..........",
    "...####...",
    "..######..",
    ".###..###.",
    ".##....##.",
    "..........",
    "..........",
]
EYES["wide"] = [
    "..######..",
    ".#......#.",
    "#........#",
    "#...##...#",
    "#........#",
    ".#......#.",
    "..######..",
]
EYES["heart"] = [
    ".###..###.",
    "##########",
    "##########",
    ".########.",
    "..######..",
    "...####...",
    "....##....",
]
EYES["x"] = [
    "##......##",
    ".##....##.",
    "..##..##..",
    "...####...",
    "..##..##..",
    ".##....##.",
    "##......##",
]
EYES["sleepy"] = [
    "..........",
    "..........",
    "..........",
    "##########",
    ".########.",
    ".########.",
    "..######..",
]
EYES["dizzy"] = [
    "..######..",
    ".#......#.",
    "#..####..#",
    "#.#.##.#.#",
    "#..####..#",
    ".#......#.",
    "..######..",
]
EYES["star"] = [
    "....##....",
    "....##....",
    "##########",
    ".########.",
    "..######..",
    ".###..###.",
    "##......##",
]

# asymmetric eyes: defined as the LEFT eye (viewer's left); right = mirror
EYES_LR = {}
EYES_LR["sad"] = [
    "..........",
    "........#.",
    "......###.",
    "....#####.",
    "..#######.",
    ".########.",
    "..######..",
]
EYES_LR["angry"] = [
    "..........",
    ".#........",
    ".###......",
    ".#####....",
    ".#######..",
    ".########.",
    "..######..",
]
EYES_LR["squint"] = [          # >  <  laughing / excited
    ".##.......",
    "...###....",
    ".....###..",
    ".......###",
    ".....###..",
    "...###....",
    ".##.......",
]

# ----------------------------------------------------------------------------
# MOUTHS: 4 cells (20 x 7) unless noted; drawn centred under the eyes.

MOUTHS = {}
MOUTHS["smile"] = [
    "....................",
    "#..................#",
    ".#................#.",
    "..##............##..",
    "....###......###....",
    ".......######.......",
    "....................",
]
MOUTHS["grin"] = [
    "....................",
    "####################",
    ".##################.",
    "..################..",
    "...##############...",
    ".....##########.....",
    "....................",
]
MOUTHS["frown"] = flip_v(MOUTHS["smile"])
MOUTHS["talk"] = [
    "....................",
    "......########......",
    "....##........##....",
    "...#............#...",
    "....##........##....",
    "......########......",
    "....................",
]
MOUTHS["tongue"] = [
    "....................",
    "#..................#",
    ".#................#.",
    "..################..",
    "...........####.....",
    "...........####.....",
    "............##......",
]
MOUTHS["smirk"] = [
    "....................",
    "...................#",
    "..................#.",
    "................##..",
    "....############....",
    "....................",
    "....................",
]
MOUTHS["grimace"] = from_fn(20, lambda x, y: (
    1 <= x <= 18 and 1 <= y <= 5 and (
        y in (1, 3, 5) or x in (1, 18) or (x % 4 == 3 and 1 <= y <= 5))))
_wave = [3 + round(1.4 * math.sin(2 * math.pi * x / 10)) for x in range(21)]
MOUTHS["wavy"] = from_fn(20, lambda x, y: y == _wave[x] or
                         (min(_wave[x], _wave[x + 1]) < y < max(_wave[x], _wave[x + 1])))

# 2-cell mouths (centred: drawn in the middle two of the four mouth cells)
MOUTHS2 = {}
MOUTHS2["o"] = [
    "...####...",
    ".##....##.",
    "#........#",
    "#........#",
    "#........#",
    ".##....##.",
    "...####...",
]
MOUTHS2["small_o"] = [
    "..........",
    "..........",
    "...####...",
    "..#....#..",
    "...####...",
    "..........",
    "..........",
]
MOUTHS2["cat"] = [
    "..........",
    "..........",
    "#...##...#",
    ".#.#..#.#.",
    "..#....#..",
    "..........",
    "..........",
]
# 1-cell repeating mouth
MOUTH_FLAT = [
    ".....",
    ".....",
    ".....",
    "#####",
    ".....",
    ".....",
    ".....",
]

# ----------------------------------------------------------------------------
# ACCESSORIES and SYMBOLS: single cells

SINGLE = {}
SINGLE["blush"] = [".....", ".....", "..#.#", ".#.#.", "#.#..", ".....", "....."]
SINGLE["tear"] = ["..#..", "..#..", ".#.#.", "#...#", "#...#", ".###.", "....."]
SINGLE["sweat"] = ["..#..", "..#..", ".###.", "#####", "#####", ".###.", "....."]
SINGLE["anger"] = [".#.#.", "##.##", ".....", "##.##", ".#.#.", ".....", "....."]
SINGLE["z_small"] = [".....", ".....", ".....", "####.", "..#..", ".#...", "####."]
SINGLE["z_big"] = ["#####", "...#.", "..#..", ".#...", "#....", "#####", "....."]
SINGLE["heart"] = [".....", ".#.#.", "#####", "#####", ".###.", "..#..", "....."]
SINGLE["heart_small"] = [".....", ".....", ".#.#.", ".###.", "..#..", ".....", "....."]
SINGLE["sparkle"] = ["..#..", "..#..", ".###.", "#####", ".###.", "..#..", "..#.."]
SINGLE["sparkle_small"] = [".....", ".....", "..#..", ".###.", "..#..", ".....", "....."]
SINGLE["exclaim"] = [".###.", ".###.", ".###.", "..#..", "..#..", ".....", "..#.."]
SINGLE["question"] = [".###.", "##.##", "...##", "..##.", "..#..", ".....", "..#.."]
SINGLE["note"] = ["..##.", "..#.#", "..#..", "..#..", ".##..", "###..", ".#..."]
SINGLE["bulb"] = [".###.", "#...#", "#...#", ".#.#.", ".###.", ".###.", "..#.."]
SINGLE["puff"] = [".....", ".##..", "#..##", "#...#", ".###.", ".....", "....."]
SINGLE["dots"] = [".....", ".....", ".....", ".....", ".....", "#.#.#", "....."]

# UI: bars, arrows, status icons
for k in range(1, 6):
    SINGLE[f"hbar{k}"] = from_fn(5, lambda x, y, k=k: x < k)          # hbar5 = full block
for k in range(1, 8):
    SINGLE[f"vbar{k}"] = from_fn(5, lambda x, y, k=k: y >= H - k)     # vbar7 = full block
SINGLE["up"] = ["..#..", ".###.", "#####", "..#..", "..#..", "..#..", "..#.."]
SINGLE["down"] = flip_v(SINGLE["up"])
SINGLE["left"] = [".....", "..#..", ".##..", "#####", ".##..", "..#..", "....."]
SINGLE["right"] = mirror(SINGLE["left"])
SINGLE["check"] = [".....", "....#", "...##", "#.##.", "###..", ".#...", "....."]
SINGLE["cross"] = [".....", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "....."]
for k in range(5):  # battery 0..4 (interior rows 2..5 filled from the bottom)
    SINGLE[f"batt{k}"] = [".###.", "#####"] + \
        ["#####" if (5 - r) < k else "#...#" for r in range(2, 6)] + ["#####"]
SINGLE["signal"] = ["....#", "....#", "..#.#", "..#.#", "#.#.#", "#.#.#", "#.#.#"]
SINGLE["bell"] = ["..#..", ".###.", ".###.", ".###.", "#####", ".....", "..#.."]
SINGLE["lock"] = [".###.", "#...#", "#...#", "#####", "##.##", "##.##", "#####"]
SINGLE["clock"] = [".....", ".###.", "#.#.#", "#.###", "#...#", ".###.", "....."]
SINGLE["sun"] = ["#.#.#", ".###.", "#####", ".###.", "#.#.#", ".....", "....."]
SINGLE["cloud"] = [".....", ".....", "..##.", ".####", "#####", ".....", "....."]
SINGLE["warn"] = ["..#..", ".###.", ".#.#.", "##.##", "#####", "##.##", "#####"]
SINGLE["home"] = ["..#..", ".###.", "#####", ".###.", ".#.#.", ".#.#.", ".###."]
SINGLE["deg"] = [".##..", "#..#.", ".##..", ".....", ".....", ".....", "....."]

# ----------------------------------------------------------------------------
# assemble the page: name -> list of cell codes


def _build():
    cells = []        # list of (name, cell_rows)
    pieces = {}       # piece name -> [codes]

    def add(name, cell_list):
        codes = []
        for i, c in enumerate(cell_list):
            codes.append(FIRST_CODE + len(cells))
            cells.append((f"{name}.{i}" if len(cell_list) > 1 else name, c))
        pieces[name] = codes

    for k, art in EYES.items():
        add(f"eye_{k}", slice_cells(art))
    for k, art in EYES_LR.items():
        add(f"eyeL_{k}", slice_cells(art))
        add(f"eyeR_{k}", slice_cells(mirror(art)))
    for k, art in MOUTHS.items():
        add(f"mouth_{k}", slice_cells(art))
    for k, art in MOUTHS2.items():
        add(f"mouth_{k}", slice_cells(art))
    add("mouth_flat", [MOUTH_FLAT])
    for k, art in SINGLE.items():
        add(k, [art])
    if len(cells) > 128:
        raise SystemExit(f"too many glyphs: {len(cells)} > 128")
    return cells, pieces


CELLS, PIECES = _build()
FREE = 128 - len(CELLS)


def code(name):
    """Code of a single-cell glyph, or of one cell: 'eye_open.1'."""
    if "." in name:
        base, idx = name.rsplit(".", 1)
        return PIECES[base][int(idx)]
    p = PIECES[name]
    if len(p) != 1:
        raise KeyError(f"{name} is {len(p)} cells wide; use {name}.0 .. {name}.{len(p) - 1}")
    return p[0]


def piece(name):
    """Bytes for a whole (multi-cell) piece."""
    return bytes(PIECES[name])


def expand(text):
    """Replace {name} tokens with glyph codes; returns bytes (latin-1)."""
    out = bytearray()
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "{":
            j = text.find("}", i)
            if j > i:
                name = text[i + 1:j]
                try:
                    if name in PIECES:
                        out += piece(name)
                    else:
                        out.append(code(name))
                    i = j + 1
                    continue
                except (KeyError, ValueError, IndexError):
                    pass
        out += ch.encode("latin-1", "replace")
        i += 1
    return bytes(out)


def glyph_rows(c):
    """5x7 art for a custom code (0x80+); blank if unused."""
    idx = c - FIRST_CODE
    return CELLS[idx][1] if 0 <= idx < len(CELLS) else blank()


def export_page(path):
    with open(path, "w") as f:
        f.write(f"; LD9900 custom page {PAGE} - generated by ld9900.glyphs ({len(CELLS)} glyphs, "
                f"{FREE} free)\n; Select with ESC % {PAGE}; save as default with ESC ' {PAGE} 0\n\n")
        for k in range(128):
            name = CELLS[k][0] if k < len(CELLS) else "(free)"
            f.write(f"[0x{FIRST_CODE + k:02X}]  ; {name}\n")
            for r in glyph_rows(FIRST_CODE + k):
                f.write(r + "\n")
            f.write("\n")


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "export":
        outdir = argv[2] if len(argv) > 2 else "font"
        os.makedirs(outdir, exist_ok=True)
        p = os.path.join(outdir, f"{PAGE}_page{PAGE}.txt")
        export_page(p)
        print(f"wrote {p}: {len(CELLS)} glyphs, {FREE} free slots")
    elif cmd == "list":
        for name, codes in PIECES.items():
            print(f"{name:18s} " + " ".join(f"{c:02X}" for c in codes))
    elif cmd == "show" and len(argv) > 2:
        for name in argv[2:]:
            codes = PIECES[name]
            for y in range(H):
                print("  ".join(glyph_rows(c)[y] for c in codes))
            print()
    else:
        print("usage: ld9900 glyphs list | show NAME... | export [DIR]")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
