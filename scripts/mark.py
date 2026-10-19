"""Stamp a scene marker into a demo timeline (stdlib only).

Usage:
    python3 scripts/mark.py TIMELINE rec_start
    python3 scripts/mark.py TIMELINE start SCENE_ID
    python3 scripts/mark.py TIMELINE end SCENE_ID

Each call appends one JSON line ``{"scene": ..., "event": ..., "t_ms": ...}``
with the current wall-clock time in milliseconds. ``rec_start`` marks the moment
the screencast started. A re-recording appends a new ``rec_start``; compose uses
only the events after the last one, so earlier takes can stay in the file.
"""

import argparse
import json
import sys
import time
from pathlib import Path

SCENE_EVENTS = ("start", "end")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mark", description=__doc__.splitlines()[0])
    parser.add_argument("timeline", type=Path, help="timeline.jsonl to append to")
    parser.add_argument("event", choices=("rec_start", *SCENE_EVENTS))
    parser.add_argument("scene", nargs="?", help="scene id (required for start/end)")
    args = parser.parse_args(argv)

    if args.event == "rec_start":
        if args.scene:
            parser.error("rec_start takes no scene id")
        record = {"event": "rec_start", "t_ms": _now_ms()}
    else:
        if not args.scene:
            parser.error(f"'{args.event}' needs a scene id")
        record = {"scene": args.scene, "event": args.event, "t_ms": _now_ms()}

    try:
        args.timeline.parent.mkdir(parents=True, exist_ok=True)
        with args.timeline.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError as exc:
        print(f"mark: error: cannot write {args.timeline}: {exc.strerror}", file=sys.stderr)
        return 1
    print(json.dumps(record))
    return 0


def _now_ms():
    return int(time.time() * 1000)


if __name__ == "__main__":
    sys.exit(main())
