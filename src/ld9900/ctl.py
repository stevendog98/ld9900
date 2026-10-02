#!/usr/bin/env python3
"""
ld9900 ctl - userspace control + firmware flasher for Logic Controls / Bematech
           LD9900 / LDX9000 / PDX3000 USB line displays. Replaces the vendor's
           usblcpd kernel module (which doesn't build on kernels >= 3.15).

Requires: pip install pyusb   (and libusb-1.0)

The display is a vendor-class USB device, VID 0x0FA8, PID A010/A030/A060/A090.
The host only ever writes raw bytes to bulk OUT endpoint 2, exactly as the
vendor driver does. All commands below assume the LCI command set.

Usage
-----
  ld9900 ctl info                         show the device and its endpoints
  ld9900 ctl raw 11 10 00 41              send raw hex bytes
  ld9900 ctl text "hello"                 send text at the current cursor
  ld9900 ctl normal                       Normal (addressable) mode      <11>
  ld9900 ctl vscroll                      Vertical Scroll mode           <12>
  ld9900 ctl pos N                        move cursor to cell 0..39      <10 N>
  ld9900 ctl put ROW COL "text"           normal mode, place text at row 0/1, col 0..19
  ld9900 ctl screen "top line" "bottom"   write all 40 cells (padded/truncated)
  ld9900 ctl clear                        blank all 40 cells (normal mode)
  ld9900 ctl reset                        <1F> power-on reset of settings
  ld9900 ctl cursor on|off                <13> / <14>
  ld9900 ctl bright 1..4                  20/40/60/100 %
  ld9900 ctl font N                       ESC % N: select 0x80-0xFF font page
  ld9900 ctl glyph ASCII ROWS             one-off user glyph via <03>; ROWS = 7 rows of 5
                                        joined by '/', e.g. ".###./#...#/..."
  ld9900 ctl cmdset N                     !! select command set N and SAVE (1 = LCI)
  ld9900 ctl flash FILE.c9f [--yes]       !! download a command-set image
            [--line-delay 0.05] [--erase-wait 2.0]

Flash protocol (recovered from DnLoadCSF.exe 12-02-2013 and LCIGP v1.45):
  1. 1B 1F 1E 13 0C 18      magic -> application does jmp $FE08 (bootloader)
  2. wait 10 ms, send "Logic Controls, Inc" (19 bytes)
  3. wait for erase (vendor: 100 x 10 ms Windows timer ticks)
  4. every S-record line incl. CRLF, one line per 50 ms; no acknowledgements
If a flash goes wrong, the resident bootloader is untouched: power-cycle and
flash the stock image again.
"""
import sys
import time

VID = 0x0FA8
PIDS = (0xA010, 0xA030, 0xA060, 0xA090)
EP_OUT_DEFAULT = 0x02

MAGIC = bytes([0x1B, 0x1F, 0x1E, 0x13, 0x0C])
HANDSHAKE = b"Logic Controls, Inc"


class Display:
    def __init__(self, dev=None):
        import usb.core
        import usb.util
        self.usb = usb
        if dev is None:
            devs = [d for d in usb.core.find(find_all=True, idVendor=VID) if d.idProduct in PIDS]
            if not devs:
                raise SystemExit("No Logic Controls display found (VID 0FA8). Check cable / permissions.")
            if len(devs) > 1:
                raise SystemExit("More than one display connected; unplug the others.")
            dev = devs[0]
        self.dev = dev
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)  # e.g. vendor usblcpd module
        except (NotImplementedError, usb.core.USBError):
            pass
        try:
            dev.set_configuration()
        except usb.core.USBError:
            pass  # already configured
        cfg = dev.get_active_configuration()
        intf = cfg[(0, 0)]
        ep = usb.util.find_descriptor(
            intf, custom_match=lambda e:
            usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
            and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK)
        self.ep_out = ep.bEndpointAddress if ep is not None else EP_OUT_DEFAULT
        self.intf = intf

    def write(self, data, timeout=10000):
        data = bytes(data)
        sent = 0
        while sent < len(data):
            sent += self.dev.write(self.ep_out, data[sent:], timeout=timeout)
        return sent

    def describe(self):
        d = self.dev
        print(f"Bus {d.bus:03d} Dev {d.address:03d}  ID {d.idVendor:04x}:{d.idProduct:04x}  "
              f"bcdDevice {d.bcdDevice:04x}")
        for attr in ("manufacturer", "product", "serial_number"):
            try:
                print(f"  {attr}: {getattr(d, attr)}")
            except Exception:
                pass
        for e in self.intf:
            print(f"  endpoint 0x{e.bEndpointAddress:02x} attr 0x{e.bmAttributes:02x} "
                  f"maxpkt {e.wMaxPacketSize}")
        print(f"  using OUT endpoint 0x{self.ep_out:02x}")


# ---------------------------------------------------------------- helpers
def pack_glyph(rows):
    if len(rows) != 7 or any(len(r) != 5 for r in rows):
        raise SystemExit("glyph needs 7 rows of 5 characters")
    v = 0
    for r, row in enumerate(rows):
        for c, ch in enumerate(row):
            if ch in "#X1*@":
                v |= 1 << (5 * r + c)
    return v.to_bytes(5, "little")


def enc(text):
    return text.encode("latin-1")


def screen_bytes(top, bottom):
    cells = top[:20].ljust(20) + bottom[:20].ljust(20)
    # normal mode, home, 40 cells; writing cell 39 wraps cursor to 0 (no scroll)
    return bytes([0x11, 0x10, 0x00]) + enc(cells)


def flash(disp, path, line_delay=0.05, erase_wait=2.0, log=print):
    with open(path, "rb") as f:
        raw = f.read()
    lines = raw.splitlines(keepends=True)
    recs = [l if l.endswith(b"\n") else l + b"\r\n" for l in lines if l.strip()]
    if not recs or not all(l.startswith(b"S") for l in recs):
        raise SystemExit(f"{path} is not an S-record file")
    # verify checksums before touching the device
    for n, l in enumerate(recs, 1):
        s = l.strip()
        b = bytes.fromhex(s[2:].decode())
        if b[0] != len(b) - 1 or sum(b) & 0xFF != 0xFF:
            raise SystemExit(f"{path}:{n}: bad S-record")
    log(f"{len(recs)} records OK. Entering bootloader...")
    disp.write(MAGIC + b"\x18")
    time.sleep(0.010)
    disp.write(HANDSHAKE)
    log(f"Erasing (waiting {erase_wait:.1f}s)...")
    time.sleep(erase_wait)
    t0 = time.time()
    for n, l in enumerate(recs, 1):
        disp.write(l)
        if n % 25 == 0 or n == len(recs):
            log(f"  {n}/{len(recs)} lines  ({time.time() - t0:.0f}s)")
        time.sleep(line_delay)
    log("Done. The display should show the command-set name and version.")


# ---------------------------------------------------------------- CLI
def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, args = argv[1], argv[2:]
    d = Display()

    if cmd == "info":
        d.describe()
    elif cmd == "raw":
        d.write(bytes(int(x, 16) for x in args))
    elif cmd == "text":
        d.write(enc(" ".join(args)))
    elif cmd == "normal":
        d.write(b"\x11")
    elif cmd == "vscroll":
        d.write(b"\x12")
    elif cmd == "pos":
        n = int(args[0], 0)
        if not 0 <= n <= 39:
            raise SystemExit("position must be 0..39")
        d.write(bytes([0x10, n]))
    elif cmd == "put":
        row, col, text = int(args[0]), int(args[1]), " ".join(args[2:])
        if row not in (0, 1) or not 0 <= col <= 19:
            raise SystemExit("row 0..1, col 0..19")
        text = text[:20 - col]  # don't spill into the other row
        d.write(bytes([0x11, 0x10, row * 20 + col]) + enc(text))
    elif cmd == "screen":
        top = args[0] if args else ""
        bot = args[1] if len(args) > 1 else ""
        d.write(screen_bytes(top, bot))
    elif cmd == "clear":
        d.write(screen_bytes("", "") + b"\x10\x00")
    elif cmd == "reset":
        d.write(b"\x1f")
    elif cmd == "cursor":
        d.write(b"\x13" if args and args[0] == "on" else b"\x14")
    elif cmd == "bright":
        lvl = {1: 0x20, 2: 0x40, 3: 0x60, 4: 0xFF}[int(args[0])]
        d.write(bytes([0x04, lvl]))
    elif cmd == "font":
        d.write(bytes([0x1B, 0x25, int(args[0], 0)]))
    elif cmd == "glyph":
        ch = args[0]
        code = ord(ch) if len(ch) == 1 else int(ch, 0)
        if not 0x20 <= code <= 0x7F:
            raise SystemExit("glyph target must be 0x20..0x7F")
        d.write(bytes([0x03, code]) + pack_glyph(args[1].split("/")))
    elif cmd == "cmdset":
        n = int(args[0], 0)
        if not 0 <= n <= 8:
            raise SystemExit("command set 0..8")
        d.write(MAGIC + bytes([0x1A, n]))
        print("Sent; the display saves the setting and resets.")
    elif cmd == "flash":
        path = args[0]
        yes = "--yes" in args
        line_delay, erase_wait = 0.05, 2.0
        if "--line-delay" in args:
            line_delay = float(args[args.index("--line-delay") + 1])
        if "--erase-wait" in args:
            erase_wait = float(args[args.index("--erase-wait") + 1])
        if not yes:
            ans = input(f"Flash {path} to the display? Don't send anything else meanwhile. [y/N] ")
            if ans.strip().lower() != "y":
                return 1
        flash(d, path, line_delay, erase_wait)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
