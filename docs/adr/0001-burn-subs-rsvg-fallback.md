# 0001: `--burn-subs` falls back to rsvg-convert when ffmpeg lacks libass

Status: accepted (2026-10-19)

## Context

`compose.py --burn-subs` draws the subtitle cues into the video. The usual way is ffmpeg's
`subtitles` filter, which needs ffmpeg built with libass. The Homebrew ffmpeg on the
development machine has no libass, and no `drawtext` either, so it cannot draw text at all.
The scripts are meant to need only `python3` and `ffmpeg`.

## Decision

`--burn-subs` uses the `subtitles` filter when `ffmpeg -filters` lists it. Otherwise it renders
each cue to a transparent PNG strip with `rsvg-convert` (librsvg) and overlays the strips with
ffmpeg's `overlay` filter, each enabled only during its cue. If neither is available, compose
exits with an error that names both options and says the `.srt` is still available without
the flag.

## Consequences

- `rsvg-convert` is an optional dependency. It is needed only for `--burn-subs`, and only on an
  ffmpeg without libass. Everything else, including the `.srt`, works without it.
- There are two rendering paths with slightly different looks (font, outline). Only the
  overlay path is exercised on the development machine.
- The overlay path adds one ffmpeg input per cue, which is fine at demo lengths (tens of cues).
