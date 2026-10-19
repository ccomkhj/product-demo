---
name: product-demo
description: Narrated product-demo video from a short interview, recorded live in the Chrome DevTools MCP browser with a visible cursor, clicks and key presses, voiced and subtitled. Use when the user wants a product demo, walkthrough or screen-recorded tour of a web app, or wants to re-narrate or re-compose a demo in demos/<slug>/.
---

# Product demo

Turns an interview into `final.mp4`, `final.srt` and `transcript.md`. The central idea is
**record-then-edit**: the agent pauses for seconds between tool calls, so a raw recording
of it driving the browser is full of frozen stretches with pacing unrelated to any voice.
So the narration is made first, the recording carries scene markers, and compose cuts each
scene out, drops the frozen frames and fits the rest to its narration.

`<skill dir>` below is the directory of this file. Each demo lives in `demos/<slug>/`
under the current working directory: the MCP can only write files (the screencast) inside
its workspace roots, which is normally that directory. The scripts need `python3` and
`ffmpeg`. `rsvg-convert` (librsvg) is optional: it is needed only for `--burn-subs` on an
ffmpeg without libass (see `docs/adr/0001-burn-subs-rsvg-fallback.md`).

## 1. Interview

Ask for the product, the audience, and the ordered list of pages. For each page: the URL,
what to do there, the key message, and whether it needs a login. Pick a short slug.
Done when every page has all four answers.

## 2. Spec and transcript approval

Write `demos/<slug>/demo.json` following `references/demo-spec.md`. Narration is plain
spoken sentences, pitched at the audience and carrying each page's key message. Describe
targets in words ("the Save button"), because element uids change between runs and are
resolved while recording. Give every action the narration talks about an `at` phrase
from its narration: compose starts the action when the voice says it, so the screen shows
what the words describe instead of racing ahead of them.

Show the user every scene's title and narration and get explicit approval before going
on. Wording is cheap to change now; after recording, it can only be changed if the new
narration still fits the visuals.

## 3. Narration audio first

```
python3 <skill dir>/scripts/tts.py demos/<slug>/demo.json [--backend say|openai|elevenlabs]
```

It validates the spec, writes `audio/<scene-id>.wav` and `audio/durations.json`, and prints
each scene's length. Knowing these lengths before recording shows how much motion each
scene needs: compose holds the screen until each `at` phrase, and holds the last frame
when the motion runs out before the narration. Without `--backend` it uses `voice.backend`, then an ElevenLabs or OpenAI
key if one is set, then macOS `say`.

## 4. Logins

For every scene with `"login": true`, navigate the MCP browser there and ask the user to
sign in by hand, then wait for them to confirm. The MCP browser profile persists, so later
runs skip this; there is no auth code to write.

## 5. Record

Follow `references/mcp-recipes.md` literally: its "Recording start and stop" section, then
one action recipe per spec action. The rules that matter, and why:

- **One tab.** The screencast is tied to one page; anything in another tab is not recorded.
- **Re-inject the overlay after every navigation.** A new document has no overlay, and an
  MCP init script only applies to the next navigation, so it can't be relied on.
- **Point before every click, badge before every Back.** Viewers can only follow what they
  can see: the cursor shows where attention goes, the badge explains the page change.
- **Resolve targets at record time** from a fresh `take_snapshot` on each page.
- **Flash `__demo.sync()` right after `screencast_start`** and pass its result to
  `rec_start --sync-ms`. It is the only reliable link between wall-clock marks and video
  time: Chrome doesn't say when the recording began.
- **Mark every scene start and end, and a beat before every `at` action.** The markers are
  the only link between the raw video and the spec; compose fails loudly if one is missing.
  Run each beat as its own call between MCP calls, never batched with them, so it lands
  in order.

Done when `screencast_stop` has saved `demos/<slug>/raw.mp4` and the timeline holds
`rec_start` with a `sync_ms`, plus a start, an end and one beat per `at` action for every
scene.

## 6. Compose

```
python3 <skill dir>/scripts/compose.py demos/<slug>/demo.json [--no-burn-subs]
```

It prints one line per scene with its fit (hold, speed-up or pad) and lengths, then a
`total` line, and writes `final.mp4`, `final.srt` and `transcript.md` (output format in
`references/verify.md`). The subtitles are burned into the video too, so the words show
wherever it plays; pass `--no-burn-subs` only when the user wants a clean video with the
`.srt` alongside.

## 7. Verify

Check the result before handing it over; a fitting bug or a missing overlay is invisible
in the compose output. Use the recipes in `references/verify.md`:

- **Length:** ffprobe's duration of `final.mp4` must match compose's `total` line, not the
  sum of the narration lengths, because padded scenes run longer than their narration.
- **Frames:** a contact sheet per scene must show the scene's page, the cursor on each
  pointed target, a ripple per click, a keycap per key press, a badge per Back, and the
  burned-in subtitle. Effects are short (a ripple lasts about 0.65 s), so tile every frame
  of the moment before calling one missing.
- **Sync:** at the start of the subtitle cue holding each `at` phrase (from `final.srt`),
  its action must be starting, not already over.

Done when the length matches, every scene's expected overlay effect has been seen, and
the actions keep pace with the voice.

## Re-running

- **Compose only** (different subtitle or fitting options): run step 6 again. It reads the
  existing recording, timeline and audio.
- **Narration edit:** edit `narration` in `demo.json`, then run step 3 and step 6. This
  works as long as the visuals still show what the new words describe and every `at`
  phrase is still in the narration; otherwise record again from step 5.
