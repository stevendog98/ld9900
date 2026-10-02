# Changelog

## 0.2.0
- **Ambient mode**: after `idle_after` seconds without activity the face moves to the right edge
  and info cards rotate on the left: clock, weather (Open-Meteo, no key), RSS/Atom news, facts,
  system, and cards pushed by other services. Face mood follows the content; quiet hours dim the
  display and the face sleeps. Any activity restores the exact previous screen (mode, canvas, face).
- New ops `ambient` and `card`; `GET /api/ambient`; dashboard Ambient panel
- `ld9900 ambient init | test | preview`; `--ambient [CONFIG]` / `--idle-after` on `dash` and `stream serve`

## 0.1.0
- `ld9900 firmware build`: reproducible custom `.c9f` from stock LCIGP v1.45 (font page 8, Normal-mode boot, boot tag)
- `ld9900 font`: extract / edit / rebuild / diff / render any of the 9 font pages
- `ld9900 ctl`: pyusb transport (no kernel driver), low-level commands, Linux flasher
- 127-glyph custom set: eyes, mouths, accessories, bars, icons
- Faces: 33 expressions, 16 reactions, talking, idle blinks
- Streaming: marquee, list, free modes; face overlay; JSON-lines socket
- `ld9900 dash`: web dashboard, REST API with validation and token auth, SSE live events
- `ld9900.client` Python client
