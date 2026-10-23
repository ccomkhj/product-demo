# 0010: Post-processing draws text and shapes with rsvg-convert

Status: accepted (2026-10-23)

## Context

Post-processing draws a text watermark, arrow and text callouts, title and closing cards,
and the contact sheet's frame times. The development machine's ffmpeg has neither libass
nor `drawtext` (ADR-0001), so ffmpeg alone cannot draw text. The scripts are meant to need
only `python3` and `ffmpeg`.

## Decision

The post plan draws each of these as an SVG (`scripts/post_draw.py`). The runner renders it
to a PNG with `rsvg-convert` (librsvg) and ffmpeg overlays or joins the PNG. There is no
second rendering path. Before rendering, the runner checks for `rsvg-convert`. When it is
missing, only a recipe that draws something fails, with `postprocess: error: ...` naming
what needs it and the install hint (`brew install librsvg`).

## Consequences

- `rsvg-convert` is optional. Trim, cuts, speed, an image watermark, the progress bar,
  subtitle tracks, chapters, click sounds, music, size cap, GIF and poster work without it.
  Cards, callouts, a text watermark and the contact sheet need it.
- One rendering path gives the same look everywhere, and the SVGs can be tested as plain
  text without ffmpeg.
- rsvg-convert only loads images next to the SVG, so a card's logo is copied into the work
  folder first.
