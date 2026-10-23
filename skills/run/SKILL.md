---
name: run
description: Narrated product-demo video from a short interview, recorded live in the Chrome DevTools MCP browser with a visible cursor, clicks and key presses, voiced and subtitled. Use when the user wants a product demo, walkthrough or screen-recorded tour of a web app, or wants to re-narrate or re-compose an existing demo folder, or to configure its voice, presenter or subtitles.
argument-hint: "[--configure] <what to demo>"
---

# Product demo

Turns an interview into `<slug>.mp4`, `<slug>.srt` and `transcript.md`. The central idea is
**record-then-edit**: the agent pauses for seconds between tool calls, so a raw recording
of it driving the browser is full of frozen stretches with pacing unrelated to any voice.
So the narration is made first, the recording carries scene markers, and compose cuts each
scene out, drops the frozen frames and fits the rest to its narration.

The plugin root is `${CLAUDE_PLUGIN_ROOT}`: scripts, references and assets live there. In the
reference files, the plugin-root variable (`CLAUDE_PLUGIN_ROOT`) stands for this same
directory. Each demo lives in `demos/<slug>/`
under the current working directory: the MCP can only write files (the screencast) inside
its workspace roots, which is normally that directory. The scripts need Python 3.11+ and
`ffmpeg`; stock macOS `python3` is 3.9, so run them through `uv` exactly as written below
(`uv run --project ${CLAUDE_PLUGIN_ROOT}` picks a 3.11+ interpreter from any working directory). `rsvg-convert` (librsvg) is optional: it is needed only for `--burn-subs` on an
ffmpeg without libass (see `${CLAUDE_PLUGIN_ROOT}/docs/adr/0001-burn-subs-rsvg-fallback.md`).

## Modes

- **Default** (`/product-demo:run <request>`): ask no setting questions. Settings come from the
  request, then the saved defaults, then the built-in defaults (see **Settings**). The
  gates still apply: missing pages or URLs (step 1), script approval (step 2), manual
  sign-in (step 3), and a missing own voice (see **Settings**).
- **Configure** (`--configure` in the arguments, or the user asks to change the saved
  settings): follow `${CLAUDE_PLUGIN_ROOT}/references/configure.md` before step 1. It asks every setting,
  records the voice if chosen, and saves the answers as the new defaults.

## 1. Interview

Infer the purpose from the request and read only the selected template:

| Purpose | Template | Suggested target |
|---------|----------|------------------|
| Feature announcement | `${CLAUDE_PLUGIN_ROOT}/references/templates/feature-announcement.md` | 30 s |
| Customer tutorial | `${CLAUDE_PLUGIN_ROOT}/references/templates/customer-tutorial.md` | 90 s |
| Sales walkthrough | `${CLAUDE_PLUGIN_ROOT}/references/templates/sales-walkthrough.md` | 120 s |
| Custom | No template required | User's choice |

Explicit user structure and duration override template suggestions (a 60-second
announcement stays 60 seconds). If purpose is unclear, use custom with no target. Do not
add a separate template approval.
Ask only for missing product, audience, viewer outcome, and ordered pages. For each page:
the URL, what to do there, the key message, and whether it needs a login. Never invent
URLs, UI controls, or sales metrics. Pick a short slug. If `demos/<slug>/` already exists,
it holds an earlier take that git cannot restore (`demos/` is ignored) and later steps
overwrite its spec, audio and videos: ask whether to replace it or use a new slug (for
example `<slug>-<YYYY-MM-DD>`) before writing anything there. Re-running an existing demo
(see **Re-running**) is the exception. Done when those details and a free or approved slug
are known.

## 2. Spec and transcript approval

Resolve the voice, presenter, viewport and subtitle policy with **Settings** below before
writing the spec.

Write `demos/<slug>/demo.json` following `${CLAUDE_PLUGIN_ROOT}/references/demo-spec.md`. Narration is spoken
sentences, pitched at the audience and carrying each page's key message. Hook the viewer,
don't introduce the site. Talk to "you" about what they gain, not what the screen shows.
Vary the rhythm, end on a payoff. Every page needs one dry line that would make a
colleague smirk. Use understatement, a before/after contrast, a callback, or a deadpan
true fact. Aim at shared pain, never at anyone. If it needs an exclamation mark, cut it.
Describe
targets in words ("the Save button"), because element uids change between runs and are
resolved while recording. Give every action the narration talks about an `at` phrase
from its narration: compose starts the action when the voice says it, so the screen shows
what the words describe instead of racing ahead of them.

Write optional `purpose`, `outcome`, and `target_duration_s` metadata. Copy a template's
suggested target only when the user has not supplied one; custom specs need no target.
Add `prerequisites` and `expected_outcomes` for observable entry and success states using
`${CLAUDE_PLUGIN_ROOT}/references/readiness.md`. Keep targets in words and intermediate states in separate scenes.

Show the purpose, audience, outcome, duration target (if any), and every scene's title and
narration. Get explicit script approval before going on. Wording is cheap to change now;
after recording, it can only be changed if the new narration still fits the visuals.

## 3. Recording readiness

Run the local preflight before generating narration:

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/scripts/preflight.py demos/<slug>/demo.json [--backend say|kokoro|openai|elevenlabs|qwen3] [--burn-subs|--no-burn-subs]
```

Resolve failures before continuing. Carry the same explicit backend to TTS and subtitle
policy to compose. A successful local report does not verify browser access or login.
Follow `${CLAUDE_PLUGIN_ROOT}/references/readiness.md` to check MCP capabilities, entry pages, authentication,
and actual viewport in the recording browser. Do not rehearse state-changing actions.
If the chrome-devtools tools are missing or failed to connect, check the Node version
first (Browser entry check 1 in `${CLAUDE_PLUGIN_ROOT}/references/readiness.md`).
Defer state-dependent scenes until reached naturally, then check them before their start
markers. Restore the first scene's entry state before capture.

For `login: true`, verify the existing session; if signed out, ask the user to sign in
by hand and wait for confirmation, then recheck. Never collect credentials. A loaded page
alone does not prove authentication. Record brief evidence in `demos/<slug>/readiness.md`.

## 4. Narration audio first

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/scripts/tts.py demos/<slug>/demo.json [--backend say|kokoro|openai|elevenlabs|qwen3]
```

It validates the spec, writes `audio/<scene-id>.wav` and `audio/durations.json`, and prints
each scene's length. Knowing these lengths before recording shows how much motion each
scene needs: compose holds the screen until each `at` phrase, and holds the last frame
when the motion runs out before the narration. Without `--backend` it uses `voice.backend`, then an ElevenLabs or OpenAI
key if one is set, then the free local Kokoro model if `uv` is on PATH, then macOS `say`.

After TTS, always check each scene's measured length, with or without a target:

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/scripts/preflight.py demos/<slug>/demo.json --durations demos/<slug>/audio/durations.json
```

Carry the same backend/subtitle overrides here too. A `pace` failure means a scene's audio
is far longer than its words can take to say (a runaway synthesis, common with `qwen3`):
rerun `tts.py` with the same wording and recheck, since compose would otherwise hold a frozen
frame for the whole runaway. A `duration` warning means narration exceeds the
target by more than 10%; final video can be longer because scenes may be padded. Propose
shorter wording, get the existing script approval for changes, then regenerate audio and
rerun the check. Never truncate or accelerate speech automatically. If the user keeps the
longer script, note the accepted duration in the readiness report. When the user says the
voice is too slow or too fast, set `voice.speed` (0.5 to 2, keeps pitch) and rerun `tts.py`.

When the backend is `qwen3`, always run the audio check after the pace check (you can't
listen, and leaked or misspoken words are the worst defect a customer video can have):

```
uv run --script ${CLAUDE_PLUGIN_ROOT}/scripts/check_audio.py demos/<slug>/demo.json
```

It transcribes each scene locally with whisper, compares it with the narration as the voice
should say it (after `voice.pronounce`), and prints `ok` or `check`, the match ratio and the
differences per scene; it exits 1 on any `check`. On `check`:

- An extra word anywhere in a scene (leaked reference text at its edges, a stray word inside):
  rerun `tts.py`, then the pace and audio checks, at most twice.
- The same word wrong on every run (a mispronunciation): propose a `voice.pronounce` entry.
- A proper noun (product, place or person name), acronym or number: likely whisper's error,
  not the voice's. List the scene and words for the user to listen to; don't rerun.

## 5. Record

Follow `${CLAUDE_PLUGIN_ROOT}/references/mcp-recipes.md` literally: its "Recording start and stop" section, then
one action recipe per spec action. The rules that matter, and why:

- **Check scene states.** Evaluate prerequisites immediately before each scene's start
  marker and expected outcomes after its actions, before its end marker. Follow the bounded
  checks in `${CLAUDE_PLUGIN_ROOT}/references/readiness.md`. On failure or ambiguity, stop capture, keep diagnostics,
  and report the condition. Do not compose or deliver the failed take.
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
scene, and every declared prerequisite and expected outcome passed for this take.

## 6. Compose

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/scripts/compose.py demos/<slug>/demo.json [--no-burn-subs]
```

It prints one line per scene with its fit (hold, speed-up or pad) and lengths, an `at`
line per synced action with its start time, then a `total` line, and writes `<slug>.mp4`,
`<slug>.srt` and `transcript.md` (output format in
`${CLAUDE_PLUGIN_ROOT}/references/verify.md`). The subtitles are burned into the video too, so the words show
wherever it plays; pass `--no-burn-subs` when **Settings** resolved to a clean video with
the `.srt` alongside. If the spec has an `avatar`, compose also draws the presenter bubble
in its corner and prints an `avatar` line.

## 7. Verify

Confirm this take passed its scene checks in `readiness.md`. Check the result before
handing it over; a fitting bug or a missing overlay is invisible in the compose output. Use the recipes in `${CLAUDE_PLUGIN_ROOT}/references/verify.md`:

- **Length:** ffprobe's duration of `<slug>.mp4` must match compose's `total` line, not the
  sum of the narration lengths, because padded scenes run longer than their narration.
- **Frames:** a contact sheet per scene must show the scene's page, the cursor on each
  pointed target, a ripple per click, a keycap per key press, a badge per Back, and the
  burned-in subtitle. Effects are short (a ripple lasts about 0.9 s), so tile every frame
  of the moment before calling one missing.
- **Sync:** 0.3 s after the time on each `at` line of the compose output, its action must
  be under way, not already over.

Done when the length matches, every scene's expected overlay effect has been seen, and
the actions keep pace with the voice.

## Re-running

- **Compose only** (different subtitle or fitting options): run step 6 again. It reads the
  existing recording, timeline and audio.
- **Narration edit:** edit `narration` in `demo.json`, then run step 4 (including its
  pace and audio check) and step 6. This works as long as the visuals still show what the new
  words describe and every `at` phrase is still in the narration; otherwise repeat
  readiness from step 3 and record again from step 5.
- **Mispronounced word:** add `voice.pronounce`, rerun TTS and compose.

In every case, finish with step 7 and meet its Done-when before handing the new video over:
new audio or new fitting can break length, overlay effects or sync just like a new take.

## Settings

Run once per demo, before writing the spec:

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/scripts/own_voice.py --status
```

It prints `ready` (own voice registered), `photo` (presenter photo saved) and `defaults`
(saved by `${CLAUDE_PLUGIN_ROOT}/references/configure.md`). For each setting, the user's request wins, then
`defaults`, then the fallback:

| Setting | `defaults` field | Fallback |
|---------|------------------|----------|
| Voice | `voice` (copy into the spec) | `ready: true` → `{"backend": "qwen3", "profile": "own_voice"}`; otherwise the **missing own voice** gate |
| Presenter | `avatar` (`false` = none; else copy into the spec) | `photo: true` → `{"profile": "own_photo"}`; otherwise none |
| Video size | `viewport` (copy into the spec) | 1920×1080 |
| Subtitles | `burn_subs` → `--burn-subs` / `--no-burn-subs` on preflight and compose | burned in |

Place a presenter without a saved `corner` in the corner the scenes' actions don't use
(keycaps take bottom-right, the Back badge top-left).

**Missing own voice** (no saved `voice`, `ready: false`): ask one AskUserQuestion with two
options, "Record my voice now (Recommended)" and "Use the free Kokoro voice". On Kokoro,
save the choice so later demos skip the question:
`own_voice.py --save-defaults '{"voice": {"backend": "kokoro"}}'`. On record, run the
recorder below.

**Recorder**: `uv run --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/scripts/own_voice.py`
(`--replace` to record over a registered voice). It opens a local recording page and waits.
Tell the user to click Record, read the passage, pause a moment, stop, check the transcript
matches word-for-word, and click Save my voice. A recording that ends mid-speech is
rejected, because Qwen would speak the missing words at the start of every scene. The user
controls microphone permission and recording; never press Record on their behalf. The
command registers the validated audio and transcript in `~/.config/product-demo/config.json`
and exits after save. Use `--no-open` and share the printed URL when opening a browser is
unavailable. Done when `--status` reports `ready: true`; recheck it after a cancelled or
failed recording.

A registered voice is reused silently on every demo: no reuse, re-record or voice-approval
question (the script approval still applies). Preserve explicit per-demo model or language
preferences; explicit per-demo reference recordings need no registered profile. When the
user gives a photo to keep, save it with `own_voice.py --photo <path>`; it replaces any
earlier one. See `${CLAUDE_PLUGIN_ROOT}/references/demo-spec.md#local-voice-cloning`.
