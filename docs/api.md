# HTTP API reference (`ld9900 dash`)

The base URL is `http://HOST:8099`. All responses are JSON. If the server was started with
`--token T`, every request needs `Authorization: Bearer T` or `?token=T`.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Dashboard |
| GET | `/api` | Endpoint index |
| GET | `/api/state` | `{mode, mode_opts, face, brightness, viewport, cells[40]}` |
| GET | `/api/meta` | Modes, expressions (with 8×2 cell previews), reactions, glyph pieces |
| GET | `/api/font` | 5×7 bitmaps for codes 0–255 (`rows[code][y]`, bit0 = leftmost) |
| GET | `/api/events` | Server-Sent Events; a `data:` line with the state on every change |
| GET | `/api/ambient` | Ambient status, provider errors, and the cards currently in rotation |
| POST | `/api/op` | One op object, a list of ops, or `{"ops":[...]}` (max 100, validated all-or-nothing) |
| POST | `/api/<op>` | Body = op fields, e.g. `POST /api/react {"name":"love"}` |
| GET | `/api/<op>?k=v` | Same as above via query string |
| GET | `/api/<op>/<value>` | Shorthand: `react/NAME`, `face/EXPR`, `mode/MODE`, `bright/N`, `text/...`, `say/...`, `idle/true` |

A request with a plain-text (non-JSON) body is treated as `{"op":"text","text":<body>}`.

Errors come back as `400 {"ok":false,"error":"..."}`, `401` or `413`. A rejected op never
reaches the display.

## Ops

| op | Fields | Notes |
|---|---|---|
| `text` | `text` (≤2000) | Feeds the current mode: appends to the marquee tape, pushes list lines, or fills free-mode rows |
| `mode` | `mode`: `marquee`\|`list`\|`free`; marquee: `speed` (0.5–60 cells/s), `row` (0\|1), `header`, `loop`; list: `hold` (s) | Switches mode |
| `header` | `text` | Marquee: static text on the other row |
| `put` | `row` 0–1, `col` 0–19, `text` | Text and `{glyph}` tokens. Persistent in free mode |
| `clear` | | Clears the screen (and the free-mode canvas) |
| `bar` | `row`, `col`, `width` 1–20, `value` 0–1 | Horizontal bar, 5 steps per cell |
| `spark` | `row`, `col`, `values` (1–20 numbers, or `"1,2,3"`), `lo`, `hi` | Vertical-bar sparkline |
| `face` | `expr`, `x` 0–12, or `off: true` | Shows, moves or hides the face. The mode uses the remaining columns |
| `react` | `name` | Plays a reaction, then returns to the base expression |
| `say` | `text` (≤300), `expr`, `hold` (s) | The mouth animates while the text types out beside the face |
| `bright` | `level` 1–4 | 20 / 40 / 60 / 100 % |
| `idle` | `on` | Random blinks and glances |
| `ambient` | `on` (force enter/exit), `enabled`, `idle_after` (s) | Ambient idle mode (dashboard started with `--ambient`) |
| `card` | `id`, `title`, `text` (scrolls if > 12 cells) or `lines` [2], `mood`, `ttl` (s, default 600), `seconds` | Add or replace an ambient card; an empty `text` deletes it |

`card`, `ambient`, `bright` and `idle` are *passive*: they don't count as activity, so they
never wake the display out of ambient mode. Every other op does.

**Expressions:** neutral happy joy laugh smile love kiss sad cry angry furious surprised shocked
confused thinking idea sleepy asleep dead dizzy starstruck smug wink playful cat embarrassed
nervous singing bored look_left look_right look_up look_down talk

**Reactions:** blink look_around nod shake laugh love angry surprise cry sleep dizzy think excited
wink nervous sing

## JSON-lines socket

`ld9900 dash` (and `ld9900 stream serve`) also listen on `127.0.0.1:7440`. Send one op object per
line, or a plain-text line that goes to the current mode. This socket has no auth and is
loopback-only by default.

```sh
echo '{"op":"react","name":"love"}' | nc -q0 127.0.0.1 7440
ld9900 stream send --tcp 127.0.0.1:7440 '{"op":"say","text":"hi"}'
```

## Python

```python
from ld9900.client import LD
ld = LD("http://display:8099", token="SECRET")    # or LD_URL / LD_TOKEN env vars
ld.react("laugh"); ld.face("happy", x=12); ld.say("hello {heart}", expr="joy")
ld.mode("marquee", speed=8, header="{signal} NEWS"); ld.text("ticker...")
with ld.batch() as b:                             # one request, all-or-nothing
    b.mode("free"); b.put(0, 8, "GPU"); b.bar(0, 12, 0.73, width=8)
for state in ld.events():                         # live updates
    print(state["mode"])
```
