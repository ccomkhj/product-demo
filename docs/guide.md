# product-demo guide

Details behind the [README](../README.md).

## Install

From [skills.sh](https://skills.sh):

```
npx skills add ccomkhj/product-demo
claude mcp add chrome-devtools -- npx -y chrome-devtools-mcp@latest
```

The chrome-devtools MCP server needs the Node version in `chrome-devtools-mcp`'s `engines`
(`npm view chrome-devtools-mcp engines`, currently `^20.19 || ^22.12 || >=23`); older Node
fails with `CONNECTION_CLOSED`.

Or clone it directly:

```
git clone https://github.com/ccomkhj/product-demo ~/.claude/skills/product-demo
```

Needs Python 3.11+ (run the scripts with `uv run python`; stock macOS `python3` is 3.9),
`ffmpeg` and [`uv`](https://docs.astral.sh/uv/). uv runs the free Kokoro voice, which
downloads once (~340 MB). With your own Python 3.11+ but no uv, narration falls back to
macOS `say`.

Demos are written to `demos/<slug>/` in the project you run it from.

## Use it

```
/product-demo We shipped CSV export on the Reports page. Show a customer how to export last month's report.
```

1. **It asks only what's missing**: the audience, viewer outcome, pages, and whether you need to sign in. Voice, presenter, video size and subtitles come from your saved settings, with no questions.
2. **You approve the script** before anything is recorded.
3. **It checks recording readiness**: local tools, browser access and scene conditions, then checks measured narration against your duration target.
4. **You get `<slug>.mp4`, `<slug>.srt` and `transcript.md`** in `demos/<slug>/`, ready for the release notes. `compose.py --out <path>.mp4` writes the video and `.srt` elsewhere.

### Configure

```
/product-demo --configure
```

Asks every setting (voice, own-voice recording, speed, presenter, video size, subtitles,
and purpose and length if you describe a demo too) and saves the answers as the defaults
for later `/product-demo` runs. Run it again to change them. The defaults stay in the
per-user `~/.config/product-demo/config.json` (under `$XDG_CONFIG_HOME` when set), so they survive plugin updates.

### Purposes

```text
/product-demo Make a 30-second feature announcement for CSV export on our Reports page.
/product-demo Make a customer tutorial showing how to export last month's report.
/product-demo Make a sales walkthrough of reporting for a finance team.
```

Suggested targets are 30, 90, and 120 seconds respectively. Your structure and duration
always take precedence. Custom walkthroughs remain supported. The skill asks for missing
URLs and context, then uses the selected [writing template](../references/templates/) to
draft the script; it does not invent product behavior or sales claims.

## Preflight and readiness

Run local checks separately from the skill directory:

```bash
uv run python scripts/preflight.py /path/to/demos/export/demo.json
uv run python scripts/preflight.py /path/to/demos/export/demo.json --json
uv run python scripts/preflight.py /path/to/demos/export/demo.json --durations /path/to/demos/export/audio/durations.json
```

Use `--backend` to override voice selection and `--burn-subs` / `--no-burn-subs` to choose
subtitle policy, passing the same choices to TTS/compose. Exit 0 means no **local** blockers,
1 means blockers, and 2 means a usage error. Warnings can remain on success. Preflight does
not contact voice services, download models, or verify browser access/login.

Optional scene `prerequisites` and `expected_outcomes` describe visible text or targets.
The agent checks them in the recording browser, stops on a failed condition, and records
brief evidence in `demos/<slug>/readiness.md`. These browser checks are agent instructions,
not a standalone Python browser runner. Narration more than 10% over a target produces a
warning; the final video may be longer because of padding. See the
[spec](../references/demo-spec.md) and [readiness workflow](../references/readiness.md).

## Voices

Set the voice in the demo spec, for example `"voice": { "backend": "kokoro", "voice": "am_michael" }`.
The default is `af_heart`. Other options are `af_bella`, `bf_emma`, `bm_george` and
[more](../references/demo-spec.md). To use paid voices instead, set `OPENAI_API_KEY` or
`ELEVENLABS_API_KEY`.

### Your own voice, locally

On an Apple Silicon Mac, select `"backend": "qwen3"` in the demo's `voice` configuration
and provide `reference_audio` plus its exact `reference_text`. The default model is
Qwen3-TTS 0.6B Base, run locally through MLX Audio with one model load per demo.
Model and language are configurable; see [the configuration example](../references/demo-spec.md#local-voice-cloning).
Initial setup downloads dependencies and weights; cached generation works offline.
Kokoro remains the default when no explicit backend or API key is configured.

To set up your reusable voice, run `uv run python scripts/own_voice.py`. If `own_voice` is
already registered and valid, it returns immediately; add `--replace` to record a new one.
Otherwise a local browser page lets you record and save it. Future demos can use
`"voice": {"backend": "qwen3", "profile": "own_voice"}` without asking again.
The recording and registry stay in `~/.config/product-demo/` (under `$XDG_CONFIG_HOME` when set); an old `.voices/` registry is copied there once.

## Presenter bubble

Add `"avatar": { "image": "me.png" }` to the demo spec to get a round photo in the
bottom-left corner, like a Loom camera bubble. Its halo glows while the voice speaks. There
is no lip-sync and no extra download. You can also set `"corner"` (for example
`"bottom-right"`) and `"size"` (the photo's diameter as a share of the video height,
default `0.2`). To reuse one photo in every demo, save it once with
`uv run python scripts/own_voice.py --photo me.png` and write `"avatar": { "profile": "own_photo" }`.
See [`references/demo-spec.md`](../references/demo-spec.md).

## How it works

- **Narration first.** The voice is generated before recording, so each scene's length is
  known in advance.
- **Record, then edit.** The raw recording is mostly frozen frames between agent steps.
  Compose drops them and keeps only the motion.
- **Actions on cue.** An action can name an `at` phrase from the narration. Compose holds
  the screen until the voice reaches that phrase.
- **Exact alignment.** A magenta flash at the start of the recording lines the timeline up
  with the video.
- **Fitting.** If a scene's motion is short, compose holds the last frame. If it runs long,
  compose speeds it up (up to 1.5×), then pads the audio.

Design decisions are in [`docs/adr/`](adr/). If your ffmpeg lacks libass, subtitles
are burned in with `rsvg-convert` (`brew install librsvg`).

## Layout

| Path | What it is |
|------|------------|
| `SKILL.md` | The skill's instructions: interview, approval, narration, recording, compose and verify |
| `references/` | Demo spec format, browser recording recipes and verification recipes |
| `references/templates/` | Announcement, tutorial and sales script guidance |
| `scripts/preflight.py` | Local readiness and measured narration duration checks |
| `scripts/tts.py` | Narration audio (Kokoro, `say`, OpenAI or ElevenLabs) |
| `scripts/kokoro_tts.py` | Local Kokoro synthesis, run by `tts.py` through `uv run` |
| `scripts/check_audio.py` | Transcribes `qwen3` narration with local whisper to catch leaked or misspoken words |
| `scripts/mark.py` | Timeline markers written while recording |
| `scripts/compose.py` | Cuts, syncs and fits the recording; writes the video, subtitles and transcript |
| `scripts/avatar.py` | The presenter bubble: a round photo whose halo glows with the voice |
| `assets/overlay.js` | The visible cursor, click ripples, keycaps, badges and sync flash |
| `docs/sample/` | The README's sample demo (spec, video, subtitles), also the reference end-to-end test |
| `tests/` | `uv run pytest` (the overlay tests need node and Chrome) |
