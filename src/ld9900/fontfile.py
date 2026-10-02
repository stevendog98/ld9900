#!/usr/bin/env python3
"""
ld9900 font - character-set editor for Logic Controls / Bematech LD9900 / LDX9000
            family line displays (MC9S08JM32, "command set" .c9f firmware files).

Works on the LCIGP_145.c9f command-set image (LCI v1.45). The application image
holds nine 128-glyph tables for codes 0x80-0xFF. Each glyph is 5x7 dots packed
into 5 bytes:

    35-bit little-endian integer, bit (5*row + col) = dot at row 0..6, col 0..4
    row 0 = top, col 0 = left. Bits 35..39 are unused (zero).

This is the same bit layout as the LCI "Down Load Font" command
<03> X G H J K M (G0 = row1/col1 ... M2 = row7/col5).

Commands
--------
  extract  IN.c9f OUTDIR         write one editable .txt per code page + PNG sheets
  build    IN.c9f FONTDIR OUT.c9f [--tag XXXXXX] [--normal-mode]
                                 patch edited pages into the image, emit a new .c9f
                                 --normal-mode: LCI boots in Normal (addressable)
                                 mode instead of Vertical Scroll
  render   IN.c9f OUT.png        PNG sheet of every page in an image
  preview  FONTDIR PAGE CODE ASCII
                                 print the LCI <03> command bytes that temporarily
                                 show glyph CODE (from PAGE) on ASCII char ASCII,
                                 for testing a design live before flashing
  diff     A.c9f B.c9f           list differing addresses

Glyph text format (pages/NN_name.txt):
    [0x80]
    .###.
    #...#
    ... (7 rows of 5 chars; '#' or 'X' or '1' = dot on, anything else = off)
Blank lines and lines starting with ';' are ignored.
"""
import os
import re
import sys

APP_LO, APP_HI = 0x8000, 0x10000

# Page index = value the firmware keeps at RAM $051E (font code), resolved by the
# selector routine at 0x8F37 in LCIGP_145, which stores the table base at RAM $07EE.
PAGES = [
    (0, 0xAF0E, "LCI"),
    (1, 0xB18E, "PC437"),
    (2, 0xB40E, "PC850"),
    (3, 0xB90E, "PC858"),
    (4, 0xBE0E, "PC863"),
    (5, 0xC08E, "PC865"),
    (6, 0xB68E, "PC852"),
    (7, 0xC30E, "page7"),
    (8, 0xBB8E, "page8"),
]
GLYPHS = 128
GLYPH_BYTES = 5
PAGE_BYTES = GLYPHS * GLYPH_BYTES  # 0x280

# Fingerprint used to refuse patching a different build by accident:
# the selector code at 0x8F40.. must load each table address with LDHX #imm.
SELECTOR_CHECK = {0x8F40: 0xB18E, 0x8F49: 0xB40E, 0x8F52: 0xB90E, 0x8F5B: 0xB68E,
                  0x8F64: 0xBE0E, 0x8F6D: 0xC08E, 0x8F76: 0xC30E, 0x8F7F: 0xBB8E,
                  0x8F84: 0xAF0E}


# ---------------------------------------------------------------- S-records
class SRec:
    """Keeps the original record list so the output keeps identical layout."""

    def __init__(self, path):
        self.lines = []      # (type, addr, data bytes) or (type, raw) for S0/S9
        self.mem = {}
        with open(path, "r", errors="replace") as f:
            for n, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                if not line.startswith("S"):
                    raise ValueError(f"{path}:{n}: not an S-record")
                t = line[1]
                cnt = int(line[2:4], 16)
                raw = bytes.fromhex(line[4:4 + 2 * cnt])
                if len(raw) != cnt:
                    raise ValueError(f"{path}:{n}: short record")
                if (sum(bytes.fromhex(line[2:4])) + sum(raw[:-1]) + raw[-1]) & 0xFF != 0xFF:
                    raise ValueError(f"{path}:{n}: bad checksum")
                if t in "123":
                    al = {"1": 2, "2": 3, "3": 4}[t]
                    addr = int.from_bytes(raw[:al], "big")
                    data = bytearray(raw[al:-1])
                    self.lines.append([t, addr, data])
                    for i, b in enumerate(data):
                        self.mem[addr + i] = b
                else:
                    self.lines.append([t, None, line])

    def read(self, addr, n):
        out = bytearray()
        for a in range(addr, addr + n):
            if a not in self.mem:
                raise ValueError(f"address {a:04X} not present in image")
            out.append(self.mem[a])
        return bytes(out)

    def write(self, addr, data):
        for i, b in enumerate(data):
            if addr + i not in self.mem:
                raise ValueError(f"address {addr + i:04X} not present in image")
            self.mem[addr + i] = b

    def dump(self, path):
        with open(path, "w", newline="\r\n") as f:
            for rec in self.lines:
                t = rec[0]
                if t in "123":
                    al = {"1": 2, "2": 3, "3": 4}[t]
                    addr, n = rec[1], len(rec[2])
                    data = bytes(self.mem[addr + i] for i in range(n))
                    body = addr.to_bytes(al, "big") + data
                    cnt = len(body) + 1
                    cs = (~(cnt + sum(body))) & 0xFF
                    f.write(f"S{t}{cnt:02X}{body.hex().upper()}{cs:02X}\n")
                else:
                    f.write(rec[2] + "\n")


class ImageError(ValueError):
    """The S-record image is not the expected LCIGP v1.45 layout."""


def check_image(img):
    for at, want in SELECTOR_CHECK.items():
        b = img.read(at, 3)
        if b[0] != 0x45 or int.from_bytes(b[1:], "big") != want:
            raise ImageError(
                f"Image doesn't match LCIGP v1.45 layout (expected LDHX #${want:04X} at {at:04X}). "
                "Refusing to patch.")


# ---------------------------------------------------------------- glyphs
def unpack(b):
    v = int.from_bytes(b, "little")
    return [[(v >> (5 * r + c)) & 1 for c in range(5)] for r in range(7)]


def pack(rows):
    v = 0
    for r in range(7):
        for c in range(5):
            if rows[r][c]:
                v |= 1 << (5 * r + c)
    return v.to_bytes(5, "little")


def glyph_text(rows):
    return ["".join("#" if d else "." for d in row) for row in rows]


def page_filename(idx, name):
    return f"{idx}_{name}.txt"


def read_page(img, base):
    return [unpack(img.read(base + GLYPH_BYTES * k, GLYPH_BYTES)) for k in range(GLYPHS)]


def write_page_text(path, idx, name, base, glyphs):
    with open(path, "w") as f:
        f.write(f"; LD9900 / LCIGP v1.45 font page {idx} ({name}), table @ 0x{base:04X}\n")
        f.write(f"; Selected with font code {idx} (LCI: ESC % n). Codes 0x80-0xFF.\n")
        f.write("; Edit dots: '#' = on, '.' = off. 5 columns x 7 rows per glyph.\n\n")
        for k, g in enumerate(glyphs):
            f.write(f"[0x{0x80 + k:02X}]\n")
            for row in glyph_text(g):
                f.write(row + "\n")
            f.write("\n")


def parse_page_text(path):
    glyphs = {}
    cur, rows = None, []
    with open(path) as f:
        for n, line in enumerate(f, 1):
            s = line.rstrip("\r\n")
            if not s.strip() or s.lstrip().startswith(";"):
                continue
            m = re.match(r"\s*\[\s*(0x[0-9A-Fa-f]+|\d+)\s*\]", s)
            if m:
                if cur is not None and len(rows) != 7:
                    raise SystemExit(f"{path}:{n}: glyph 0x{cur:02X} has {len(rows)} rows (need 7)")
                cur = int(m.group(1), 0)
                if not 0x80 <= cur <= 0xFF:
                    raise SystemExit(f"{path}:{n}: code 0x{cur:02X} out of range 0x80-0xFF")
                rows = []
                glyphs[cur] = rows
                continue
            if cur is None:
                raise SystemExit(f"{path}:{n}: dot row before any [0xNN] header")
            row = s.strip()
            if len(row) != 5:
                raise SystemExit(f"{path}:{n}: row '{row}' must be exactly 5 chars")
            if len(rows) >= 7:
                raise SystemExit(f"{path}:{n}: glyph 0x{cur:02X} has more than 7 rows")
            rows.append([1 if ch in "#X1*@" else 0 for ch in row])
    if cur is not None and len(rows) != 7:
        raise SystemExit(f"{path}: glyph 0x{cur:02X} has {len(rows)} rows (need 7)")
    return glyphs


# ---------------------------------------------------------------- render
def render_png(pages, out, scale=4):
    from PIL import Image, ImageDraw
    cell_w, cell_h = 5 * scale + 2 * scale, 7 * scale + 3 * scale
    label_h = 14
    margin = 28
    page_w = margin + 16 * cell_w
    page_h = label_h + margin // 2 + 8 * cell_h + 8
    img = Image.new("RGB", (page_w, page_h * len(pages)), (12, 14, 16))
    d = ImageDraw.Draw(img)
    on, off = (90, 255, 210), (30, 40, 40)
    for p, (title, glyphs) in enumerate(pages):
        y0 = p * page_h
        d.text((4, y0 + 2), title, fill=(230, 230, 230))
        for c in range(16):
            d.text((margin + c * cell_w + 2, y0 + label_h), f"{c:X}", fill=(140, 140, 140))
        for r in range(8):
            d.text((2, y0 + label_h + 12 + r * cell_h + 6), f"{8 + r:X}0", fill=(140, 140, 140))
        for k, g in enumerate(glyphs):
            gx = margin + (k % 16) * cell_w
            gy = y0 + label_h + 12 + (k // 16) * cell_h
            for rr in range(7):
                for cc in range(5):
                    x, y = gx + cc * scale, gy + rr * scale
                    d.rectangle([x, y, x + scale - 2, y + scale - 2], fill=on if g[rr][cc] else off)
    img.save(out)


# ---------------------------------------------------------------- commands
def cmd_extract(src, outdir):
    img = SRec(src)
    check_image(img)
    os.makedirs(outdir, exist_ok=True)
    sheets = []
    for idx, base, name in PAGES:
        g = read_page(img, base)
        write_page_text(os.path.join(outdir, page_filename(idx, name)), idx, name, base, g)
        sheets.append((f"page {idx}  {name}  @0x{base:04X}", g))
    render_png(sheets, os.path.join(outdir, "font_sheet.png"))
    print(f"wrote {len(PAGES)} pages + font_sheet.png to {outdir}")


class PatchError(ImageError):
    pass


MODE_PATCH_ADDR = 0x8440
MODE_PATCH_CONTEXT = bytes([0xA6, 0x02, 0xC7, 0x05, 0x33])   # LDA #$02 / STA $0533 @ 0x843F


def patch_normal_mode(img):
    """LCI init @0x8431 does LDA #$02 / STA $0533 (power-up = Vertical Scroll).
    Make the immediate 0 so power-up and <1F> reset give Normal (addressable) mode."""
    cur = img.read(0x843F, 5)
    if cur == bytes([0xA6, 0x00, 0xC7, 0x05, 0x33]):
        return False                                  # already patched
    if cur != MODE_PATCH_CONTEXT:
        raise PatchError("LCI init at 0x843F doesn't match the expected code; not patching mode")
    img.write(MODE_PATCH_ADDR, b"\x00")
    return True


def patch_tag(img, tag, old=b"V 1.45"):
    """Replace the 6-char version string shown on the boot screen. Returns count replaced."""
    tb = tag.encode("ascii")
    if len(tb) != len(old):
        raise PatchError(f"tag must be exactly {len(old)} ASCII characters (replaces {old.decode()!r})")
    lo = min(img.mem)
    blob = bytes(img.mem.get(a, 0xFF) for a in range(lo, 0xD800))
    n = 0
    for m in re.finditer(re.escape(old), blob):
        img.write(lo + m.start(), tb)
        n += 1
    return n


def write_page(img, base, glyphs):
    """glyphs: {code: rows}. Returns number of changed glyphs."""
    changed = 0
    for code, rows in glyphs.items():
        addr = base + (code - 0x80) * GLYPH_BYTES
        new = pack(rows)
        if img.read(addr, GLYPH_BYTES) != new:
            img.write(addr, new)
            changed += 1
    return changed


def cmd_build(src, fontdir, dst, tag=None, normal_mode=False):
    img = SRec(src)
    check_image(img)
    try:
        if normal_mode and patch_normal_mode(img):
            print("  LCI power-up mode: Vertical Scroll -> Normal (addressable, 40 cells)")
        changed = 0
        for idx, base, name in PAGES:
            path = os.path.join(fontdir, page_filename(idx, name))
            if os.path.exists(path):
                n = write_page(img, base, parse_page_text(path))
                if n:
                    print(f"  page {idx} ({name}): {n} glyph(s) changed")
                changed += n
        if tag is not None:
            n = patch_tag(img, tag)
            print(f"  version tag 'V 1.45' -> '{tag}' ({n} places)")
    except PatchError as e:
        raise SystemExit(str(e))
    img.dump(dst)
    print(f"{changed} glyph(s) changed -> {dst}")


def cmd_render(src, out):
    img = SRec(src)
    check_image(img)
    render_png([(f"page {i}  {n}  @0x{b:04X}", read_page(img, b)) for i, b, n in PAGES], out)
    print(f"wrote {out}")


def cmd_preview(fontdir, page, code, ascii_code):
    page = int(page, 0)
    code = int(code, 0)
    ascii_code = int(ascii_code, 0)
    name = dict((i, n) for i, _, n in PAGES)[page]
    g = parse_page_text(os.path.join(fontdir, page_filename(page, name)))[code]
    payload = bytes([0x03, ascii_code]) + pack(g)
    print("LCI 'Down Load Font' command:", " ".join(f"{b:02X}" for b in payload))
    print("Send those bytes, then the character itself (0x%02X) to display it." % ascii_code)
    print("Note: stock firmware holds only ONE downloaded glyph at a time.")


def cmd_diff(a, b):
    A, B = SRec(a), SRec(b)
    keys = sorted(set(A.mem) | set(B.mem))
    run = None
    for k in keys + [None]:
        diff = k is not None and A.mem.get(k) != B.mem.get(k)
        if diff and run is None:
            run = k
        if not diff and run is not None:
            print(f"{run:04X}-{prev:04X} ({prev - run + 1} bytes)")
            run = None
        prev = k


def main(argv=None):
    try:
        return _main(list(sys.argv if argv is None else argv))
    except (ImageError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


def _main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    c = argv[1]
    if c == "extract" and len(argv) == 4:
        cmd_extract(argv[2], argv[3])
    elif c == "build" and len(argv) >= 5:
        rest, tag, normal = argv[5:], None, False
        while rest:
            if rest[0] == "--tag" and len(rest) > 1:
                tag, rest = rest[1], rest[2:]
            elif rest[0] == "--normal-mode":
                normal, rest = True, rest[1:]
            else:
                print(__doc__)
                return 1
        cmd_build(argv[2], argv[3], argv[4], tag, normal)
    elif c == "render" and len(argv) == 4:
        cmd_render(argv[2], argv[3])
    elif c == "preview" and len(argv) == 6:
        cmd_preview(*argv[2:6])
    elif c == "diff" and len(argv) == 4:
        cmd_diff(argv[2], argv[3])
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
