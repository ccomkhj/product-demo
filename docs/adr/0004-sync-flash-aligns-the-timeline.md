# 0004: A sync flash lines the timeline up with the recording

Status: accepted (2026-10-19)

## Context

Compose turns the timeline's wall-clock marks into raw-video seconds by subtracting the
`rec_start` mark. That assumes the mark was taken the moment the recording began. But the
MCP records with Chrome's `Page.startScreenRecording`, which starts on Chrome's own clock
and reports nothing, and the agent's mark arrives as a separate tool call several seconds
later. In the first synced Wikidata take (see 0002), the offset was about 7 s. Every beat
then cut seconds before its action, so each action played at the phrase of the one after
it. Without beats this only shifted the scene windows a little, which is why it went
unnoticed.

## Decision

Right after `screencast_start`, the agent calls `__demo.sync()`. The overlay fills the
viewport with pure magenta for 0.4 s and resolves to `Date.now()` once the flash has been
painted. The agent records that as `mark.py rec_start --sync-ms <ms>`. Compose scans the
raw video's frames (ffmpeg `signalstats` on a 64×36 copy) for the first one whose minimum
chroma is magenta throughout. It then sets raw time 0 to `sync_ms` minus that frame's
time. The flash falls before the first scene's start marker, so it never reaches the final
video.

## Consequences

- Alignment is accurate to about a frame, however slow the tool calls are.
- A `rec_start` without `sync_ms` keeps the old behaviour, so earlier demos still compose.
  A `sync_ms` with no flash in the video fails compose with a hint.
- A page whose viewport is genuinely all magenta before the flash would be taken for it.
  That is unlikely enough to accept.
- Compose decodes the whole raw video once more to find the flash; at demo lengths that
  takes seconds.
