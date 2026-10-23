import array
import math
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from avatar import Avatar, bubble_cmd, geometry, halo_cmd, level_commands, read_wav, voice_levels

PHOTO = Path(__file__).resolve().parent.parent / "assets" / "avatars" / "huijo.png"

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def tone(seconds, rate=48000, amplitude=8000):
    return [
        round(amplitude * math.sin(2 * math.pi * 220 * n / rate))
        for n in range(round(seconds * rate))
    ]


@pytest.mark.parametrize(
    "corner, left, top",
    [
        ("bottom-left", True, False),
        ("bottom-right", False, False),
        ("top-left", True, True),
        ("top-right", False, True),
    ],
)
def test_the_bubble_sits_in_its_corner_inside_the_frame(corner, left, top):
    geo = geometry(Avatar(PHOTO, corner, 0.2), 1920, 1080)

    centre_x, centre_y = geo.x + geo.canvas / 2, geo.y + geo.canvas / 2
    outer = geo.face / 2 + geo.rim
    assert geo.face == 216
    assert (centre_x < 960) == left and (centre_y < 540) == top
    assert outer <= centre_x <= 1920 - outer and outer <= centre_y <= 1080 - outer
    # 3% of the height between the rim and both edges it is near
    gap_x = centre_x - outer if left else 1920 - centre_x - outer
    gap_y = centre_y - outer if top else 1080 - centre_y - outer
    assert gap_x == gap_y == round(1080 * 0.03)


def test_only_a_bottom_bubble_reserves_room_beside_the_subtitles():
    bottom = geometry(Avatar(PHOTO, "bottom-right", 0.2), 1920, 1080)
    top = geometry(Avatar(PHOTO, "top-left", 0.2), 1920, 1080)

    assert bottom.reserve == 1920 - bottom.x  # the halo's left edge
    assert top.reserve == 0


def test_silence_is_dark_and_speech_lights_the_halo_then_fades():
    samples = [0] * 4800 + tone(0.5) + [0] * 24000  # 0.1 s silence, 0.5 s voice, 0.5 s silence

    levels = voice_levels(samples, 48000, 30)

    assert len(levels) == 33
    assert max(levels[:3]) == 0
    assert levels[17] > 0.95
    assert levels[18:] == sorted(levels[18:], reverse=True)  # fades out, never flickers back
    assert levels[-1] < 0.05


def test_a_quiet_voice_pulses_like_a_loud_one():
    loud = voice_levels(tone(0.5, amplitude=16000), 48000, 30)
    quiet = voice_levels(tone(0.5, amplitude=500), 48000, 30)

    assert quiet == pytest.approx(loud, rel=1e-4)


def test_no_audio_gives_no_levels():
    assert voice_levels([], 48000, 30) == []
    assert level_commands([], 30) == "\n"


def test_level_commands_send_rounded_opacity_only_when_it_changes():
    text = level_commands([0.0, 0.01, 0.5, 0.52, 0.98], 30)

    assert text == (
        "0.000 colorchannelmixer@halo aa 0.00;\n"
        "0.067 colorchannelmixer@halo aa 0.50;\n"
        "0.133 colorchannelmixer@halo aa 1.00;\n"
    )


def test_read_wav_returns_mono_16_bit_samples(tmp_path):
    path = tmp_path / "voice.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(array.array("h", [0, 1000, -1000]).tobytes())

    samples, rate = read_wav(path)

    assert list(samples) == [0, 1000, -1000] and rate == 48000


def test_read_wav_rejects_stereo(tmp_path):
    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(b"\0" * 8)

    with pytest.raises(ValueError, match="mono 16-bit"):
        read_wav(path)


def alpha_at(png, size, x, y):
    alpha = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(png),
            "-vf",
            "format=rgba,extractplanes=a",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    assert len(alpha) == size * size
    return alpha[y * size + x]


@needs_ffmpeg
def test_bubble_is_an_opaque_disc_on_a_transparent_square(tmp_path):
    geo = geometry(Avatar(PHOTO), 640, 360)
    out = tmp_path / "bubble.png"

    subprocess.run(bubble_cmd(PHOTO, geo, out), check=True)

    centre = geo.canvas // 2
    assert alpha_at(out, geo.canvas, centre, centre) == 255
    assert alpha_at(out, geo.canvas, centre, geo.halo // 2) == 0  # above the rim: clear
    assert alpha_at(out, geo.canvas, 0, 0) == 0


@needs_ffmpeg
def test_halo_glows_at_the_rim_and_fades_to_nothing(tmp_path):
    geo = geometry(Avatar(PHOTO), 640, 360)
    out = tmp_path / "halo.png"

    subprocess.run(halo_cmd(geo, out), check=True)

    centre = geo.canvas // 2
    assert alpha_at(out, geo.canvas, centre, geo.halo) > 200  # just outside the rim
    assert alpha_at(out, geo.canvas, centre, 0) < 8
    assert alpha_at(out, geo.canvas, 0, 0) == 0
