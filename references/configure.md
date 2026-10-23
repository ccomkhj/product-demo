# Configure mode (`/product-demo --configure`)

Asks every setting with AskUserQuestion, records the voice if chosen, and saves the
answers as the defaults that default mode reads (see **Settings** in `SKILL.md`).

## 1. Current state

```bash
uv run --project <skill dir> python <skill dir>/scripts/own_voice.py --status
```

Mark the current value of each setting (from `defaults`, else the **Settings** fallback)
by putting its option first with "(current)" in the label.

## 2. Ask

At most four questions per AskUserQuestion call. Ask a follow-up only when its condition
holds. "Other" is always available for a free answer (a path, a voice ID, a size).

**Call 1**

| Question | Options |
|----------|---------|
| Voice | My own voice (local clone, Apple Silicon) · Kokoro (free, local) · OpenAI or ElevenLabs (API key) · macOS `say` |
| Subtitles | Burned into the video · Separate `.srt` only |
| Purpose (only when the request describes a demo) | Feature announcement · Customer tutorial · Sales walkthrough · Custom |
| Length (only when the request describes a demo) | Template suggestion · 30 s · 60 s · 120 s |

**Call 2**

| Question | When | Options |
|----------|------|---------|
| Own voice | own voice and `ready: true` | Keep my recording · Record again |
| Language | own voice | English · Korean · German · Japanese |
| Kokoro voice | Kokoro | `af_heart` · `am_michael` · `bf_emma` · `bm_george` |
| API voice | OpenAI or ElevenLabs | OpenAI `alloy` · OpenAI `nova` · ElevenLabs (voice ID via Other) |
| Speed | always | Normal (1.0) · Slower (0.9) · Faster (1.1) |
| Presenter | always | My saved photo (only with `photo: true`) · A new photo (path via Other) · None |
| Video size | when Call 2 has room, else Call 3 | 1920×1080 · 1280×720 · 1080×1920 (vertical) |

**Call 3** (presenter chosen)

| Question | Options |
|----------|---------|
| Corner | Auto (the corner the actions don't use) · Bottom-left · Bottom-right · Top-right |
| Size | Default (0.2) · Small (0.15) · Large (0.3) |

## 3. Act

- Own voice, not registered or "Record again": run the **Recorder** in `SKILL.md`
  (`--replace` for "Record again"). Done when `--status` reports `ready: true`.
- A new photo: `own_voice.py --photo <path>`.

## 4. Save

```bash
uv run --project <skill dir> python <skill dir>/scripts/own_voice.py --save-defaults '<json>'
```

`<json>` holds `voice`, `avatar`, `viewport` and `burn_subs`, written as spec fields:

```json
{
  "voice": {"backend": "qwen3", "profile": "own_voice", "language": "Korean", "speed": 1.1},
  "avatar": {"profile": "own_photo", "corner": "top-right", "size": 0.15},
  "viewport": {"width": 1920, "height": 1080},
  "burn_subs": true
}
```

Kokoro is `{"backend": "kokoro", "voice": "am_michael"}`; no presenter is `"avatar": false`;
leave out `corner` for Auto. Purpose and length belong to this demo only: they go into its
spec, never into the defaults. Done when the command prints the status with the new
`defaults`.

## 5. Continue

When the request describes a demo, continue at step 1 of `SKILL.md` with these answers
(the interview then asks only for missing pages). Otherwise report the saved settings and
stop.
