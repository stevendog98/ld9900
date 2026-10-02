"""ld9900 - one command for everything. `ld9900 <command> --help` for details."""
import importlib
import signal
import sys

from . import __version__

COMMANDS = {
    "dash":     ("dash",     "web dashboard + HTTP API (owns the display)"),
    "stream":   ("stream",   "marquee / list / free modes, faces, reactions, socket server"),
    "client":   ("client",   "call a running dashboard's API: ld9900 client react name=love"),
    "ambient":  ("ambient",  "idle mode: init config, test providers, preview"),
    "ctl":      ("ctl",      "low-level: raw bytes, cursor, brightness, flash a .c9f"),
    "firmware": ("firmware", "build a custom .c9f from the vendor's stock file; inspect images"),
    "font":     ("fontfile", "extract / edit / rebuild any of the 9 font pages in a .c9f"),
    "glyphs":   ("glyphs",   "list / show / export the custom glyph set"),
    "face":     ("face",     "render expression + reaction previews (PNG/GIF)"),
}

USAGE = "usage: ld9900 <command> [args]\n\ncommands:\n" + "\n".join(
    f"  {k:9s} {v[1]}" for k, v in COMMANDS.items()) + "\n\n  ld9900 --version"


def main(argv=None):
    if hasattr(signal, "SIGPIPE"):                 # `ld9900 ... | head` exits quietly
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if argv[0] in ("-V", "--version"):
        print(f"ld9900 {__version__}")
        return 0
    cmd = argv[0]
    if cmd not in COMMANDS:
        print(f"unknown command '{cmd}'\n\n{USAGE}", file=sys.stderr)
        return 2
    mod = importlib.import_module(f".{COMMANDS[cmd][0]}", __package__)
    return mod.main([f"ld9900 {cmd}"] + argv[1:]) or 0


if __name__ == "__main__":
    sys.exit(main())
