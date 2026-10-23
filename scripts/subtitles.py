"""SubRip (.srt) subtitles: write cues, read them back (stdlib only)."""

import re
from dataclasses import dataclass

_SRT_TIME = r"(\d+):(\d\d):(\d\d)[,.](\d{3})"


@dataclass(frozen=True)
class Cue:
    """One subtitle cue, in seconds."""

    start_s: float
    end_s: float
    text: str


def srt_text(cues):
    """Render subtitle cues (anything with ``start_s``, ``end_s``, ``text``) as a SubRip
    (.srt) document."""
    blocks = [
        f"{n}\n{_srt_time(cue.start_s)} --> {_srt_time(cue.end_s)}\n{cue.text}\n"
        for n, cue in enumerate(cues, start=1)
    ]
    return "\n".join(blocks)


def parse_srt(text):
    """The cues (Cue) of a SubRip (.srt) document."""
    cues = []
    for block in re.split(r"\n\s*\n", text.strip().replace("\r\n", "\n")):
        lines = block.splitlines()
        for index, line in enumerate(lines):
            match = re.match(rf"\s*{_SRT_TIME}\s*-->\s*{_SRT_TIME}", line)
            if match:
                h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, match.groups())
                cues.append(
                    Cue(
                        (h1 * 3600 + m1 * 60 + s1) + ms1 / 1000,
                        (h2 * 3600 + m2 * 60 + s2) + ms2 / 1000,
                        "\n".join(lines[index + 1 :]),
                    )
                )
                break
    return tuple(cues)


def _srt_time(seconds):
    ms = max(0, round(seconds * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"
