import random

import pytest

from ld9900 import firmware, fontfile as F, glyphs as G


def test_pack_unpack_roundtrip():
    rng = random.Random(1)
    for _ in range(200):
        rows = [[rng.randint(0, 1) for _ in range(5)] for _ in range(7)]
        assert F.unpack(F.pack(rows)) == rows


def test_glyph_bit_layout_matches_lci_download_font():
    # manual: G0 = row1/col1, G1 = row1/col2 ... M2 = row7/col5
    rows = [[0] * 5 for _ in range(7)]
    rows[0][0] = 1
    assert F.pack(rows) == b"\x01\x00\x00\x00\x00"
    rows = [[0] * 5 for _ in range(7)]
    rows[6][4] = 1
    assert F.pack(rows) == b"\x00\x00\x00\x00\x04"


def test_srec_roundtrip_identical(stock, tmp_path):
    out = tmp_path / "rt.c9f"
    F.SRec(stock).dump(out)
    assert out.read_bytes() == open(stock, "rb").read()


def test_build_patches_only_expected_bytes(stock, tmp_path):
    out = str(tmp_path / "out.c9f")
    info = firmware.build(stock, out, log=lambda *a: None)
    a, b = F.SRec(stock), F.SRec(out)
    base = firmware.page_base(G.PAGE)
    allowed = set(range(base, base + 640)) | {F.MODE_PATCH_ADDR}
    allowed |= {at + i for at in (0xC600, 0xC60F, 0xC622) for i in range(6)}
    changed = {k for k in a.mem if a.mem[k] != b.mem[k]}
    assert changed and changed <= allowed
    assert b.read(F.MODE_PATCH_ADDR, 1) == b"\x00"
    assert b.read(0xC600, 6) == b"FACE01"
    assert info["tag_places"] == 3
    page = F.read_page(b, base)
    assert all(F.glyph_text(page[k]) == G.glyph_rows(0x80 + k) for k in range(128))


def test_build_options(stock, tmp_path):
    out = str(tmp_path / "o.c9f")
    firmware.build(stock, out, tag=None, normal_mode=False, log=lambda *a: None)
    b = F.SRec(out)
    assert b.read(F.MODE_PATCH_ADDR, 1) == b"\x02"
    assert b.read(0xC600, 6) == b"V 1.45"


def test_build_is_deterministic(stock, tmp_path):
    o1, o2 = str(tmp_path / "1.c9f"), str(tmp_path / "2.c9f")
    firmware.build(stock, o1, log=lambda *a: None)
    firmware.build(stock, o2, log=lambda *a: None)
    assert open(o1, "rb").read() == open(o2, "rb").read()


def test_rejects_wrong_layout(tmp_path):
    from conftest import _srec
    p = tmp_path / "other.c9f"
    p.write_text("\n".join([_srec(0x8000 + o, b"\x00" * 32) for o in range(0, 0x4800, 32)]) + "\n")
    with pytest.raises(F.ImageError):
        firmware.build(str(p), str(tmp_path / "x.c9f"), log=lambda *a: None)


def test_rejects_bad_checksum(stock, tmp_path):
    lines = open(stock).read().splitlines()
    lines[5] = lines[5][:-2] + ("00" if lines[5][-2:] != "00" else "01")
    bad = tmp_path / "bad.c9f"
    bad.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError):
        F.SRec(str(bad))


def test_bad_tag_length(stock, tmp_path):
    with pytest.raises(F.PatchError):
        firmware.build(stock, str(tmp_path / "t.c9f"), tag="TOO-LONG", log=lambda *a: None)


def test_font_text_roundtrip(stock, tmp_path):
    img = F.SRec(stock)
    base = firmware.page_base(G.PAGE)
    F.write_page(img, base, firmware.glyph_table())
    p = tmp_path / "page.txt"
    F.write_page_text(p, 8, "page8", base, F.read_page(img, base))
    parsed = F.parse_page_text(p)
    assert len(parsed) == 128
    assert all(F.pack(parsed[c]) == F.pack(firmware.glyph_table()[c]) for c in parsed)
