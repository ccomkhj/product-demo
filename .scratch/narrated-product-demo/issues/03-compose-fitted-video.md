# 03: Compose a fitted video from a recording and timeline

**What to build:** Given a raw screencast, a timeline, a durations file and a demo spec, the compose CLI writes a `final.mp4`. In it, each scene is cut from the raw recording, its frozen agent-thinking frames are removed, and it is fitted to its narration length. The scenes are then joined in spec order and muxed with the narration audio, giving a uniform 30 fps H.264 video. This ticket also adds the `mark` CLI, which the agent uses during recording to stamp markers into the timeline.

**Mark:** a one-line command appends `{scene, event: start|end, t_ms}` with wall-clock milliseconds. A special `rec_start` event anchors the timeline to the start of the raw video.

**Edit plan (the deep module):** a pure builder takes (demo spec, timeline, durations) and returns a plan. For each scene, in spec order, the plan holds:
- the trim window in raw-video seconds (`start − rec_start`, `end − rec_start`)
- the narration audio length
- the fit strategy and speed factor
- the scene's final length

The fit rule is a pure function of (video length after decimation, audio length):
- video ≤ audio → **hold** the last frame for `audio − video`; scene length = audio.
- audio < video ≤ 1.5 × audio → **speed up** by `video / audio`; scene length = audio.
- video > 1.5 × audio → speed up 1.5× and **pad the audio** with silence; scene length = video / 1.5.

The planner raises a clear error for every timeline inconsistency: a missing `rec_start`, a scene with no end, an end before its start, and a scene that is in the timeline but not in the spec.

**Runner:** a thin layer that builds each ffmpeg command as an argument list, separately from running it:
1. Trim each scene, then run `mpdecimate,setpts=N/FRAME_RATE/TB`.
2. Measure the decimated length with ffprobe and ask the planner for the fit.
3. Apply the fit filter and encode at 30 fps H.264.
4. Concat the video, concat the audio with `apad` where needed, and mux.

Compose can be re-run on its own after recording.

Subtitles and the transcript are not part of this ticket (see 04).

**Blocked by:** 01 (Narrate a demo spec with macOS `say`), for demo spec validation and the durations file format.

**Status:** ready-for-agent

- [ ] `mark` appends well-formed events to the timeline, including `rec_start`, with no timing maths needed from the caller.
- [ ] Planner tests check trim windows, fit strategy, speed factor and scene lengths for each fit case, including exactly 1.5× and a zero-length decimated video.
- [ ] Planner tests cover each timeline-inconsistency error and its message.
- [ ] Tests use only the public planner/fit functions and the CLI. They don't assert on exact ffmpeg filter strings.
- [ ] Smoke test (skipped if ffmpeg is missing): a synthetic `testsrc` raw video with a frozen stretch, synthetic WAVs, a timeline and durations go through compose end to end. ffprobe then confirms that `final.mp4` has video and audio streams, runs at 30 fps, and lasts about as long as the sum of the planned scene lengths.
