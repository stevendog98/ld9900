# Firmware reverse-engineering notes

These notes cover the Logic Controls **LCIGP v1.45** command-set image (`LCIGP_145.c9f`, dated
2016-03-01). It was tested on a Bematech **LD9900UP-GY-CM**, which uses an NXP/Freescale
**MC9S08JM32** (HCS08) and shows `LCI V1.45` on its boot screen.

## The `.c9f` file

A `.c9f` is a plain Motorola **S19** file. It is not encrypted or signed, and the bootloader
checks no checksum beyond the per-record S-record checksums. It was built with CodeWarrior.
The S0 record holds the build path. `0xFFAE/0xFFAF = 01 9D` is identical in every command
set: these are the JM's NV trim bytes, not a checksum.

The vendor ships five command sets (`LCIGP`, `EMAX`, `Firich`, `IEE`, `PTC`). All use the same
application interface to the resident boot block.

## Memory map (MC9S08JM32, 32 KB flash at 0x8000–0xFFFF, 4 KB RAM)

| Range | Contents |
|---|---|
| `0x8000–0xAF0D` | Application code. `0x8000: jmp $A5B0` |
| `0xAF0E–0xC58D` | **9 font tables**, codes 0x80–0xFF, 128 glyphs × 5 bytes = 640 bytes each |
| `0xC58E–0xC698` | Power-on strings (`V 1.45` at `0xC600`, …) |
| `0xD800–0xD819` | App header; reset stub `jmp $D815` → `jmp $8000` |
| `0xD81A–0xFBFF` | **Resident boot block, not in the file**: bootloader, USB stack, VFD scan driver, ASCII 0x20–0x7F font |
| `0xFC00–0xFDFF` | Config page (copied to RAM `$05B0` at boot; written back via `jsr $FE0C`) |
| `0xFE00–0xFE0F` | Resident service entry points (the file contains only stubs) |
| `0xFFBD/0xFFBF` | NVPROT `0xFF` / NVOPT `0xFE` (the app image is built **unsecured**) |

### Resident services
| Call | Function |
|---|---|
| `jsr $FE00` | Service poll (USB + housekeeping); called in every wait loop |
| `jmp $FE04` | Transmit the byte in `$07EC` to the host |
| `jmp $FE08` | Enter the bootloader (download mode) |
| `jsr $FE0C` | Write the RAM config back to the flash page at `$FC00` |

### RAM interface between the app and the resident renderer
| Address | Meaning |
|---|---|
| `$087F–$08A6` | 40-byte display buffer of **character codes** (row-major 2×20) |
| `$04BD–$04E4` | Second buffer (back-side display in "both sides" mode) |
| `$07EE/$07EF` | Pointer to the active 0x80–0xFF font table. Set by `0x8F37` from the font code in `$051E` |
| `$0568` | Code of the single user-defined character; `$0569–$056D` holds its packed glyph |
| `$0533` | LCI display-mode bits (bit 1 = Vertical Scroll) |
| `$08AD` | Cursor (0–39) |
| `$07ED` | Last received byte (`jsr $833B` fetches the next one) |

## Font tables

| Font code (`ESC % n`) | Table | Contents |
|---|---|---|
| 0 | `0xAF0E` | Logic Controls |
| 1 | `0xB18E` | PC437 |
| 2 | `0xB40E` | PC850 |
| 3 | `0xB90E` | PC858 |
| 4 | `0xBE0E` | PC863 |
| 5 | `0xC08E` | PC865 |
| 6 | `0xB68E` | PC852 |
| 7 | `0xC30E` | LCI variant (alternate Cyrillic) |
| 8 | `0xBB8E` | PC437 variant with € (**replaced by ld9900's custom set**) |

### Glyph encoding
Each glyph is 5 bytes, read as a **35-bit little-endian integer**. Bit `5*row + col` is the dot at
`row` 0–6 (top to bottom) and `col` 0–4 (left to right). Bits 35–39 are unused. This is the same
layout as the LCI `Down Load Font` command (`<03> X G H J K M`, where G0 = row 1/col 1).

## LCI display modes
LCI init (`0x8431`, also run on `<1F>` reset) executes `LDA #$02 / STA $0533`, which selects
**Vertical Scroll**. Writing the 40th character, or any LF, then copies row 2 to row 1 and clears
row 2. In **Normal** mode (`<11>`), the character writer at `0x8CC6` stores the byte at
`$087F + cursor` and advances the cursor, wrapping 40 → 0. `ld9900 firmware build` changes the
immediate at **`0x8440`** from `02` to `00`, so the display boots in Normal mode.

## USB
- Vendor class, VID `0x0FA8`, PID `A010` / `A030` / `A060` / `A090`. The host writes raw bytes to
  **bulk OUT EP2**, so no kernel driver is needed (the vendor's `usblcpd` doesn't build on Linux ≥ 3.15).

### Hidden service channel
The magic prefix `1B 1F 1E 13 0C` is parsed at `0x8177` (`$07EB` counts matched bytes). The next
byte selects:

| Byte | Action |
|---|---|
| `18` | `jmp $FE08`: enter the bootloader |
| `19 nn` | Set baud / pass-through, save the config, then hang until the watchdog resets |
| `1A nn` | Select command set `nn` (0–8; 1 = LCI), save, reset (`SelCmdLciGp.exe`) |

### Download protocol (`DnLoadCSF.exe` 12-02-2013)
1. `1B 1F 1E 13 0C 18`
2. Wait 10 ms, then send `Logic Controls, Inc` (19 bytes).
3. Wait for the erase (the vendor tool waits 100 × 10 ms Windows timer ticks, about 1–1.6 s).
4. Send each S-record line with its CRLF, one line per 50 ms. There are no acknowledgements.

The bootloader only writes the application region, so a failed download is recoverable:
power-cycle the display and download the stock file again.

## Other commands worth knowing (LCI)
| Bytes | Effect |
|---|---|
| `11` / `12` | Normal / Vertical Scroll mode |
| `10 nn` | Cursor to cell `nn` (0x00–0x27) |
| `13` / `14` | Cursor on / off |
| `04 nn` | Brightness (`20`, `40`, `60`, `FF`) |
| `1F` | Reset settings |
| `1B 25 n` | Select font page `n` |
| `1B 27 n m` | Save font page `n` and international set `m` as power-up defaults |
| `03 X G H J K M` | Define one temporary user glyph on ASCII `X` |
