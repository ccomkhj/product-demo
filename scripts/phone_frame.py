"""The phone frame: a generic rounded device that phone scenes are shown in (stdlib + ffmpeg).

A phone scene's page is cropped out of the take (see ADR-0011), scaled to the frame's
screen, and drawn on the demo's backdrop colour at the video's full size, so cutting
between desktop and phone scenes never changes the video's size. The frame sits above
the subtitle band, so burned-in subtitles never cover the screen. Like the presenter
bubble (ADR-0006), its screen mask and body are drawn once as PNGs by ffmpeg.
"""

from dataclasses import dataclass

BODY_COLOUR = "0x111111"
TOP_MARGIN = 0.04  # of the video's height, above the frame
BEZEL = 0.025  # of the frame's height
CORNER = 0.12  # the screen's corner radius, as a share of its width
# A page this many times wider (for its height) than the phone's own shape was not
# recorded as a phone. A failed emulation leaves Chrome's narrowest window (~500x836,
# 1.3x a 390x844 phone), so the limit sits below that.
MAX_ASPECT_STRETCH = 1.2


@dataclass(frozen=True)
class Geometry:
    """The mask, the body and the scene must all agree on one layout, in video pixels."""

    x: int
    y: int
    width: int
    height: int
    bezel: int
    screen_width: int
    screen_height: int
    radius: int


def geometry(page_width, page_height, width, height, clear_below):
    """As large as fits above ``clear_below`` pixels of subtitles, so they never cover the screen."""
    top = round(height * TOP_MARGIN)
    room_h = height - 2 * top - clear_below
    room_w = width - 2 * top
    bezel = max(2, round(room_h * BEZEL))
    scale = min((room_h - 2 * bezel) / page_height, (room_w - 2 * bezel) / page_width)
    screen_w, screen_h = _even(page_width * scale), _even(page_height * scale)
    outer_w, outer_h = screen_w + 2 * bezel, screen_h + 2 * bezel
    return Geometry(
        (width - outer_w) // 2,
        top,
        outer_w,
        outer_h,
        bezel,
        screen_w,
        screen_h,
        round(screen_w * CORNER),
    )


def too_wide(crop_width, crop_height, phone):
    """A desktop page framed as a phone would be squashed, so compose refuses it instead."""
    return crop_width / crop_height > MAX_ASPECT_STRETCH * phone["width"] / phone["height"]


# --- command builders (pure: arguments in, argument list out) ---------------------------


def mask_cmd(geo, out):
    """A page is square-cornered; the mask rounds it to look like a phone's screen."""
    inside = _rounded(geo.screen_width, geo.screen_height, geo.radius)
    return _png_cmd(
        f"color=c=black:s={geo.screen_width}x{geo.screen_height}",
        f"format=gray,geq=lum='{inside}'",
        out,
    )


def body_cmd(geo, out, colour=BODY_COLOUR):
    """The body is what makes the screen read as a phone rather than a floating page."""
    outside = _rounded(geo.width, geo.height, geo.radius + geo.bezel)
    return _png_cmd(
        f"color=c={colour}:s={geo.width}x{geo.height}",
        f"format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{outside}'",
        out,
    )


def frame_cmd(screen, frame, frames, fps, x264, out):
    """Concat needs every scene at the video's size and exact length, phone or not."""
    geo, (width, height) = frame.geo, frame.viewport
    graph = (
        f"color=c={frame.background.replace('#', '0x')}:s={width}x{height}:r={fps}[bg];"
        f"[0:v][1:v]alphamerge[screen];"
        f"[bg][2:v]overlay=x={geo.x}:y={geo.y}[body];"
        f"[body][screen]overlay=x={geo.x + geo.bezel}:y={geo.y + geo.bezel},setsar=1[out]"
    )
    return [
        "ffmpeg", "-v", "error", "-y",
        "-i", str(screen),
        "-loop", "1", "-framerate", str(fps), "-i", str(frame.mask),
        "-loop", "1", "-framerate", str(fps), "-i", str(frame.body),
        "-filter_complex", graph,
        "-map", "[out]", "-an", "-frames:v", str(frames), "-r", str(fps),
        *x264, str(out),
    ]  # fmt: skip


def _png_cmd(source, chain, out):
    return [
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source,
        "-vf", chain, "-frames:v", "1", "-update", "1", str(out),
    ]  # fmt: skip


def _rounded(width, height, radius):
    """ffmpeg has no rounded-rectangle source, so geq draws one, anti-aliased."""
    dx = f"max(abs(X+0.5-{width / 2})-{width / 2 - radius},0)"
    dy = f"max(abs(Y+0.5-{height / 2})-{height / 2 - radius},0)"
    return f"255*clip({radius}-hypot({dx},{dy})+0.5,0,1)"


def _even(value):
    return int(value) - int(value) % 2
