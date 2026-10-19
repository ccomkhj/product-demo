# 04: Subtitles, transcript and burn-in

**What to build:** Next to `final.mp4`, compose also writes:
- a subtitle `.srt` file that follows the narration sentence by sentence
- a `transcript.md` with each scene's title and narration, so the copy can be reused in docs or blog posts

An optional `--burn-subs` flag burns the subtitles into the video instead of only writing them to a separate file.

The edit plan gains the full list of subtitle cues, with absolute times in the final video. Each scene's narration is split into sentences, and abbreviations must not cause false splits. The scene's *audio* length is shared out across its sentences in proportion to their character counts. Cues are offset by the cumulative *scene* lengths, so silence padding on one scene correctly pushes all later cues back. Subtitles can be changed by re-running only compose, without recording again.

**Blocked by:** 03 (Compose a fitted video from a recording and timeline)

**Status:** ready-for-agent

- [ ] Planner tests check cue start/end times for a multi-sentence scene, a single-sentence scene, and narration containing abbreviations (e.g. "e.g.", "Dr.").
- [ ] A planner test shows that silence padding in an earlier scene moves later scenes' cues back by the padded amount.
- [ ] Compose writes a valid `.srt` and a `transcript.md` (scene titles + narration in spec order).
- [ ] `--burn-subs` produces a `final.mp4` with the subtitles burned in. Without the flag, subtitles are only in the separate file.
- [ ] The compose smoke test also checks that the `.srt` and `transcript.md` exist and that `--burn-subs` produces a playable video.
