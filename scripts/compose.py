"""Compose a fitted, narrated demo video from a raw screencast (stdlib + ffmpeg).

Usage:
    uv run python scripts/compose.py demos/<slug>/demo.json [--raw RAW] [--timeline TIMELINE]
        [--audio-dir DIR] [--durations DURATIONS] [--out FINAL] [--no-burn-subs]

Defaults are relative to the demo spec's folder: raw.mp4, timeline.jsonl,
audio/ (with <scene-id>.wav), audio/durations.json and <slug>.mp4, where <slug>
is the folder's name.

Next to FINAL it also writes FINAL's name with .srt (one cue per narration
sentence), FINAL's name with .editmap.json (see ``edit_map``) and transcript.md
(scene titles + narration). The subtitles are also
drawn into the video, with libass when ffmpeg has the `subtitles` filter,
otherwise as cue images rendered by rsvg-convert and overlaid; --no-burn-subs
leaves them out, and --burn-subs fails instead of warning when neither is there.
A spec with an ``avatar`` also gets the presenter bubble drawn in its corner (see
avatar.py), and subtitles at the same edge keep clear of it.

Each scene is cut from the raw recording into segments at its beat markers,
their frozen frames are dropped with mpdecimate, each segment is held back until
the narration reaches its action's `at` phrase, and the scene is fitted to its
narration (see edit_plan). The scenes are joined in spec order and muxed with
the narration as 30 fps H.264 + AAC.
Every ffmpeg command is built as an argument list by a ``*_cmd`` function and
run separately by ``run``; ``RawVideo`` is how the edit plan measures the raw video.
"""

import argparse
import json
import math
import shutil
import sys
import tempfile
import textwrap
from enum import StrEnum
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import proc
from avatar import Avatar, burn_avatar, geometry
from demo_spec import SpecError, load_spec, read_json_object
from edit_plan import FPS, PlanError, build_plan, load_timeline
from tts import SAMPLE_RATE
from voice_config import saved_photo

X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]


class ComposeError(RuntimeError):
    """Raised when an input is missing or an ffmpeg step fails."""


class BurnMethod(StrEnum):
    """How --burn-subs draws the subtitles (see docs/adr/0001-burn-subs-rsvg-fallback.md)."""

    SUBTITLES = "subtitles"  # ffmpeg's libass `subtitles` filter
    OVERLAY = "overlay"  # cue images rendered by rsvg-convert, overlaid by ffmpeg


# --- command builders (pure: arguments in, argument list out) ---------------------------

def decimate_cmd(raw, trim_start, trim_end, out):
    """Cut [trim_start, trim_end) from the raw video at 30 fps and drop frozen frames."""
    chain = (f"trim=start={trim_start:.3f}:end={trim_end:.3f},setpts=PTS-STARTPTS,"
             f"fps={FPS},mpdecimate,setpts=N/FRAME_RATE/TB")
    return ["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-vf", chain, "-an",
            "-r", str(FPS), *X264, str(out)]


def flash_stats_cmd(raw):
    """Per-frame luma/chroma stats of a tiny copy of every raw frame, printed with pts_time."""
    return ["ffmpeg", "-v", "error", "-i", str(raw), "-fps_mode", "passthrough",
            "-vf", "scale=64:36,signalstats,metadata=print:file=-", "-an", "-f", "null", "-"]


def count_frames_cmd(path):
    return ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
            "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)]


def sync_cmd(parts, out):
    """Join a scene's decimated segments, each led by ``lead_frames`` copies of its first frame.

    ``parts`` is [(path, lead_frames)], in order.
    """
    args = ["ffmpeg", "-v", "error", "-y"]
    for path, _ in parts:
        args += ["-i", str(path)]
    chain = [f"[{index}:v]tpad=start_mode=clone:start={lead}[s{index}]"
             for index, (_, lead) in enumerate(parts)]
    joined = "".join(f"[s{index}]" for index in range(len(parts)))
    chain.append(f"{joined}concat=n={len(parts)}:v=1:a=0[out]")
    return args + ["-filter_complex", ";".join(chain), "-map", "[out]", "-an",
                   "-r", str(FPS), *X264, str(out)]


def fit_cmd(src, scene_fit, frames, viewport, out):
    """Apply a fit to a decimated segment, scale to the viewport, emit exactly ``frames``."""
    width, height = _even(viewport["width"]), _even(viewport["height"])
    filters = []
    if scene_fit.speed != 1.0:
        filters.append(f"setpts=PTS/{scene_fit.speed:.6f}")
    filters += [
        f"fps={FPS}",
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "setsar=1",
        # Always clone the last frame a little past the target, then cut at
        # exactly ``frames``: covers the hold and any rounding short-fall.
        f"tpad=stop_mode=clone:stop_duration={scene_fit.hold_s + 1:.3f}",
    ]
    return ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", ",".join(filters), "-an",
            "-frames:v", str(frames), "-r", str(FPS), *X264, str(out)]


def concat_video_cmd(list_file, out):
    return ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c", "copy", str(out)]


def concat_audio_cmd(wavs_and_lengths, out):
    """Pad (apad) or trim each narration WAV to its scene length and join them."""
    args = ["ffmpeg", "-v", "error", "-y"]
    for wav, _ in wavs_and_lengths:
        args += ["-i", str(wav)]
    parts = []
    for index, (_, seconds) in enumerate(wavs_and_lengths):
        parts.append(f"[{index}:a]aresample={SAMPLE_RATE},aformat=channel_layouts=mono,"
                     f"apad=whole_dur={seconds:.6f},atrim=end={seconds:.6f},"
                     f"asetpts=PTS-STARTPTS[a{index}]")
    joined = "".join(f"[a{i}]" for i in range(len(wavs_and_lengths)))
    parts.append(f"{joined}concat=n={len(wavs_and_lengths)}:v=0:a=1[out]")
    return args + ["-filter_complex", ";".join(parts), "-map", "[out]",
                   "-c:a", "pcm_s16le", str(out)]


# ffmpeg renders an .srt on a 384 x 288 libass canvas; side margins are in its units.
LIBASS_WIDTH, LIBASS_MARGIN = 384, 10


def subtitles_burn_cmd(video, srt_name, out, side_margin=LIBASS_MARGIN):
    """Burn an .srt with libass (the ``subtitles`` filter); run with cwd = the srt's folder.

    ``side_margin`` is the left and right margin in libass units, the same on both sides
    so the lines stay centred.
    """
    style = ("FontName=Helvetica,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
             f"BorderStyle=1,Outline=1.5,Shadow=0,MarginV=18,"
             f"MarginL={side_margin},MarginR={side_margin}")
    return ["ffmpeg", "-v", "error", "-y", "-i", str(video),
            "-vf", f"subtitles=filename={srt_name}:force_style='{style}'",
            "-an", "-r", str(FPS), *X264, str(out)]


def overlay_burn_cmd(video, overlays, out):
    """Burn pre-rendered cue images: ``overlays`` is [(png, start_s, end_s, y)], one per cue."""
    args = ["ffmpeg", "-v", "error", "-y", "-i", str(video)]
    for png, *_ in overlays:
        args += ["-i", str(png)]
    chain, last = [], "0:v"
    for index, (_, start, end, y) in enumerate(overlays, start=1):
        chain.append(f"[{last}][{index}:v]overlay=x=0:y={y}:"
                     f"enable='between(t,{start:.3f},{end:.3f})'[v{index}]")
        last = f"v{index}"
    return args + ["-filter_complex", ";".join(chain), "-map", f"[{last}]",
                   "-an", "-r", str(FPS), *X264, str(out)]


def svg_to_png_cmd(svg, png):
    return ["rsvg-convert", "-o", str(png), str(svg)]


def mux_cmd(video, audio, out):
    return ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(audio),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(out)]


# --- sync flash (pure) ----------------------------------------------------------------

# __demo.sync() fills the viewport with #ff00ff: Y 106, U 202, V 222. Every pixel of a
# flash frame is close to that; no ordinary page frame has chroma this high throughout.
FLASH_MIN_U, FLASH_MIN_V = 170, 190


def first_flash_s(stats):
    """The pts_time of the first all-magenta frame in ``flash_stats_cmd`` output, or None."""
    pts, values = None, {}
    for line in [*stats.splitlines(), "frame:end"]:
        if line.startswith("frame:"):
            if (pts is not None and values.get("UMIN", 0) >= FLASH_MIN_U
                    and values.get("VMIN", 0) >= FLASH_MIN_V):
                return pts
            fields = dict(part.split(":", 1) for part in line.split() if ":" in part)
            pts = float(fields["pts_time"]) if "pts_time" in fields else None
            values = {}
        elif line.startswith("lavfi.signalstats."):
            key, _, value = line[len("lavfi.signalstats."):].partition("=")
            values[key] = float(value)
    return None


# --- text outputs (pure) -------------------------------------------------------------

def srt_text(cues):
    """Render subtitle cues as a SubRip (.srt) document."""
    blocks = [f"{n}\n{_srt_time(cue.start_s)} --> {_srt_time(cue.end_s)}\n{cue.text}\n"
              for n, cue in enumerate(cues, start=1)]
    return "\n".join(blocks)


def transcript_text(title, plan):
    """Render the narration as Markdown: the demo title, then each scene's title and narration."""
    lines = [f"# {title}", ""]
    for scene in plan.scenes:
        lines += [f"## {scene.title}", "", scene.narration.strip(), ""]
    return "\n".join(lines)


def cue_svg(text, width, height, reserve=0):
    """Render one cue as a transparent ``width``-wide SVG strip of centred, outlined text.

    Lines wrap to keep ``reserve`` pixels clear at both sides. Returns (svg, strip_height).
    Used to burn subtitles when ffmpeg lacks libass.
    """
    font = max(14, round(height * 0.045))
    line_h = round(font * 1.3)
    per_line = max(10, int(min(width * 0.85, width - 2 * reserve) / (font * 0.55)))
    lines = textwrap.wrap(text, per_line) or [""]
    strip_h = line_h * len(lines) + font
    tspans = "".join(
        f'<tspan x="{width / 2:.0f}" y="{font // 2 + line_h * (i + 1) - (line_h - font)}">'
        f"{xml_escape(line)}</tspan>"
        for i, line in enumerate(lines)
    )
    # The outline is its own text layer under the fill: rsvg-convert ignores paint-order,
    # and a stroke drawn over the fill eats into the letters.
    text_attrs = (f'font-family="Helvetica, Arial, sans-serif" font-size="{font}" '
                  f'font-weight="600" text-anchor="middle"')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{strip_h}">'
           f'<text {text_attrs} fill="black" stroke="black" stroke-width="{max(2, font // 6)}" '
           f'stroke-linejoin="round">{tspans}</text>'
           f'<text {text_attrs} fill="white">{tspans}</text></svg>')
    return svg, strip_h


def edit_map(spec, plan):
    """The edit map: the total length and, per scene, its id, title, start and end in the
    final video, plus every action whose start compose knows (the scene's first action and
    each ``at`` action) with its index in the scene's actions, its type and its start.

    Times are in seconds, rounded to the millisecond like compose's printed lines.
    """
    at_starts = {(action.scene_id, action.index): action.start_s for action in plan.actions}
    scenes = []
    for spec_scene, scene in zip(spec["scenes"], plan.scenes):
        actions, at_count = [], 0
        for index, action in enumerate(spec_scene.get("actions", [])):
            entry = {"index": index, "type": action["type"]}
            if "at" in action:
                at_count += 1
                entry.update(start_s=round(at_starts[(scene.id, at_count)], 3), at=action["at"])
            elif index == 0:
                entry["start_s"] = round(scene.offset_s, 3)
            else:
                continue  # its start is not known
            actions.append(entry)
        scenes.append({"id": scene.id, "title": scene.title, "start_s": round(scene.offset_s, 3),
                       "end_s": round(scene.end_s, 3), "actions": actions})
    return {"total_s": round(plan.total_s, 3), "scenes": scenes}


def action_line(action):
    """One ``at`` action's line of compose output: where it starts, and how late if it is."""
    late = f"\tlate {action.late_s:.3f}s" if action.late_s else ""
    return f'at\t{action.scene_id}\t{action.index}\t{action.start_s:.3f}s\t"{action.phrase}"{late}'


def _srt_time(seconds):
    ms = max(0, round(seconds * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


# --- runner ---------------------------------------------------------------------------

def run(cmd, cwd=None):
    """Run one command; raise ComposeError with ffmpeg's message if it fails."""
    return proc.run(cmd, cwd=cwd, error=ComposeError)


def burn_method(*, probe=None):
    """How subtitles can be burned in here: a BurnMethod, or None if neither tool is present."""
    try:
        filters = (probe or run)(["ffmpeg", "-hide_banner", "-filters"])
    except ComposeError:
        filters = ""
    if any(line.split()[1:2] == ["subtitles"] for line in filters.splitlines()):
        return BurnMethod.SUBTITLES
    if shutil.which("rsvg-convert"):
        return BurnMethod.OVERLAY
    return None


def burn_subtitles(video, cues, viewport, method, out, work_dir, reserve=0):
    """Burn ``cues`` into ``video`` with libass, or as pre-rendered images overlaid by ffmpeg.

    The lines keep ``reserve`` pixels clear at both sides (where a bubble sits).
    """
    width, height = _frame(viewport)
    if method is BurnMethod.SUBTITLES:
        (work_dir / "subs.srt").write_text(srt_text(cues), encoding="utf-8")
        margin = max(LIBASS_MARGIN, math.ceil(reserve * LIBASS_WIDTH / width))
        run(subtitles_burn_cmd(Path(video).resolve(), "subs.srt", Path(out).resolve(), margin),
            cwd=work_dir)
        return
    overlays = []
    for index, cue in enumerate(cues):
        svg, strip_h = cue_svg(cue.text, width, height, reserve)
        svg_path, png = work_dir / f"cue-{index:03d}.svg", work_dir / f"cue-{index:03d}.png"
        svg_path.write_text(svg, encoding="utf-8")
        run(svg_to_png_cmd(svg_path, png))
        y = max(0, height - strip_h - round(height * 0.04))
        overlays.append((png, cue.start_s, cue.end_s, y))
    if not overlays:
        shutil.copyfile(video, out)
        return
    run(overlay_burn_cmd(video, overlays, out))


class RawVideo:
    """The raw screencast as ``build_plan`` measures it, with ffmpeg.

    Each window it is asked about is decimated into ``work_dir`` once and kept there
    (``decimated``) for the rest of the pipeline. It only checks that the file
    exists when first asked, so a bad timeline is reported before a missing video.
    """

    def __init__(self, path, work_dir):
        self.path, self._work_dir = Path(path), Path(work_dir)
        self._decimated = {}

    def flash_s(self):
        self._check()
        return first_flash_s(run(flash_stats_cmd(self.path)))

    def frames(self, trim_start, trim_end):
        self._check()
        out = self._work_dir / f"decimated-{len(self._decimated):03d}.mp4"
        run(decimate_cmd(self.path, trim_start, trim_end, out))
        self._decimated[(trim_start, trim_end)] = out
        return int(run(count_frames_cmd(out)).strip() or 0)

    def decimated(self, trim_start, trim_end):
        """The decimated file of a window that ``frames`` measured."""
        return self._decimated[(trim_start, trim_end)]

    def _check(self):
        if not self.path.is_file():
            raise ComposeError(f"raw recording not found: {self.path}")


def compose(spec, timeline, durations, raw, audio_dir, out, work_dir, method=None, avatar=None):
    """Plan the edit against the raw recording and run the ffmpeg pipeline; return the plan.

    With a BurnMethod ``method`` the plan's subtitle cues are drawn into the video; with
    an Avatar ``avatar`` its bubble is drawn in its corner, glowing with the narration.
    """
    audio_dir, work_dir = Path(audio_dir), Path(work_dir)
    raw_video = RawVideo(raw, work_dir)
    plan = build_plan(spec, timeline, durations, raw_video)
    wavs = {scene.id: audio_dir / f"{scene.id}.wav" for scene in plan.scenes}
    for scene_id, wav in wavs.items():
        if not wav.is_file():
            raise ComposeError(f"narration audio for scene '{scene_id}' not found: {wav}")

    list_lines, audio_parts = [], []
    for index, scene in enumerate(plan.scenes):
        parts = [(raw_video.decimated(segment.trim_start, segment.trim_end), segment.lead_frames)
                 for segment in scene.segments if segment.frames]
        if len(parts) == 1 and parts[0][1] == 0:
            synced = parts[0][0]
        else:
            synced = work_dir / f"{index:03d}-synced.mp4"
            run(sync_cmd(parts, synced))
        fitted = work_dir / f"{index:03d}-fitted.mp4"
        run(fit_cmd(synced, scene.fit, scene.frames, spec["viewport"], fitted))
        list_lines.append(f"file '{fitted.as_posix()}'")
        audio_parts.append((wavs[scene.id], scene.scene_s))
    list_file = work_dir / "scenes.txt"
    list_file.write_text("\n".join(list_lines) + "\n")

    video, audio = work_dir / "video.mp4", work_dir / "audio.wav"
    run(concat_video_cmd(list_file, video))
    run(concat_audio_cmd(audio_parts, audio))
    if method is not None:
        reserve = geometry(avatar, *_frame(spec["viewport"])).reserve if avatar else 0
        burned = work_dir / "burned.mp4"
        burn_subtitles(video, plan.cues, spec["viewport"], method, burned, work_dir, reserve)
        video = burned
    if avatar is not None:
        bubbled = work_dir / "bubbled.mp4"
        burn_avatar(video, audio, avatar, spec["viewport"], FPS, bubbled, work_dir, run)
        video = bubbled
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    run(mux_cmd(video, audio, out))
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(prog="compose", description=__doc__.splitlines()[0])
    parser.add_argument("spec", type=Path, help="demos/<slug>/demo.json")
    parser.add_argument("--raw", type=Path, help="raw screencast (default: <demo>/raw.mp4)")
    parser.add_argument("--timeline", type=Path, help="default: <demo>/timeline.jsonl")
    parser.add_argument("--audio-dir", type=Path, help="default: <demo>/audio")
    parser.add_argument("--durations", type=Path, help="default: <audio-dir>/durations.json")
    parser.add_argument("--out", type=Path, help="default: <demo>/<slug>.mp4, named after the demo folder")
    parser.add_argument("--burn-subs", action=argparse.BooleanOptionalAction, default=None,
                        help="draw the subtitles into the video (default: when a burn method "
                             "is available; the .srt is always written)")
    args = parser.parse_args(argv)

    demo_dir = args.spec.parent
    audio_dir = args.audio_dir or demo_dir / "audio"
    out = args.out or demo_dir / f"{demo_dir.resolve().name}.mp4"
    srt_path, transcript_path = out.with_suffix(".srt"), out.parent / "transcript.md"
    edit_map_path = out.with_suffix(".editmap.json")
    try:
        spec = load_spec(args.spec)
        timeline = load_timeline(args.timeline or demo_dir / "timeline.jsonl")
        durations = read_json_object(args.durations or audio_dir / "durations.json",
                                     "durations", ComposeError, hint="; run tts.py first",
                                     shape="a JSON object of scene id -> seconds")
        raw = args.raw or demo_dir / "raw.mp4"
        avatar = spec_avatar(spec, demo_dir)
        method = burn_method() if args.burn_subs is not False else None
        if method is None and args.burn_subs:
            raise ComposeError("--burn-subs needs an ffmpeg with libass (the `subtitles` filter) "
                               "or rsvg-convert (`brew install librsvg`); drop the flag to get "
                               "the subtitles only as a separate .srt")
        if method is None and args.burn_subs is None:
            print("compose: warning: neither libass nor rsvg-convert is available, so the "
                  "subtitles are only in the .srt (`brew install librsvg` to burn them in)",
                  file=sys.stderr)
        with tempfile.TemporaryDirectory(prefix="compose-") as work_dir:
            plan = compose(spec, timeline, durations, raw, audio_dir, out, work_dir,
                           method=method, avatar=avatar)
        srt_path.write_text(srt_text(plan.cues), encoding="utf-8")
        edit_map_path.write_text(json.dumps(edit_map(spec, plan), indent=2) + "\n",
                                 encoding="utf-8")
        transcript_path.write_text(transcript_text(spec.get("title") or demo_dir.name, plan),
                                   encoding="utf-8")
    except (SpecError, PlanError, ComposeError) as exc:
        print(f"compose: error: {exc}", file=sys.stderr)
        return 1

    actions = {}
    for action in plan.actions:
        actions.setdefault(action.scene_id, []).append(action)
    for scene in plan.scenes:
        print(f"{scene.id}\t{scene.fit.strategy}\t{scene.fit.speed:.2f}x\t"
              f"video {scene.video_s:.3f}s\taudio {scene.audio_s:.3f}s\tscene {scene.scene_s:.3f}s")
        for action in actions.get(scene.id, ()):
            print(action_line(action))
    if plan.flash_s is not None:
        print(f"sync\tflash at {plan.flash_s:.3f}s in the raw video")
    print(f"total\t{plan.total_s:.3f}s\t{out}")
    print(f"subtitles\t{len(plan.cues)} cues\t{srt_path}\t"
          f"{f'burned in ({method})' if method else 'not burned in'}")
    print(f"transcript\t{transcript_path}")
    if avatar:
        print(f"avatar\t{avatar.corner}\t{avatar.image}")
    return 0


def spec_avatar(spec, demo_dir):
    """The spec's ``avatar`` as an Avatar, its image relative to the demo folder or the saved
    own_photo; None without one."""
    if "avatar" not in spec:
        return None
    fields = spec["avatar"]
    if "profile" in fields:
        try:
            return Avatar(saved_photo(), fields["corner"], fields["size"])
        except ValueError as exc:
            raise ComposeError(str(exc)) from exc
    image = Path(demo_dir) / Path(fields["image"]).expanduser()
    if not image.is_file():
        raise ComposeError(f"avatar image not found: {image}")
    return Avatar(image, fields["corner"], fields["size"])


def _even(value):
    return int(value) - int(value) % 2


def _frame(viewport):
    return _even(viewport["width"]), _even(viewport["height"])


if __name__ == "__main__":
    sys.exit(main())
