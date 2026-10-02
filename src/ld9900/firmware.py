"""
Build a custom LD9900 firmware image (.c9f) from the vendor's stock command-set file.

The stock image is NOT distributed with this package (it is Logic Controls' code).
Download "Line Display Command Set Utility" from the vendor, take LCIGP_145.c9f, then:

    ld9900 firmware build LCIGP_145.c9f                 -> FACES_145.c9f
    ld9900 firmware build LCIGP_145.c9f -o my.c9f --tag MYFW01 --no-normal-mode
    ld9900 firmware info  some.c9f                      identify / sanity-check an image

What `build` changes (nothing else):
  * font page N (default 8, codes 0x80-0xFF) <- the glyph set in ld9900/glyphs.py
  * optional: power-up mode Vertical Scroll -> Normal (1 byte at 0x8440)
  * optional: boot-screen version string "V 1.45" -> a 6-char tag
"""
import argparse
import hashlib
import sys

from . import fontfile as F
from . import glyphs as G

# sha256 of known inputs/outputs, for reproducibility checks
KNOWN = {
    "2b63561d8f515c42f591370458064e87ac946553065e37e0798492e1780abca2":
        "stock LCIGP_145.c9f (Logic Controls LCIGP v1.45, dated 2016-03-01)",
    "a2fa3af1a226d7266c1b54cdc5c6b5e1a83c0c74dd0aa1943ab2999f6acf8daa":
        "FACES_145.c9f built by ld9900 0.1.0 with default options",
}
STOCK_SHA256 = "2b63561d8f515c42f591370458064e87ac946553065e37e0798492e1780abca2"
DEFAULT_TAG = "FACE01"


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def page_base(page):
    for idx, base, _name in F.PAGES:
        if idx == page:
            return base
    raise ValueError(f"no font page {page} (valid: 0-8)")


def glyph_table():
    """{code: rows} for all 128 codes of the custom page (unused codes blank)."""
    return {0x80 + k: [[1 if ch == "#" else 0 for ch in row] for row in G.glyph_rows(0x80 + k)]
            for k in range(128)}


def build(stock, out, page=G.PAGE, tag=DEFAULT_TAG, normal_mode=True, log=print):
    """Patch `stock` and write `out`. Returns a dict describing what changed."""
    digest = sha256(stock)
    if digest != STOCK_SHA256:
        log(f"warning: {stock} is not the known stock LCIGP_145.c9f (sha256 {digest[:16]}...); "
            "layout checks will still run")
    img = F.SRec(stock)
    F.check_image(img)                      # refuses anything that isn't LCIGP v1.45 layout
    base = page_base(page)
    changed = F.write_page(img, base, glyph_table())
    mode = F.patch_normal_mode(img) if normal_mode else False
    tags = F.patch_tag(img, tag) if tag else 0
    img.dump(out)

    # verify by reading the output back
    back = F.SRec(out)
    F.check_image(back)
    page_back = F.read_page(back, base)
    for k in range(128):
        if F.glyph_text(page_back[k]) != G.glyph_rows(0x80 + k):
            raise F.ImageError(f"verification failed at code 0x{0x80 + k:02X}")
    info = {"out": out, "page": page, "base": base, "glyphs_changed": changed,
            "normal_mode": mode or (normal_mode and back.read(F.MODE_PATCH_ADDR, 1) == b"\x00"),
            "tag_places": tags, "sha256": sha256(out)}
    log(f"page {page} @0x{base:04X}: {changed} glyphs written ({len(G.CELLS)} defined, {G.FREE} free)")
    if normal_mode:
        log("power-up mode: Normal (addressable)")
    if tag:
        log(f"boot tag: '{tag}' ({tags} places)")
    log(f"wrote {out}  sha256 {info['sha256']}")
    return info


def describe(path, log=print):
    digest = sha256(path)
    log(f"{path}\n  sha256  {digest}")
    if digest in KNOWN:
        log(f"  known   {KNOWN[digest]}")
    img = F.SRec(path)
    try:
        F.check_image(img)
        log("  layout  LCIGP v1.45 (font selector at 0x8F37 verified)")
    except F.ImageError as e:
        log(f"  layout  NOT LCIGP v1.45: {e}")
        return 1
    mode = img.read(F.MODE_PATCH_ADDR, 1)[0]
    log(f"  boot    {'Normal (addressable)' if mode == 0 else 'Vertical Scroll (stock)'}")
    tag = bytes(img.mem.get(0xC600 + i, 0) for i in range(6))
    log(f"  tag     {tag.decode('latin-1')!r} @0xC600")
    custom = F.read_page(img, page_base(G.PAGE))
    same = sum(F.glyph_text(custom[k]) == G.glyph_rows(0x80 + k) for k in range(128))
    log(f"  page {G.PAGE}  {same}/128 glyphs match this package's glyph set")
    return 0


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    ap = argparse.ArgumentParser(prog="ld9900 firmware", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("build", help="build a custom image from the stock LCIGP_145.c9f")
    b.add_argument("stock")
    b.add_argument("-o", "--out", default="FACES_145.c9f")
    b.add_argument("--page", type=int, default=G.PAGE, help="font page to replace (default 8)")
    b.add_argument("--tag", default=DEFAULT_TAG, help="6-char boot tag (default FACE01)")
    b.add_argument("--no-tag", action="store_true", help="keep 'V 1.45'")
    b.add_argument("--no-normal-mode", action="store_true", help="keep stock Vertical Scroll boot mode")
    i = sub.add_parser("info", help="identify and sanity-check a .c9f")
    i.add_argument("file")
    a = ap.parse_args(argv[1:])
    try:
        if a.cmd == "build":
            build(a.stock, a.out, a.page, None if a.no_tag else a.tag, not a.no_normal_mode)
            return 0
        if a.cmd == "info":
            return describe(a.file)
    except (F.ImageError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
