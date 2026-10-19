# product-demo

**Ship the release. Skip the screen recording.**

A [Claude Code](https://claude.com/claude-code) skill that turns a sentence about what you
shipped into a **narrated, subtitled demo video**. It drives a real Chrome browser, voices
the script, puts every click on the word that names it, and cuts the dead time.
**An afternoon of recording and editing becomes two messages: the request and your
approval.**

https://github.com/user-attachments/assets/0708be78-771b-4499-8c81-c5d854d2a679

*53 s, made from this one line:*

```
/product-demo record a sample video for https://www.wikipedia.org -> https://www.wikidata.org/wiki/Wikidata:Main_Page -> https://www.wikidata.org/wiki/Q136027913
```

## Why

- **No more lost afternoons.** Writing a script, recording and re-recording, voicing,
  syncing and subtitling are all done for you.
- **A fresh demo for every release.** Describe what changed and get a new video. Nothing
  needs to be re-shot by hand.
- **Free.** The voice is [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M), which
  runs offline on your machine. No API key, no per-minute bill.
- **Looks produced, not captured.** You see the cursor, click ripples and keycaps. Each
  action lands as the voice names it, and the subtitles are burned in.

## Use it

```
/product-demo We shipped CSV export on the Reports page. Show a customer how to export last month's report.
```

1. **It asks only what's missing**: the audience, the pages, and whether you need to sign in.
2. **You approve the script** before anything is recorded.
3. **You get `final.mp4`, `final.srt` and `transcript.md`**, ready for the release notes.

## Install

```
git clone https://github.com/ccomkhj/product-demo ~/.claude/skills/product-demo
claude mcp add chrome-devtools -- npx -y chrome-devtools-mcp@latest
```

Needs `python3` (3.11+), `ffmpeg` and [`uv`](https://docs.astral.sh/uv/). uv runs the free
Kokoro voice, which downloads once (~340 MB). Without uv, narration falls back to macOS `say`.

Demos are written to `demos/<slug>/` in the project you run it from.

<details>
<summary><b>Voices</b></summary>

Set the voice in the demo spec, for example `"voice": { "backend": "kokoro", "voice": "am_michael" }`.
The default is `af_heart`. Other options are `af_bella`, `bf_emma`, `bm_george` and
[more](references/demo-spec.md). To use paid voices instead, set `OPENAI_API_KEY` or
`ELEVENLABS_API_KEY`.

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
| `scripts/tts.py` | Narration audio (Kokoro, `say`, OpenAI or ElevenLabs) |
| `scripts/kokoro_tts.py` | Local Kokoro synthesis, run by `tts.py` through `uv run` |
| `scripts/mark.py` | Timeline markers written while recording |
| `scripts/compose.py` | Cuts, syncs and fits the recording; writes the video, subtitles and transcript |
| `assets/overlay.js` | The visible cursor, click ripples, keycaps, badges and sync flash |
| `tests/` | `uv run pytest` (the overlay tests need node and Chrome) |

</details>
