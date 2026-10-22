# Recording readiness

Readiness has three separate parts: local tools, the live browser entry state, and the
result of each recorded scene. The Python command covers only the first part. Browser
checks are agent instructions using the existing Chrome MCP tools, not Python automation.

## Local checks

After script approval and before TTS:

```bash
python3 <skill dir>/scripts/preflight.py demos/<slug>/demo.json
python3 <skill dir>/scripts/preflight.py demos/<slug>/demo.json --json
```

Use `--backend say|kokoro|openai|elevenlabs|qwen3` when overriding the spec, and carry that choice
to TTS. Use `--burn-subs` or `--no-burn-subs` when explicitly selecting a subtitle policy,
and carry it to compose. Defaults match the existing scripts.

The command checks the spec, ffmpeg/ffprobe, writable output directory, chosen voice
backend configuration, and subtitle renderer. Probes time out after 10 seconds. It never
installs dependencies, synthesizes speech, downloads models, or contacts voice services.
A configured API key does not prove remote validity or available credit. Kokoro dependency,
model and voice availability are ultimately verified by synthesis. Missing subtitle support
warns by default (SRT remains available), blocks explicit `--burn-subs`, and does not block
`--no-burn-subs`.

Exit codes: **0** no local blockers (warnings may remain), **1** blockers, **2** CLI usage
error. JSON contains `schema_version`, `scope: "local"`, `ready`, `selected_backend`, and
`checks` with `id`, `status`, `message`, and `remedy`. Local `ready` never certifies a browser.

After TTS, run again with the same options and measured durations:

```bash
python3 <skill dir>/scripts/preflight.py demos/<slug>/demo.json --durations demos/<slug>/audio/durations.json
```

Every current scene needs a positive finite duration; extra scene entries are ignored.
Narration exceeding `target_duration_s` by more than 10% warns without blocking. No target
means report the measurement without inventing one. The final video can be longer because
compose pads scenes. Propose a shorter script when needed; get approval for changed wording
and regenerate TTS. Do not silently cut text or accelerate speech.

## Browser entry checks

1. Discover the Chrome MCP tools in `mcp-recipes.md`. Confirm navigation, snapshots, input,
   evaluation, resizing, and screencast start/stop are available. Tool availability is not
   proof that recording will succeed. Missing tools block the browser workflow.
2. Use `list_pages` to select the single recording tab. Check independently accessible
   scene entry pages with navigation and fresh snapshots. Do not submit forms or rehearse
   actions that change state. A browser error page, login redirect, or ambiguous page does
   not count as successful access.
3. For `login: true`, verify the existing session using the page's intended content and
   declared prerequisites. If signed out, ask for manual login, wait for confirmation,
   then verify again. Never collect credentials or record the login process.
4. Defer scene checks that depend on an earlier action or transient state. Mark them
   **deferred**, not passed. Check them when the scene is reached naturally during the take.
   If dependencies are unclear, resolve the starting-state question before recording.
5. Use the resize and actual `innerWidth`/`innerHeight` recipe in `mcp-recipes.md`. Preserve
   the requested aspect ratio and restore the first scene's entry state before capture.
   If inspection altered history needed by a Back action, reestablish that entry history
   before starting; do not assume inspecting later pages was harmless.

## Scene conditions

Scene `prerequisites` run immediately before the start marker, and `expected_outcomes`
run after the last action but before the end marker. Missing/empty lists mean “not
declared”; they do not certify a business result. Check every declared condition.

- **`visible_text`**: confirm its text is actually visible on the current page. Use a fresh
  snapshot and, if visibility is uncertain, inspect a screenshot or element visibility.
  A string in hidden DOM content does not pass. For delayed content use `wait_for` followed
  by fresh evidence.
- **`visible_target`**: resolve the natural-language target from a fresh snapshot. Confirm
  visibility, uniqueness and any stated enabled/disabled state. Never reuse an old UID.
  An ambiguous match is unresolved, not passed.

Use a **10-second total observation budget per condition**, including retries. Tool-call
latency can add overhead; do not reset the budget on each snapshot. A missing condition
at the deadline is failed. Do not click or type to make a prerequisite pass. Record
intermediate states as separate scenes when they need their own outcomes.

If a condition fails or remains unresolved during recording, stop the screencast, retain
raw video/timeline and brief evidence, and report the scene and condition. Do not mark that
scene complete, compose the take, or deliver an old final video as its output. If stopping
fails, report that capture may still be active and retry stop in the same session. After
correction, preserve failed artifacts before a full re-record; scene-only retry is not
implemented. Recheck prerequisites for the new take.

## Per-demo evidence

Write `demos/<slug>/readiness.md` for each run, identifying the take by its `rec_start`
marker once recording begins. Refresh results rather than trusting an old report. Record
short observations, not credentials, cookies, full snapshots, or customer data.

```markdown
# Readiness — <demo slug>

Run: <timestamp>; take: <rec_start t_ms, or not started>
Local checks: <ready/blocked, selected backend, warnings and remedies>
Narration: <measured seconds, target if any, accepted overrun if applicable>
Browser: <selected page, entry access, auth evidence, actual viewport>

| Condition | Status | Evidence |
|-----------|--------|----------|
| export.prerequisites.1 | pass | Reports heading visible |
| export.prerequisites.2 | pass | One visible enabled Export button |
| export.expected_outcomes.1 | deferred | Evaluate after action |

Capture: <not started/active/stopped/stop failed>
Delivery: <blocked until all declared checks pass / ready for visual verification>
```

Statuses are `pass`, `fail`, `unresolved`, `deferred`, or `not declared`. Convert deferred
conditions to actual results when reached. Readiness complements the frame, duration and
sync checks in `verify.md`; it does not replace them.

## Manual acceptance walkthrough

Use `tests/fixtures/readiness.html` as a local page. Copy it to a temporary directory and
serve that directory on localhost, for example `python3 -m http.server 8765 --bind 127.0.0.1`.
In the MCP browser, use these cases (query parameters select fixture behavior):

| URL suffix | Expected observation |
|------------|----------------------|
| `/readiness.html` | Reports and enabled Export pass prerequisites; clicking Export reveals Export ready. |
| `/readiness.html?missing=1` | Export prerequisite fails before that scene starts. |
| `/readiness.html?disabled_result=1` | Click does not reveal Export ready; outcome times out, capture stops, delivery is blocked. |
| `/readiness.html?delay=500` | Export ready appears within the observation budget. |
| `/readiness.html?delay=12000` | Outcome exceeds the observation budget and fails. |

For a state-dependent second scene, use the “Export details” region revealed only after
Export. Defer its prerequisite initially, then verify on natural entry without navigating
away. For authentication, use a user-provided test app with an expired session: confirm the
manual sign-in handoff and rerun checks after login. The fixture does not model real auth.

Generate a short announcement from the successful fixture workflow, then verify its MP4,
SRT and transcript with `verify.md`. Record actual observations and environment blockers;
do not label the walkthrough passed solely because unit tests passed.
