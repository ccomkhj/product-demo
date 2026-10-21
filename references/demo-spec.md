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
| `purpose` | no | `feature-announcement`, `customer-tutorial`, `sales-walkthrough`, or `custom`. Omitted means custom behavior. |
| `outcome` | no | Non-empty description of what the viewer should understand or accomplish. |
| `target_duration_s` | no | Finite positive number (not a boolean). Editorial target, not a rendering limit. No automatic default for existing specs. |
| `viewport` | no | `{ "width", "height" }` of the **output video**. Default `1920×1080`. |
| `voice` | no | `{ "backend", "voice" }`. `backend` is `say`, `kokoro`, `openai` or `elevenlabs`; leave it out to auto-detect (`ELEVENLABS_API_KEY`, then `OPENAI_API_KEY`, then `kokoro` if `uv` is on PATH, then `say`). `voice` is a `say -v` name, a Kokoro voice (default `af_heart`; others include `af_bella`, `am_michael`, `bf_emma` and `bm_george`), an OpenAI voice name, or an ElevenLabs voice **ID**. |
| `avatar` | no | `{ "image", "corner", "size" }`: a presenter bubble, a round photo whose halo glows while the voice speaks (no lip-sync). `image` is a photo path, relative to the demo folder or absolute; any shape works, as it is cropped to a centred circle. `corner` is `bottom-left` (default), `bottom-right`, `top-left` or `top-right`. `size` is the photo's diameter as a share of the video height, from `0.05` to `0.5` (default `0.2`). Subtitles wrap clear of a bottom-corner bubble. Pick the corner the scenes' actions don't use, because the bubble covers the page there. |
| `scenes` | yes | Non-empty, ordered list of scenes. The video follows this order. |

## Scene

| Field | Required | Meaning |
|-------|----------|---------|
| `id` | yes | Unique slug. Names the audio file (`audio/<id>.wav`) and the timeline markers. |
| `title` | no | Shown as the scene heading in `transcript.md` (falls back to `id`). |
| `url` | yes | The page the scene starts on. Navigate there before marking the scene start, unless the browser is already on it (e.g. the previous scene's click led here). |
| `login` | no | `true` if the page needs a signed-in session. The user signs in by hand in the MCP browser before recording; the profile persists. |
| `actions` | no | Ordered list of actions (below). An empty list is a scene that just shows the page. |
| `prerequisites` | no | List of visible conditions to check immediately before the scene start marker. |
| `expected_outcomes` | no | List of visible conditions to check after the actions, before the scene end marker. |
| `narration` | yes | What the voice says, in plain sentences. Its spoken length sets the scene's length in the final video, and each sentence becomes one subtitle cue. |

## Purpose and readiness

Templates in `templates/` guide script writing: announcement (30 s), tutorial (90 s),
and sales walkthrough (120 s). The agent copies a suggested target only when the user
has not supplied one; explicit user duration/structure takes precedence. Custom demos
require no template or duration target. Approval still covers the actual narration.

Each scene condition is an object with `type: "visible_text"` and a non-empty `text`, or
`type: "visible_target"` and a non-empty natural-language `target`. Empty condition lists
are allowed. Unknown types and missing/empty payloads are rejected. These checks run in
the agent's browser workflow, not in the local preflight command. Ambiguous targets do
not pass; resolve against a fresh snapshot and verify visibility and any stated enabled
state. UIDs are never persisted. See `readiness.md` for bounded waits and failure handling.
Intermediate outcomes should be represented as separate scenes. Loaded pages alone do
not prove a valid login; the existing `login` hint still applies.

After narration, `preflight.py --durations <audio/durations.json>` warns when measured
narration exceeds the target by more than 10%. Composition may make the final video
longer. The target never changes fitting or truncates speech.

## Actions

Every action has a `type`. Targets are natural-language descriptions of an element, never
selectors or uids: resolve them against a fresh `take_snapshot` at record time. How to
perform each one is in `mcp-recipes.md`.

Any action may also have `at`: a phrase copied from the scene's narration (any case). The
action then starts when the voice reaches that phrase, and the screen holds still until
it does; without `at`, an action follows the previous one straight away. Give an `at` to
every action the narration talks about, so the cursor arrives as the words do. Phrases
must appear in the narration in the same order as their actions.

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
press on a page long enough to scroll. The `at` phrases start the later actions as the
voice mentions them.

```json
{
  "title": "A tour of the example domains",
  "product": "example.com",
  "audience": "developers new to reserved domains",
  "purpose": "customer-tutorial",
  "outcome": "Understand where to find information about reserved example domains",
  "target_duration_s": 90,
  "viewport": { "width": 1920, "height": 1080 },
  "voice": { "backend": "kokoro", "voice": "af_heart" },
  "scenes": [
    {
      "id": "home",
      "title": "The example domain",
      "url": "https://example.com",
      "prerequisites": [{ "type": "visible_text", "text": "Example Domain" }],
      "expected_outcomes": [],
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
        { "type": "point", "target": "the Example Domains page heading", "at": "who reserves" },
        { "type": "back", "at": "head back" }
      ],
      "narration": "The Learn more link explains who reserves these names. Then we head back to where we started."
    },
    {
      "id": "reserved",
      "title": "Reserved domains",
      "url": "https://www.iana.org/domains/reserved",
      "prerequisites": [{ "type": "visible_target", "target": "the IANA-managed Reserved Domains heading" }],
      "actions": [
        { "type": "point", "target": "the IANA-managed Reserved Domains heading" },
        { "type": "key", "key": "Space", "at": "Pressing space" },
        { "type": "wait", "ms": 1000 }
      ],
      "narration": "IANA keeps the full list of reserved names. Pressing space scrolls down to the rest of it."
    }
  ]
}
```
