"""The presenter bubble: a round photo in a corner that glows while the voice speaks (stdlib + ffmpeg).

Like a Loom camera bubble, but from a still photo: the photo is cropped to a circle
with a white rim and a soft shadow, and a halo around the rim brightens with the
narration's loudness, frame by frame. There is no lip-sync. The halo follows the
final, fitted narration track, so it is in step with the voice by construction.

ffmpeg draws both layers once as PNGs (``bubble_cmd``, ``halo_cmd``), then overlays
them for the whole video (``avatar_burn_cmd``), with the halo's opacity driven by a
``sendcmd`` file of per-frame levels (``voice_levels``, ``level_commands``).
"""

import array
import math
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

CORNERS = ("bottom-left", "bottom-right", "top-left", "top-right")
DEFAULT_CORNER = "bottom-left"
DEFAULT_SIZE = 0.2  # the photo's diameter, as a share of the video's height
MIN_SIZE, MAX_SIZE = 0.05, 0.5
HALO_COLOUR = "0x8b5cf6"
X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]

# Loudness to halo opacity: the track's loud end (its 95th-percentile frame) is fully
# lit and anything RANGE_DB below it is dark, so quiet and loud voices pulse alike.
RANGE_DB = 30.0
ATTACK, RELEASE = 0.6, 0.2  # per-frame smoothing toward a louder / quieter level
STEP = 0.05  # opacity is sent in steps of this size, and only when it changes


@dataclass(frozen=True)
class Avatar:
    """A demo spec's ``avatar``: the photo, its corner, and its diameter as a share of the height."""

    image: Path
    corner: str = DEFAULT_CORNER
    size: float = DEFAULT_SIZE


@dataclass(frozen=True)
class Geometry:
    """Where the bubble sits in a ``width`` x ``height`` video, in pixels.

    Both PNGs are ``canvas`` pixels square, centred on the photo, and overlaid at
    (``x``, ``y``). ``reserve`` is how far the bubble and its halo reach in from their
    side edge, so subtitles at the same edge can keep clear of it (0 for a top corner).
    """

    face: int
    rim: int
    halo: int
    canvas: int
    x: int
    y: int
    reserve: int


def geometry(avatar, width, height):
    """Lay the bubble out in its corner, a margin of 3% of the height from the edges."""
    face = _even(round(avatar.size * height))
    rim = max(2, round(face * 0.025))
    halo = max(4, round(face * 0.12))
    canvas = face + 2 * (rim + halo)
    margin = round(height * 0.03)
    centre_x = margin + face // 2 + rim
    centre_y = centre_x
    if avatar.corner.endswith("right"):
        centre_x = width - centre_x
    if avatar.corner.startswith("bottom"):
        centre_y = height - centre_y
    reserve = margin + face + 2 * rim + halo if avatar.corner.startswith("bottom") else 0
    return Geometry(
        face, rim, halo, canvas, centre_x - canvas // 2, centre_y - canvas // 2, reserve
    )


# --- command builders (pure: arguments in, argument list out) ---------------------------


def bubble_cmd(image, geo, out):
    """The photo as a circle ``geo.face`` across, with a white rim and a soft shadow, as a PNG."""
    centre = geo.canvas / 2
    outer = geo.face / 2 + geo.rim
    drop = max(1, round(geo.face * 0.02))
    # The rim is white and the shadow black; at the rim's edge the two blend to grey.
    rim = f"clip({outer:.2f}-hypot(X+0.5-{centre:.2f},Y+0.5-{centre:.2f})+0.5,0,1)"
    shadow = (
        f"0.35*clip(({outer + geo.halo * 0.6:.2f}"
        f"-hypot(X+0.5-{centre:.2f},Y+0.5-{centre + drop:.2f}))/{geo.halo * 0.6:.2f},0,1)"
    )
    base = (
        f"color=c=black@0:s={geo.canvas}x{geo.canvas},format=rgba,"
        f"geq=r='255*{rim}':g='255*{rim}':b='255*{rim}':a='255*max({rim},{shadow})'[base]"
    )
    face = (
        f"[0:v]crop='min(iw,ih)':'min(iw,ih)',scale={geo.face}:{geo.face}:flags=lanczos,"
        f"format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{_disc('W/2', 'W/2', 'H/2')}'"
        "[face]"
    )
    offset = geo.rim + geo.halo
    return [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(image),
        "-filter_complex",
        f"{base};{face};[base][face]overlay={offset}:{offset}:format=auto[out]",
        "-map",
        "[out]",
        "-frames:v",
        "1",
        "-update",
        "1",
        str(out),
    ]


def halo_cmd(geo, out, colour=HALO_COLOUR):
    """A glow that fades out over ``geo.halo`` pixels beyond the rim, at full opacity, as a PNG."""
    centre = geo.canvas / 2
    outer = geo.face / 2 + geo.rim
    glow = f"pow(clip(1-(hypot(X+0.5-{centre:.2f},Y+0.5-{centre:.2f})-{outer:.2f})/{geo.halo},0,1),1.5)"
    return [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={colour}:s={geo.canvas}x{geo.canvas}",
        "-vf",
        f"format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='255*{glow}'",
        "-frames:v",
        "1",
        "-update",
        "1",
        str(out),
    ]


def avatar_burn_cmd(video, bubble, halo, commands_name, geo, fps, out):
    """Overlay the halo (opacity from the ``sendcmd`` file) and the bubble on ``video``.

    Run with cwd = the commands file's folder: ``commands_name`` is a bare name, so the
    filtergraph needs no path escaping.
    """
    graph = (
        f"[2:v]format=rgba,sendcmd=f={commands_name},colorchannelmixer@halo=aa=0[halo];"
        f"[0:v][halo]overlay=x={geo.x}:y={geo.y}:shortest=1[lit];"
        f"[lit][1:v]overlay=x={geo.x}:y={geo.y}:shortest=1[out]"
    )
    return [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(video),
        "-loop",
        "1",
        "-framerate",
        str(fps),
        "-i",
        str(bubble),
        "-loop",
        "1",
        "-framerate",
        str(fps),
        "-i",
        str(halo),
        "-filter_complex",
        graph,
        "-map",
        "[out]",
        "-an",
        "-r",
        str(fps),
        *X264,
        str(out),
    ]


# --- voice levels (pure) -----------------------------------------------------------------


def voice_levels(samples, rate, fps):
    """The voice's loudness for each video frame, smoothed, from 0 (silent) to 1 (loud).

    ``samples`` are mono 16-bit PCM values at ``rate`` Hz; one level per 1/``fps`` s.
    """
    window = max(1, round(rate / fps))
    frames = math.ceil(len(samples) / window)
    if not frames:
        return []
    decibels = []
    for index in range(frames):
        chunk = samples[index * window : (index + 1) * window]
        rms = math.sqrt(sum(value * value for value in chunk) / len(chunk)) / 32768
        decibels.append(20 * math.log10(max(rms, 1e-6)))
    loud = sorted(decibels)[min(frames - 1, int(frames * 0.95))]
    levels, level = [], 0.0
    for db in decibels:
        target = min(1.0, max(0.0, (db - (loud - RANGE_DB)) / RANGE_DB))
        level += (target - level) * (ATTACK if target > level else RELEASE)
        levels.append(level)
    return levels


def level_commands(levels, fps, target="colorchannelmixer@halo"):
    """An ffmpeg ``sendcmd`` script that sets the halo's opacity to each frame's level."""
    lines, sent = [], None
    for index, level in enumerate(levels):
        value = round(round(level / STEP) * STEP, 2)
        if value != sent:
            lines.append(f"{index / fps:.3f} {target} aa {value:.2f};")
            sent = value
    return "\n".join(lines) + "\n"


def read_wav(path):
    """The samples and rate of a mono 16-bit PCM WAV file."""
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
            raise ValueError(f"{path}: expected mono 16-bit PCM")
        samples = array.array("h", handle.readframes(handle.getnframes()))
        rate = handle.getframerate()
    if sys.byteorder == "big":
        samples.byteswap()
    return samples, rate


# --- pipeline ----------------------------------------------------------------------------


def burn_avatar(video, audio, avatar, viewport, fps, out, work_dir, run):
    """Draw ``avatar``'s bubble on ``video``, its halo following ``audio``; ``run`` runs a command."""
    work_dir = Path(work_dir)
    geo = geometry(avatar, _even(viewport["width"]), _even(viewport["height"]))
    bubble, halo = work_dir / "avatar-bubble.png", work_dir / "avatar-halo.png"
    run(bubble_cmd(avatar.image, geo, bubble))
    run(halo_cmd(geo, halo))
    samples, rate = read_wav(audio)
    (work_dir / "avatar-levels.cmd").write_text(
        level_commands(voice_levels(samples, rate, fps), fps)
    )
    run(
        avatar_burn_cmd(
            Path(video).resolve(),
            bubble.resolve(),
            halo.resolve(),
            "avatar-levels.cmd",
            geo,
            fps,
            Path(out).resolve(),
        ),
        cwd=work_dir,
    )
    return geo


def _disc(radius, centre_x, centre_y):
    """A geq alpha expression: opaque inside the circle, with a one-pixel smooth edge."""
    return f"255*clip({radius}-hypot(X+0.5-{centre_x},Y+0.5-{centre_y})+0.5,0,1)"


def _even(value):
    return int(value) - int(value) % 2
