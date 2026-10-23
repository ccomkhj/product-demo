# product-demo

**Ship the release. Skip the screen recording.**

A [Claude Code](https://claude.com/claude-code) skill that turns a sentence about what you
shipped into a **narrated, subtitled demo video**. It drives a real Chrome browser, voices
the script, puts every click on the word that names it, and cuts the dead time.
**An afternoon of recording and editing becomes two messages: the request and your
approval.**

https://github.com/user-attachments/assets/6b411193-4300-417b-8ec8-564527144894

*54 s, with a presenter bubble in the corner, made from this one line:*

```
/product-demo record a sample video for https://www.korea.net -> https://www.korea.net/AboutKorea/OverviewofKorea (explain only 3 bullet points about Korea) -> https://www.korea.net/Government/Administration/President-Lee-Jae-Myung (just tell who the president is and when he was elected). Use the presenter avatar assets/avatars/huijo.png.
```

## Why

- **No more lost afternoons.** Writing a script, recording and re-recording, voicing,
  syncing and subtitling are all done for you.
- **A fresh demo for every release.** Describe what changed and get a new video. Nothing
  needs to be re-shot by hand.
- **Free.** The voice is [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M), which
  runs offline on your machine. No API key, no per-minute bill.
- **Looks produced, not captured.** You see the cursor, click ripples and keycaps. Each
  action lands as the voice names it, and the subtitles are burned in. Add your photo and
  it sits in the corner as the presenter.

## Use it

```
/product-demo We shipped CSV export on the Reports page. Show a customer how to export last month's report.
```

1. **It asks only what's missing**: the audience, viewer outcome, pages, and whether you need to sign in.
2. **You approve the script** before anything is recorded.
3. **It checks recording readiness**: local tools, browser access and scene conditions, then checks measured narration against your duration target.
4. **You get `final.mp4`, `final.srt` and `transcript.md`**, ready for the release notes.

Choose a purpose in your request:

```text
/product-demo Make a 30-second feature announcement for CSV export on our Reports page.
/product-demo Make a customer tutorial showing how to export last month's report.
/product-demo Make a sales walkthrough of reporting for a finance team.
```

Suggested targets are 30, 90, and 120 seconds respectively. Your structure and duration
always take precedence. Custom walkthroughs remain supported. The skill asks for missing
URLs and context, then uses the selected [writing template](references/templates/) to draft
the script; it does not invent product behavior or sales claims.

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
warning; the final video may be longer because of padding. See the [spec](references/demo-spec.md)
and [readiness workflow](references/readiness.md).

## Install

```
git clone https://github.com/ccomkhj/product-demo ~/.claude/skills/product-demo
claude mcp add chrome-devtools -- npx -y chrome-devtools-mcp@latest
```

Needs Python 3.11+ (run the scripts with `uv run python`; stock macOS `python3` is 3.9), `ffmpeg` and [`uv`](https://docs.astral.sh/uv/). uv runs the free
Kokoro voice, which downloads once (~340 MB). With your own Python 3.11+ but no uv, narration falls back to macOS `say`.

Demos are written to `demos/<slug>/` in the project you run it from.

<details>
<summary><b>Voices</b></summary>

Set the voice in the demo spec, for example `"voice": { "backend": "kokoro", "voice": "am_michael" }`.
The default is `af_heart`. Other options are `af_bella`, `bf_emma`, `bm_george` and
[more](references/demo-spec.md). To use paid voices instead, set `OPENAI_API_KEY` or
`ELEVENLABS_API_KEY`.

</details>

<details>
<summary><b>Presenter bubble</b></summary>

Add `"avatar": { "image": "me.png" }` to the demo spec to get a round photo in the
bottom-left corner, like a Loom camera bubble. Its halo glows while the voice speaks. There
is no lip-sync and no extra download. You can also set `"corner"` (for example
`"bottom-right"`) and `"size"` (the photo's diameter as a share of the video height,
default `0.2`). To reuse one photo in every demo, save it once with
`uv run python scripts/own_voice.py --photo me.png` and write `"avatar": { "profile": "own_photo" }`.
See [`references/demo-spec.md`](references/demo-spec.md).

</details>

<details>
<summary><b>How it works</b></summary>

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

Design decisions are in [`docs/adr/`](docs/adr/). If your ffmpeg lacks libass, subtitles
are burned in with `rsvg-convert` (`brew install librsvg`).

</details>

<details>
<summary><b>Layout</b></summary>

| Path | What it is |
|------|------------|
| `SKILL.md` | The skill's instructions: interview, approval, narration, recording, compose and verify |
| `references/` | Demo spec format, browser recording recipes and verification recipes |
| `scripts/preflight.py` | Local readiness and measured narration duration checks |
| `references/templates/` | Announcement, tutorial and sales script guidance |
| `scripts/tts.py` | Narration audio (Kokoro, `say`, OpenAI or ElevenLabs) |
| `scripts/kokoro_tts.py` | Local Kokoro synthesis, run by `tts.py` through `uv run` |
| `scripts/mark.py` | Timeline markers written while recording |
| `scripts/compose.py` | Cuts, syncs and fits the recording; writes the video, subtitles and transcript |
| `scripts/avatar.py` | The presenter bubble: a round photo whose halo glows with the voice |
| `assets/overlay.js` | The visible cursor, click ripples, keycaps, badges and sync flash |
| `docs/sample/` | The README's sample demo (spec, video, subtitles), also the reference end-to-end test |
| `tests/` | `uv run pytest` (the overlay tests need node and Chrome) |

</details>

## License

[Apache-2.0](LICENSE). You may use, change, republish and sell it; copies and derivative
works must keep the [`NOTICE`](NOTICE) file, which credits this repository.

### Narrate in your own voice locally

On an Apple Silicon Mac, select `"backend": "qwen3"` in the demo's `voice` configuration
and provide `reference_audio` plus its exact `reference_text`. The default model is
Qwen3-TTS 0.6B Base, run locally through MLX Audio with one model load per demo.
Model and language are configurable; see [the configuration example](references/demo-spec.md#local-voice-cloning).
Initial setup downloads dependencies and weights; cached generation works offline.
Kokoro remains the default when no explicit backend or API key is configured.

To set up your reusable voice, run `uv run python scripts/own_voice.py`. If `own_voice` is
already registered and valid, it returns immediately; add `--replace` to record a new one.
Otherwise a local browser page lets you record and save it. Future demos can use
`"voice": {"backend": "qwen3", "profile": "own_voice"}` without asking again.
The recording and registry stay in the ignored `.voices/` directory.
