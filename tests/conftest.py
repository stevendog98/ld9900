"""Shared fixtures. The vendor firmware is never needed: tests build a synthetic S19 image
with the same layout markers that ld9900.fontfile.check_image() verifies."""
import pytest

from ld9900 import fontfile as F


def _srec(addr, data):
    body = addr.to_bytes(2, "big") + data
    cnt = len(body) + 1
    return f"S1{cnt:02X}{body.hex().upper()}{(~(cnt + sum(body))) & 0xFF:02X}"


def make_image(path, version=b"V 1.45"):
    mem = bytearray(b"\x9d" * (0xC700 - 0x8000))          # 'NOP' filler
    for at, want in F.SELECTOR_CHECK.items():                # LDHX #table, as in LCIGP v1.45
        mem[at - 0x8000:at - 0x8000 + 3] = bytes([0x45]) + want.to_bytes(2, "big")
    mem[0x843F - 0x8000:0x8444 - 0x8000] = F.MODE_PATCH_CONTEXT
    for at in (0xC600, 0xC60F, 0xC622):
        mem[at - 0x8000:at - 0x8000 + 6] = version
    lines = ["S00600004844521B"]
    for off in range(0, len(mem), 32):
        lines.append(_srec(0x8000 + off, bytes(mem[off:off + 32])))
    lines.append("S9030000FC")
    with open(path, "w", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")
    return path


@pytest.fixture
def stock(tmp_path):
    return str(make_image(tmp_path / "LCIGP_test.c9f"))
