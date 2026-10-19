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
its workspace roots, which is normally that directory. The scripts need only `python3` and
`ffmpeg`.

## 1. Interview

Ask for the product, the audience, and the ordered list of pages. For each page: the URL,
what to do there, the key message, and whether it needs a login. Pick a short slug.
Done when every page has all four answers.

## 2. Spec and transcript approval

Write `demos/<slug>/demo.json` following `references/demo-spec.md`. Narration is plain
spoken sentences, pitched at the audience and carrying each page's key message. Describe
targets in words ("the Save button"), because element uids change between runs and are
resolved while recording.

Show the user every scene's title and narration and get explicit approval before going
on. Wording is cheap to change now; after recording, it can only be changed if the new
narration still fits the visuals.

## 3. Narration audio first

```
python3 <skill dir>/scripts/tts.py demos/<slug>/demo.json [--backend say|openai|elevenlabs]
```

It validates the spec, writes `audio/<scene-id>.wav` and `audio/durations.json`, and prints
each scene's length. Knowing these lengths before recording is what gives each scene a
pacing target. Without `--backend` it uses `voice.backend`, then an ElevenLabs or OpenAI
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
- **Mark every scene start and end.** The markers are the only link between the raw video
  and the spec; compose fails loudly if one is missing.

Done when `screencast_stop` has saved `demos/<slug>/raw.mp4` and the timeline holds
`rec_start` plus a start and an end for every scene.

## 6. Compose

```
python3 <skill dir>/scripts/compose.py demos/<slug>/demo.json [--burn-subs]
```

It prints one line per scene (`<id>  <hold|speedup|pad>  <speed>x  video <s>s  audio <s>s
scene <s>s`), then `total <s>s`, and writes `final.mp4`, `final.srt` and `transcript.md`.
Burn subtitles in only when the user's platform can't load an `.srt`.

## 7. Verify

Check the result before handing it over; a fitting bug or a missing overlay is invisible
in the compose output.

- **Length:** `ffprobe -v error -show_entries format=duration -of csv=p=0 demos/<slug>/final.mp4`
  must match the `total` line to within 1/30 s per scene (each scene is cut on a whole
  frame).
- **Frames:** for each scene, extract a contact sheet from the part of the scene where its
  actions play (`offset` = sum of the earlier scenes' `scene` values; `active` = its
  `video` value ÷ its speed; ffmpeg won't create `frames/`, so make it first):
  `ffmpeg -v error -y -ss <offset> -t <active> -i demos/<slug>/final.mp4 -vf "fps=6/<active>,scale=960:-1,tile=2x3" -frames:v 1 demos/<slug>/frames/<scene-id>.png`
  Look at each with Read. Confirm the page is the scene's page, the cursor sits on each
  pointed target, clicks show a ripple, keys show their keycap and Back shows its badge.
  A ripple lasts about 0.65 s, so six samples often miss it. Before calling one missing,
  tile every frame of that second, cropped around the target:
  `ffmpeg -v error -y -ss <time> -t 1 -i demos/<slug>/final.mp4 -vf "crop=480:270:<x-240>:<y-135>,tile=6x5" -frames:v 1 demos/<slug>/frames/<scene-id>-dense.png`
  (`x`, `y` in output-video pixels). A link click with no ripple at all means the
  hold-the-navigation step of the click recipe was skipped.

Done when the length matches and every scene's expected overlay effect has been seen.

## Re-running

- **Compose only** (different subtitle or fitting options): run step 6 again. It reads the
  existing recording, timeline and audio.
- **Narration edit:** edit `narration` in `demo.json`, then run step 3 and step 6. This
  works as long as the visuals still show what the new words describe; otherwise record
  again from step 5.
