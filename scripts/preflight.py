#!/usr/bin/env python3
"""Check local demo dependencies and optional measured narration duration."""

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

if __name__ == "__main__" and sys.version_info < (3, 11):
    skill_dir = Path(__file__).resolve().parents[1]
    command = f"uv run --project {skill_dir} python {skill_dir / 'scripts' / 'preflight.py'}"
    message = f"Python 3.11+ is required; rerun with the same arguments as: {command}"
    if "--json" in sys.argv:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "scope": "local",
                    "ready": False,
                    "selected_backend": None,
                    "checks": [
                        {
                            "id": "python",
                            "status": "fail",
                            "message": message,
                            "remedy": f"Run: {command}",
                        }
                    ],
                }
            )
        )
    else:
        print(message)
    raise SystemExit(1)

from compose import ComposeError, burn_method
from demo_spec import SpecError, load_spec, positive_seconds, read_json_object
from tts import BACKENDS, TtsError, choose_backend
from voice_config import resolve_voice_config


@dataclass(frozen=True)
class CheckResult:
    id: str
    status: str
    message: str
    remedy: str = ""


def probe(argv):
    """Bounded local probe; never echo subprocess output on failure."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ComposeError(f"{argv[0]} could not run ({type(exc).__name__})") from exc
    if result.returncode:
        raise ComposeError(f"{argv[0]} exited with status {result.returncode}")
    return result.stdout


def _executable(name, args):
    remedy = f"Install or repair {name} and make it available on PATH."
    if not shutil.which(name):
        return CheckResult(name, "fail", f"{name} not found on PATH", remedy)
    try:
        probe([name, *args])
    except ComposeError as exc:
        return CheckResult(name, "fail", str(exc), remedy)
    return CheckResult(name, "pass", f"{name} is available")


def _voice(spec, selected, spec_dir):
    """So a backend's needs are declared once, in tts.BACKENDS, not re-coded per backend."""
    backend, checks = BACKENDS[selected], []
    if backend.api_key:
        key = backend.api_key
        present = bool(os.environ.get(key, "").strip())
        checks.append(
            CheckResult(
                "voice",
                "pass" if present else "fail",
                f"{key} is configured; remote validity and credits are not checked"
                if present
                else f"{key} is missing or blank",
                "" if present else f"Set {key} or choose an available local backend.",
            )
        )
    tool = _executable(*backend.tool) if backend.tool else None
    if tool:
        checks.append(tool)
    if backend.check_voice and (tool is None or tool.status == "pass"):
        try:
            config = resolve_voice_config(spec.get("voice"), selected, spec_dir)
            backend.check_voice(config, probe)
            checks.append(CheckResult("voice", "pass", f"The {selected} voice settings are valid"))
        except (ValueError, OSError, ComposeError, TtsError) as exc:
            checks.append(
                CheckResult(
                    "voice",
                    "fail",
                    str(exc),
                    "Fix voice in demo.json (references/demo-spec.md), then recheck.",
                )
            )
    if backend.note:
        checks.append(CheckResult(selected, "warning", backend.note))
    return checks


# Spoken narration runs about 0.3-0.5 s per word; well past this bound the synthesis ran away.
MAX_SECONDS_PER_WORD = 1.0
PACE_SLACK_S = 3.0


def _duration(spec, path):
    try:
        values = read_json_object(path, "narration durations", SpecError)
        seconds = []
        for scene in spec["scenes"]:
            value = values.get(scene["id"])
            if not positive_seconds(value):
                raise SpecError(f"scene '{scene['id']}' needs a finite positive narration duration")
            seconds.append(value)
        total = sum(seconds)
        if not math.isfinite(total):
            raise SpecError("total narration duration must be finite")
    except (SpecError, OverflowError, UnicodeError) as exc:
        return [
            CheckResult(
                "duration",
                "fail",
                str(exc),
                "Regenerate audio/durations.json with tts.py for this spec.",
            )
        ]
    return [_pace(spec["scenes"], seconds), _target(spec.get("target_duration_s"), total)]


def _pace(scenes, seconds):
    """Fail scenes whose audio is far longer than their words can take to say."""
    outliers = []
    for scene, value in zip(scenes, seconds):
        words = len(scene["narration"].split())
        if value > words * MAX_SECONDS_PER_WORD + PACE_SLACK_S:
            outliers.append(f"{scene['id']} ({value:g}s for {words} words)")
    if outliers:
        return CheckResult(
            "pace",
            "fail",
            "Narration audio far longer than its words: " + ", ".join(outliers),
            "Rerun tts.py to resynthesize, then recheck; keep the wording.",
        )
    return CheckResult("pace", "pass", "Every scene's audio length fits its word count")


def _target(target, total):
    message = f"Measured narration duration: {total:g}s"
    if target is None:
        return CheckResult("duration", "pass", message + " (no target set)")
    message += f"; target: {target:g}s. Final video may be longer because scenes can be padded."
    if total > target * 1.1:
        return CheckResult(
            "duration",
            "warning",
            message,
            "Propose a shorter script; approve changed wording and regenerate audio before recording.",
        )
    return CheckResult("duration", "pass", message)


def run_checks(spec_path, backend=None, burn_subs=None, durations=None):
    """Return a JSON-serializable report. Ready refers only to local checks."""
    checks = []
    selected = None
    try:
        spec = load_spec(spec_path)
        selected = choose_backend(backend, spec, os.environ)
        checks.append(CheckResult("spec", "pass", "Demo spec and backend selection are valid"))
    except (SpecError, TypeError, AttributeError, UnicodeError) as exc:
        # Older spec fields may fail with a structural type error; keep CLI errors actionable.
        message = (
            str(exc)
            if isinstance(exc, SpecError)
            else "Demo spec contains an invalid field type or encoding"
        )
        checks.append(
            CheckResult("spec", "fail", message, "Correct demo.json using references/demo-spec.md.")
        )
        return _report(checks, selected)

    checks.extend(_executable(name, ["-version"]) for name in ("ffmpeg", "ffprobe"))
    try:
        with tempfile.TemporaryFile(dir=Path(spec_path).resolve().parent) as handle:
            handle.write(b"preflight\n")
            handle.flush()
        checks.append(CheckResult("output", "pass", "Demo directory is writable"))
    except OSError:
        checks.append(
            CheckResult(
                "output",
                "fail",
                "Cannot write beside the demo spec",
                "Choose a writable demo directory or fix its permissions.",
            )
        )

    checks.extend(_voice(spec, selected, Path(spec_path).resolve().parent))

    if burn_subs is False:
        checks.append(
            CheckResult("subtitles", "pass", "Burned subtitles disabled; SRT remains available")
        )
    else:
        method = burn_method(probe=probe)
        if method is not None:
            checks.append(
                CheckResult("subtitles", "pass", f"Subtitle renderer available: {method.value}")
            )
        else:
            checks.append(
                CheckResult(
                    "subtitles",
                    "fail" if burn_subs is True else "warning",
                    "No subtitle renderer; default composition will still write SRT",
                    "Install ffmpeg with libass or rsvg-convert, or use --no-burn-subs.",
                )
            )
    if durations is not None:
        checks.extend(_duration(spec, durations))
    checks.append(
        CheckResult(
            "browser",
            "warning",
            "Browser access, login, viewport and capture are not checked here",
            "Complete the Chrome MCP workflow in references/readiness.md before recording.",
        )
    )
    return _report(checks, selected)


def _report(checks, selected):
    return {
        "schema_version": 1,
        "scope": "local",
        "ready": not any(item.status == "fail" for item in checks),
        "selected_backend": selected,
        "checks": [asdict(item) for item in checks],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--json", action="store_true", help="print a machine-readable local report")
    parser.add_argument("--backend", choices=BACKENDS)
    parser.add_argument("--burn-subs", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument(
        "--durations",
        type=Path,
        help="check measured TTS durations per scene and against the optional target",
    )
    args = parser.parse_args(argv)
    report = run_checks(args.spec, args.backend, args.burn_subs, args.durations)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"Local preflight: {'ready' if report['ready'] else 'blocked'} (backend: {report['selected_backend'] or 'unresolved'})"
        )
        for item in report["checks"]:
            print(f"{item['status'].upper()} {item['id']}: {item['message']}")
            if item["remedy"]:
                print(f"  Fix: {item['remedy']}")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
