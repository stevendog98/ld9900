from ld9900 import glyphs as G


def test_fits_in_one_page():
    assert len(G.CELLS) <= 128
    assert G.FREE == 128 - len(G.CELLS)


def test_cells_are_5x7():
    for name, rows in G.CELLS:
        assert len(rows) == 7, name
        assert all(len(r) == 5 and set(r) <= {"#", "."} for r in rows), name


def test_codes_unique_and_in_range():
    codes = [c for cs in G.PIECES.values() for c in cs]
    assert len(codes) == len(set(codes))
    assert all(0x80 <= c <= 0xFF for c in codes)


def test_expand_tokens():
    assert G.expand("A{heart}B") == b"A" + bytes([G.code("heart")]) + b"B"
    assert G.expand("{mouth_smile}") == G.piece("mouth_smile")
    assert G.expand("{eye_open.1}") == bytes([G.PIECES["eye_open"][1]])
    assert G.expand("{nope} x") == b"{nope} x"            # unknown tokens stay literal


def test_ascii_untouched():
    assert G.expand("Hello, 123!") == b"Hello, 123!"
