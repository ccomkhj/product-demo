Status: ready-for-agent

# Spec: `product-demo` skill for narrated product-demo videos

## Problem Statement

Making a product-demo video by hand is slow. Someone has to write a script, record a voice-over, drive the browser while screen-recording, and then edit the recording so that what's on screen lines up with what's being said. Every change to the wording means recording again. Viewers of a plain screen recording also can't see what is happening: clicks, double-clicks and key presses leave no visible trace, so they can't tell what the presenter just did.

An agent can already drive a real browser through the connected Chrome DevTools MCP and record it with a screencast. But a raw recording of an agent driving the browser can't be used as is. The agent pauses for several seconds between tool calls while it thinks, so the video is full of frozen stretches, and its pacing has nothing to do with any narration.

## Solution

A Claude Code skill, `product-demo`, that turns a short interview into a finished, narrated demo video.

1. The agent asks the user for the product, the audience, and an ordered list of pages. For each page it asks for the URL, what to do there, and the key message.
2. The agent writes a **demo spec**: an ordered list of **scenes**, each with a URL, actions and narration. It shows the user the transcript to approve before anything is recorded.
3. The narration audio is generated first, so the exact length of each scene's audio is known before recording.
4. The agent drives the browser live through the Chrome DevTools MCP, with a screencast running. An injected **overlay** makes every action visible: a fake cursor glides to its target, clicks draw ripples, double-clicks draw a double ring, key presses show keycap badges, and Back navigations show a badge. The agent stamps **scene markers** into a **timeline** as it goes.
5. A compose step cuts each scene out of the raw recording and removes the frozen agent-thinking frames. It fits each scene to its narration length, then joins the scenes, muxes the audio and writes subtitles. The result is a polished `final.mp4`, a `transcript.md`, and a subtitle file.

The user gets a video where the visuals and the narration line up, every interaction is visible, and changing the script only means editing text and running the pipeline again.

## User Stories

1. As a product person, I want to describe my demo in a short conversation, so that I don't have to write a storyboard format by hand.
2. As a product person, I want to give an ordered list of URLs with what to show on each, so that the video follows the story I want to tell.
3. As a product person, I want to state the audience and key message per page, so that the narration is pitched correctly.
4. As a product person, I want to review and edit the full transcript before recording starts, so that I don't pay the cost of re-recording because of wording.
5. As a product person, I want the demo spec saved as a file in a per-demo folder, so that I can tweak it and run the pipeline again later.
6. As a product person, I want each demo kept in its own folder under a slug, so that several demos don't overwrite each other.
7. As a product person, I want to say whether a page needs a login, so that the agent pauses and lets me sign in before recording.
8. As a product person, I want my login to persist between runs in the MCP browser profile, so that I only sign in once.
9. As a product person, I want a default 1920×1080 viewport, so that the output is ready for standard screens and slides without extra setup.
10. As a product person, I want to override the viewport in the demo spec, so that I can make vertical or smaller demos when needed.
11. As a product person, I want narration generated with macOS `say` by default, so that the skill works without any API keys.
12. As a product person, I want OpenAI TTS used automatically when an OpenAI API key is set, so that I get a more natural voice with no extra configuration.
13. As a product person, I want ElevenLabs used automatically when an ElevenLabs API key is set, so that I can use my preferred premium voice.
14. As a product person, I want to force a TTS backend with a flag or in the demo spec, so that I can pick one explicitly when several keys are set.
15. As a product person, I want to choose the voice in the demo spec, so that the narrator matches my brand.
16. As a viewer, I want to see a cursor move to an element before it is clicked, so that I can follow where attention is going.
17. As a viewer, I want to see a ripple where a click lands, so that I know a click happened.
18. As a viewer, I want a double-click to look different from a single click, so that I can tell them apart.
19. As a viewer, I want a keycap badge for key presses, including modifiers such as "⌘ K", so that keyboard shortcuts are visible.
20. As a viewer, I want named keys to show readable labels ("Space", "Enter", "←"), so that I recognise them at once.
21. As a viewer, I want a visible "◀ Back" badge when the demo navigates back, so that page changes make sense.
22. As a viewer, I want optional lower-third captions, so that sections can be titled on screen.
23. As a viewer, I want the overlay never to block or change the page's own behaviour, so that the demo shows the real product.
24. As a viewer, I want no frozen stretches in the video, so that the demo feels smooth and professional.
25. As a viewer, I want each scene's visuals to last exactly as long as its narration, so that what I hear matches what I see.
26. As a viewer, I want short scenes to hold their final frame rather than cut away early, so that the narration finishes over the right page.
27. As a viewer, I want long scenes sped up only modestly (at most 1.5×), so that the motion still looks natural.
28. As a viewer, I want scenes too long even at 1.5× to get trailing silence instead of faster playback, so that the video never looks frantic.
29. As a viewer, I want subtitles that follow the narration sentence by sentence, so that I can watch with the sound off.
30. As a product person, I want subtitles as a separate file by default, so that I can upload them to a video platform.
31. As a product person, I want an option to burn subtitles into the video, so that they show on platforms that don't support subtitle files.
32. As a product person, I want a `transcript.md` with each scene's title and narration, so that I can reuse the copy in docs or blog posts.
33. As a product person, I want the output as a uniform 30 fps H.264 MP4, so that it plays everywhere.
34. As a product person, I want to re-run only the compose step after recording, so that I can change subtitle or fitting options without recording again.
35. As a product person, I want to change the narration and re-run TTS and compose, so that small script edits don't force a new recording when the visuals still fit.
36. As the agent, I want exact MCP call recipes for point, click, double-click, key, type, scroll, back and wait, so that I drive the browser the same way every run.
37. As the agent, I want the overlay to be safe to inject more than once, so that I can re-inject it after every navigation without breaking anything.
38. As the agent, I want a one-line command that records a scene marker with a wall-clock time, so that I can mark scene boundaries without doing any timing maths myself.
39. As the agent, I want a recording-start marker, so that scene times can be converted to positions in the raw video.
40. As the agent, I want each scene's audio length known before recording, so that I can stay on a scene about as long as its narration.
41. As the agent, I want clear validation errors when the demo spec is malformed (missing ids, duplicate ids, unknown action types, empty narration), so that I can fix it before generating audio.
42. As the agent, I want clear errors when the timeline is inconsistent (a missing recording start, a scene with no end, an end before its start, a scene not in the spec), so that compose fails loudly instead of producing a broken video.
43. As the agent, I want a verification step that checks the final length against the total narration length, so that I can catch fitting bugs.
44. As the agent, I want to pull one frame per scene and look at it, so that I can confirm the overlay and page state are right before handing the video over.
45. As the agent, I want to stay in a single tab during recording, so that the screencast (which is tied to one page) captures everything.
46. As a maintainer, I want the scripts to use only the Python stdlib plus ffmpeg/ffprobe, so that nothing has to be installed beyond what's already on the machine.
47. As a maintainer, I want the skill installed with a single symlink into the Claude skills directory, so that edits in the project folder take effect immediately.
48. As a maintainer, I want raw recordings and audio files gitignored, so that the repo stays small.
49. As a maintainer, I want SKILL.md short and focused on *why*, with schemas and recipes in references, so that it follows the skills-repo convention and stays readable for agents.

## Implementation Decisions

**Modules**

- **SKILL.md**: the workflow (interview → spec → audio → record → compose → verify) and the reasoning behind each step. It stays short. Details are pushed to the references.
- **Demo spec reference**: the demo spec schema plus one worked example.
- **MCP recipes reference**: the exact Chrome DevTools MCP call sequences for each action type, overlay injection, and recording start and stop.
- **Overlay** (browser JavaScript, injected into the page): an idempotent script, guarded on and exposed as a single global `__demo` API. Its operations are `point(el)` (glide the fake cursor to the element's centre in about 600 ms), `badge(text)`, and `caption(text)`. Capture-phase listeners on `mousedown`, `dblclick` and `keydown` draw ripples, double rings and keycap badges on their own, because MCP `click` and key presses send real CDP input events. Every overlay element uses `pointer-events: none` and the highest z-index.
- **TTS** (Python CLI): demo spec → one 48 kHz mono WAV per scene, plus a durations file mapping scene id → seconds. Backends are `say`, `openai` (`gpt-4o-mini-tts`) and `elevenlabs`. The HTTP backends use `urllib`. All output is normalised to WAV with ffmpeg, and lengths are measured with ffprobe.
- **Mark** (Python CLI): appends `{scene, event: start|end, t_ms}` to the timeline file, using wall-clock milliseconds. A special `rec_start` event anchors the timeline.
- **Compose** (Python CLI): raw screencast + timeline + durations + demo spec → `final.mp4`, `transcript.md`, subtitles `.srt`. It takes an optional `--burn-subs` flag.

**Edit plan: the deep module inside compose**

- Compose is split into a pure **edit plan** builder and a thin ffmpeg runner. The builder takes (demo spec, timeline, durations) and returns a plan. For each scene, in spec order, the plan holds: the trim window in raw-video seconds (`start − rec_start`, `end − rec_start`), the narration audio length, a fit strategy, and the scene's final length. The plan also holds the full list of subtitle cues with absolute times in the final video.
- The plan's per-scene lengths are worked out from the trimmed segment length. The real length after `mpdecimate` is only known once ffmpeg runs, so the runner measures the decimated length and asks the planner for the fit. The **fit rule** is a pure function of (video length after decimation, audio length):
  - video ≤ audio → **hold** the last frame (tpad) for `audio − video`; scene length = audio.
  - audio < video ≤ 1.5 × audio → **speed up** by `video / audio`; scene length = audio.
  - video > 1.5 × audio → speed up 1.5× and **pad the audio** with silence; scene length = video / 1.5.
- Subtitle cues: each scene's narration is split into sentences. The scene's *audio* length is shared out across the sentences in proportion to their character counts. Cues are offset by the cumulative *scene* lengths, so silence padding pushes later scenes back correctly.
- Validation lives in the planner, and it raises a clear error for every timeline inconsistency listed in user story 42.
- The ffmpeg runner builds each command as an argument list: per-scene trim → `mpdecimate,setpts=N/FRAME_RATE/TB` → fit filter → 30 fps H.264, then concat video, concat audio with `apad`, and mux. Commands are built separately from running them.

**TTS backend choice**

- Precedence: CLI `--backend` flag → `voice.backend` in the demo spec → auto-detect (`ELEVENLABS_API_KEY` → elevenlabs, else `OPENAI_API_KEY` → openai, else `say`). This choice is a pure function of (flag, spec, environment).
- Demo spec validation (ids present and unique, actions from the allowed set, narration non-empty, viewport defaulted to 1920×1080) is a pure function shared by TTS and compose.

**Demo spec shape**

- Top level: `title`, `product`, `audience`, `viewport {width, height}`, `voice {backend, voice}`, `scenes[]`.
- Scene: `id`, `title`, `url`, `actions[]`, `narration`.
- Action types: `point`, `click`, `dblclick`, `key`, `type`, `scroll`, `back`, `wait`. Element targets are described in natural language. The agent resolves them to MCP snapshot uids at record time, because uids are not stable across runs.

**Recording protocol**

- `resize_page` to the viewport → `navigate_page` to the first URL → inject the overlay → `screencast_start` (writing into the demo folder) → mark `rec_start`. For each scene: mark start → run the actions using the recipes → wait until the time spent on screen roughly covers the narration → mark end. Finish with `screencast_stop`.
- The overlay is re-injected with `evaluate_script` after **every** navigation. An MCP init script only applies to the next navigation, so it can't be relied on.
- Before a click, the agent calls `__demo.point` on the target element so the cursor glides there first. Before `navigate_page(type=back)`, it calls `__demo.badge("◀ Back")`.
- Auth: no auth code. The MCP browser profile persists, so on the first run the user logs in by hand in the MCP browser.

**Per-demo folder layout** (artifacts, not source paths): demo spec, the audio directory with per-scene WAVs and durations, the timeline, the raw recording, and the final outputs.

**Install and repo**: a symlink from the project folder into the Claude skills directory. A `.gitignore` for raw recordings and WAVs. Because `~/personal` is itself a git repo, the agent asks the user whether product-demo should be a nested repo or ignored there, and asks before committing. No AI co-author trailers.

## Testing Decisions

- **What makes a good test**: it exercises a module's public interface (CLI or the pure planner/selector functions) and asserts on observable results: plan contents, cue timings, error messages, output file lengths. Tests don't assert on internal helpers or the exact ffmpeg filter strings.
- **Seam 1: edit plan** (main seam). Feed a demo spec, timeline and durations; assert on the per-scene trim windows, fit strategy (hold / speed-up / pad), speed factor, scene lengths, and subtitle cue times. Cover the edges: exactly 1.5×, zero-length decimated video, silence padding moving later cues back, sentence splitting with abbreviations or a single sentence, and every timeline-inconsistency error.
- **Seam 2: spec validation and backend choice.** Assert that valid specs are accepted with defaults filled in (the viewport), that each malformed-spec case is rejected with a clear message, and that the backend precedence is right across flag / spec / env combinations, with the environment injected rather than read from the process.
- **Seam 3: compose CLI smoke test.** Generate a synthetic raw video with ffmpeg `testsrc`, including a frozen stretch, plus synthetic WAVs (`anullsrc`/sine), a timeline and durations. Run compose end to end and use ffprobe to check that `final.mp4` exists, has video and audio streams, runs at 30 fps, and lasts about as long as the sum of the planned scene lengths. Also check that the `.srt` and `transcript.md` are written. Skip it if ffmpeg is missing.
- **Not covered by automated tests**: live MCP driving and the overlay's look. These are checked by the end-to-end smoke run: a 3-scene demo on a public site (example.com → link click → back, plus a Space key press on a scrollable page), followed by a check of the final length and one extracted frame per scene, inspected for the cursor, ripple and key badge. The TTS `say` backend gets a manual smoke test on a 2-scene spec, checking that every duration is > 0.
- **Prior art**: none. The repo is new. Tests use pytest and run with `uv run pytest`.

## Out of Scope

- Multi-tab or multi-window demos (the screencast is tied to one page).
- Automated login flows, credential storage, or any auth code.
- Recording real mouse motion or a human-driven session. Only agent-driven recording is supported.
- Background music, intro and outro cards, branding watermarks, and transitions between scenes.
- Video editing UI. Changes go through editing the demo spec and running again.
- Lip-synced avatars or a webcam overlay.
- Translation or multi-language narration in one run.
- Cross-platform support for the `say` default (it is macOS-only; elsewhere an API backend is needed).
- Uploading or publishing the video anywhere.

## Further Notes

- The central design choice is **record-then-edit**: generate the narration first, record with scene markers, then fit the video to the audio. Without it, the agent's pauses between tool calls make the raw recording unusable.
- `mpdecimate` removes near-duplicate frames, so animations that are legitimately slow and subtle may be shortened too. The 1.5× speed cap and the hold-last-frame rule keep that from being visible.
- The edit plan has to take the *decimated* length as an input, because only ffmpeg knows it. Keeping the fit rule a pure function of (decimated length, audio length) is what keeps the planner testable while the runner does the measuring.
- Element targets in the demo spec are natural-language descriptions, not selectors, so specs keep working across UI changes. Resolving them depends on the agent reading MCP snapshots at record time.
