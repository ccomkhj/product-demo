# 0003: Subtitles are burned in by default

Status: accepted (2026-10-19). Amends 0001.

## Context

Compose drew subtitles into the video only with `--burn-subs`; otherwise they came only
as `final.srt`. Most places a demo gets shared (chat, slides, social posts) never load a
side-car `.srt`, so viewers watching with the sound off saw no words at all.

## Decision

Compose burns the subtitles in by default, with the method from 0001 (libass, else
rsvg-convert overlays). `--no-burn-subs` leaves them out. If neither method is available,
the default only warns and still writes the `.srt`. An explicit `--burn-subs` keeps 0001's
behaviour and fails.

## Consequences

- A clean video for a player that loads the `.srt` now needs `--no-burn-subs`.
- The compose `subtitles` line says whether, and how, the subtitles were burned in.
