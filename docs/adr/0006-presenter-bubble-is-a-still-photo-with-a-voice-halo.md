# 0006: The presenter bubble is a still photo with a voice halo

Status: accepted (2026-10-21).

## Context

A Loom-style face in the corner makes a demo feel presented, not just captured. A real
talking head needs lip-sync. Local models (SadTalker, Hallo, MuseTalk and the like) are
built for CUDA and are slow or don't run on Apple Silicon, and some have non-commercial
licences. Hosted ones (HeyGen, D-ID and the like) charge per minute and need an API key.
Both would break the skill's promise that it is free and runs offline.

## Decision

The optional `avatar` field draws a still photo in a corner, cropped to a circle with a
white rim. A halo around the rim brightens with the narration's loudness, frame by frame.
`scripts/avatar.py` does this with stdlib and ffmpeg only. ffmpeg draws the bubble and the
halo once as PNGs. The per-frame opacity is read from the final, fitted `audio.wav` and fed
to ffmpeg with `sendcmd`. Compose overlays the bubble after the subtitles. Subtitles at the
same edge wrap clear of a bottom-corner bubble.

## Consequences

- The halo follows the fitted narration track, so it is in step with the voice without
  any extra sync work.
- The halo is lit by loudness relative to the track's own loud end, so quiet and loud
  voices pulse alike.
- The mouth doesn't move. A lip-sync backend could be added behind the same `avatar`
  field later, the way `voice.backend` chooses a TTS.
- The bubble is one more x264 pass over the video, which takes a few seconds per minute.
- The bubble covers that corner of the page for the whole video. The spec author picks a
  corner the actions don't use.
