"""Stamp a scene marker into a demo timeline (stdlib only).

Usage:
    uv run python scripts/mark.py TIMELINE rec_start [--sync-ms MS]
    uv run python scripts/mark.py TIMELINE start SCENE_ID [--window WxH --page WxH --dpr N]
    uv run python scripts/mark.py TIMELINE beat SCENE_ID
    uv run python scripts/mark.py TIMELINE end SCENE_ID

Each call appends one JSON line ``{"scene": ..., "event": ..., "t_ms": ...}``
with the current wall-clock time in milliseconds. ``rec_start`` marks the moment
the screencast started. ``--sync-ms`` records the wall-clock time that
``__demo.sync()`` returned: compose finds that flash in the recording and lines
the timeline up on it, which is exact however long the call took to arrive.
A phone scene's ``start`` also records the window and emulated page sizes (CSS
pixels) and the device pixel ratio, so compose can crop the page out of the take.
``beat`` marks the moment just before an action that has
an ``at`` phrase, so compose can start that part of the scene on its phrase. A re-recording appends a new ``rec_start``; compose uses
only the events after the last one, so earlier takes can stay in the file.
"""

import argparse
import json
import sys
import time
from pathlib import Path

SCENE_EVENTS = ("start", "beat", "end")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mark", description=__doc__.splitlines()[0])
    parser.add_argument("timeline", type=Path, help="timeline.jsonl to append to")
    parser.add_argument("event", choices=("rec_start", *SCENE_EVENTS))
    parser.add_argument("scene", nargs="?", help="scene id (required for start/beat/end)")
    parser.add_argument(
        "--sync-ms", type=int, metavar="MS", help="rec_start only: the value __demo.sync() returned"
    )
    phone_sizes = parser.add_argument_group("phone scene start (ADR-0011)")
    phone_sizes.add_argument(
        "--window", type=_size, metavar="WxH", help="innerWidth x innerHeight before emulate"
    )
    phone_sizes.add_argument(
        "--page", type=_size, metavar="WxH", help="innerWidth x innerHeight after emulate"
    )
    phone_sizes.add_argument(
        "--dpr", type=float, metavar="N", help="devicePixelRatio read before emulate"
    )
    args = parser.parse_args(argv)
    phone = {k: v for k in ("window", "page", "dpr") if (v := getattr(args, k)) is not None}
    if phone and args.event != "start":
        parser.error("--window, --page and --dpr go with start only")

    if args.event == "rec_start":
        if args.scene:
            parser.error("rec_start takes no scene id")
        record = {"event": "rec_start", "t_ms": _now_ms()}
        if args.sync_ms is not None:
            record["sync_ms"] = args.sync_ms
    else:
        if not args.scene:
            parser.error(f"'{args.event}' needs a scene id")
        if args.sync_ms is not None:
            parser.error("--sync-ms goes with rec_start only")
        record = {"scene": args.scene, "event": args.event, "t_ms": _now_ms(), **phone}

    try:
        args.timeline.parent.mkdir(parents=True, exist_ok=True)
        with args.timeline.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError as exc:
        print(f"mark: error: cannot write {args.timeline}: {exc.strerror}", file=sys.stderr)
        return 1
    print(json.dumps(record))
    return 0


def _size(text):
    """Lets the recipe pass a size the way the page reports it, as one WxH token."""
    try:
        width, height = (int(n) for n in text.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected WxH, such as 390x844, got {text!r}") from None
    return [width, height]


def _now_ms():
    return int(time.time() * 1000)


if __name__ == "__main__":
    sys.exit(main())
