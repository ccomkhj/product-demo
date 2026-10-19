# 0002: Actions start on narration phrases, via `at` and beat markers

Status: accepted (2026-10-19)

## Context

Compose drops every frozen frame, so a scene's video is only its motion: a few seconds of
glides, scrolls and clicks. When that is shorter than the narration, compose used to play
it all at once and freeze the last frame for the rest. In the Wikidata sample, the Hexed
scene's statements all flew past in the first 5 s of a 22 s narration, and the screen then
sat on the last target while the voice was still describing the first ones.

The pauses in the raw recording can't be used for pacing, because they come from the
agent's thinking time between tool calls, not from anything in the narration.

## Decision

An action may name an `at` phrase from its scene's narration. While recording, the agent
marks a `beat` in the timeline right before each such action. Compose cuts the scene at
its beats, decimates each segment on its own, and holds each segment's first frame until
the narration reaches its phrase. A phrase's time comes from the same character share as
the subtitle cues, so an action starts with the cue that mentions it. A segment that runs
past the next phrase pushes the rest back. The synced scene is then fitted to its
narration as a whole (hold, speed-up or pad), as before.

## Consequences

- Specs without `at` and timelines without beats compose exactly as before.
- Beats are matched to `at` actions by order and count; a missing or extra beat fails
  compose with both counts. The spec rejects phrases that aren't in the narration, or that
  appear out of order.
- Timing is only as good as the character share: a TTS voice that dwells on some words
  shifts the phrase a little. Per-word timestamps from the TTS backend would be more
  precise, but `say` doesn't provide them.
- The agent must run each beat as its own call, between MCP calls, so that it lands in order.
