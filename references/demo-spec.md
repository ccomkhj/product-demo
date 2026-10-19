# Demo spec (`demos/<slug>/demo.json`)

The demo spec is the single source of truth for a demo: `tts.py` reads it to make the
narration, the agent follows it while recording, and `compose.py` reads it to cut and fit
the video. `scripts/demo_spec.py` validates it (both CLIs run that validation first), so a
malformed spec fails before any audio or recording is made.

## Top level

| Field | Required | Meaning |
|-------|----------|---------|
| `title` | no | Demo title. Heads `transcript.md`. |
| `product` | no | Product name, for writing the narration. |
| `audience` | no | Who the video is for, for pitching the narration. |
| `viewport` | no | `{ "width", "height" }` of the **output video**. Default `1920×1080`. |
| `voice` | no | `{ "backend", "voice" }`. `backend` is `say`, `openai` or `elevenlabs`; leave it out to auto-detect (`ELEVENLABS_API_KEY`, then `OPENAI_API_KEY`, then `say`). `voice` is a `say -v` name, an OpenAI voice name, or an ElevenLabs voice **ID**. |
| `scenes` | yes | Non-empty, ordered list of scenes. The video follows this order. |

## Scene

| Field | Required | Meaning |
|-------|----------|---------|
| `id` | yes | Unique slug. Names the audio file (`audio/<id>.wav`) and the timeline markers. |
| `title` | no | Shown as the scene heading in `transcript.md` (falls back to `id`). |
| `url` | yes | The page the scene starts on. Navigate there before marking the scene start, unless the browser is already on it (e.g. the previous scene's click led here). |
| `login` | no | `true` if the page needs a signed-in session. The user signs in by hand in the MCP browser before recording; the profile persists. |
| `actions` | no | Ordered list of actions (below). An empty list is a scene that just shows the page. |
| `narration` | yes | What the voice says, in plain sentences. Its spoken length sets the scene's length in the final video, and each sentence becomes one subtitle cue. |

## Actions

Every action has a `type`. Targets are natural-language descriptions of an element, never
selectors or uids: resolve them against a fresh `take_snapshot` at record time. How to
perform each one is in `mcp-recipes.md`.

| `type` | Fields | Effect on screen |
|--------|--------|------------------|
| `point` | `target` | Cursor glides to the element. |
| `click` | `target` | Cursor glides there, then a ripple as it clicks. |
| `dblclick` | `target` | Cursor glides there, then a double ring. |
| `key` | `key` (Playwright name: `Enter`, `Space`, `Meta+K`) | Keycap badge (`Space`, `⌘ K`). |
| `type` | `target`, `text`, optional `submit` (a key such as `Enter`) | Clicks the field, then one keycap with the typed text. |
| `scroll` | `target` to scroll to, or `by` (pixels, negative is up) | Smooth scroll. |
| `back` | none | `◀ Back` badge, then the previous page. |
| `wait` | `ms`, or `text` to wait for | Nothing; holds the page. |

## Worked example

Three scenes on public pages: a pointer on example.com, a link click and Back, then a Space
press on a page long enough to scroll.

```json
{
  "title": "A tour of the example domains",
  "product": "example.com",
  "audience": "developers new to reserved domains",
  "viewport": { "width": 1920, "height": 1080 },
  "voice": { "backend": "say", "voice": "Samantha" },
  "scenes": [
    {
      "id": "home",
      "title": "The example domain",
      "url": "https://example.com",
      "actions": [
        { "type": "point", "target": "the paragraph explaining what the domain is for" }
      ],
      "narration": "This is example dot com. It exists only so documentation has a safe address to point at."
    },
    {
      "id": "learn-more",
      "title": "Learn more, then back",
      "url": "https://example.com",
      "actions": [
        { "type": "click", "target": "the Learn more link" },
        { "type": "point", "target": "the Example Domains page heading" },
        { "type": "back" }
      ],
      "narration": "The Learn more link explains who reserves these names. Then we head back to where we started."
    },
    {
      "id": "reserved",
      "title": "Reserved domains",
      "url": "https://www.iana.org/domains/reserved",
      "actions": [
        { "type": "point", "target": "the IANA-managed Reserved Domains heading" },
        { "type": "key", "key": "Space" },
        { "type": "wait", "ms": 1000 }
      ],
      "narration": "IANA keeps the full list of reserved names. Pressing space scrolls down to the rest of it."
    }
  ]
}
```
