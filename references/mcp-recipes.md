# Chrome DevTools MCP recipes

The exact call sequences to use when recording a demo. Follow them literally. Every call
below was run against the Chrome DevTools MCP (`mcp__chrome-devtools__*`). If the tools
are deferred, load them first with
`ToolSearch("select:mcp__chrome-devtools__list_pages,mcp__chrome-devtools__navigate_page,mcp__chrome-devtools__resize_page,mcp__chrome-devtools__evaluate_script,mcp__chrome-devtools__take_snapshot,mcp__chrome-devtools__click,mcp__chrome-devtools__press_key,mcp__chrome-devtools__type_text,mcp__chrome-devtools__wait_for,mcp__chrome-devtools__take_screenshot,mcp__chrome-devtools__screencast_start,mcp__chrome-devtools__screencast_stop")`.

Every tool takes `pageId`. Get it once with `list_pages` (the selected page is marked
`[selected]`) and use the same id for the whole recording.

## Rules

- **One tab.** The screencast records one page. Never call `new_page`. Follow links in the
  same tab, and avoid targets that open a new window.
- **Re-inject the overlay after every navigation**: after every `navigate_page`
  (url, back, forward, reload) and after any `click` whose result says
  `Page navigated to ...`. Injecting again on a page that already has the overlay is safe:
  it returns `"overlay already present"`.
- **Point before every click.** `__demo.point` makes the fake cursor glide to the target,
  so viewers see where the click is going.
- **Badge before Back.** Show `◀ Back` before `navigate_page` with `type: "back"`.
- **Resolve targets at record time.** Element targets in the demo spec are natural-language
  descriptions. Take a fresh `take_snapshot` on each page and pick the uid that matches the
  description. Never reuse uids from an earlier run or an earlier snapshot of a page that
  has since changed: uids are not stable.
- **Mark a beat before every `at` action.** Compose cuts the scene there and holds the
  screen until the narration reaches the phrase, so a missing or extra beat fails compose.
- **Use real input.** Clicks, double-clicks and key presses must go through `click`,
  `press_key` and `type_text`. They send real CDP input events, which the overlay draws on
  its own (ripple, double ring, keycap). Don't use `fill` or `fill_form` on screen: they
  set the value without key events, so nothing is shown.

## Overlay injection

`assets/overlay.js` is a classic script. Inject it with `format: "script"`:

```
evaluate_script({ pageId, sourcePath: "<skill dir>/assets/overlay.js", format: "script" })
```

It returns `"overlay installed"` the first time on a document and
`"overlay already present"` after that.

`sourcePath` only works when the file is inside one of the MCP server's workspace roots
(normally the directory Claude Code was started in). Otherwise the call fails with
`Access denied: path "..." is not within any of the configured workspace roots`. In that
case, read `assets/overlay.js` and pass its full contents inline:

```
evaluate_script({ pageId, format: "script", function: "<entire contents of assets/overlay.js>" })
```

Leaving out `format: "script"` makes the MCP treat the source as a function declaration,
and the injection fails with `Error: fn is not a function`.

After a Back or Forward navigation the browser may restore the previous document from
its back/forward cache, overlay included. Re-injecting then returns
`"overlay already present"`. That is expected.

## Overlay API

Called with `evaluate_script` (default `format: "function"`, so `args` work):

| Call | Effect |
|------|--------|
| `__demo.point(el)` | Glides the cursor to the element's centre (~600 ms). Scrolls it into view first if it is off screen. Resolves when the glide ends. `el` may also be a CSS selector. |
| `__demo.badge(text[, ms])` | Transient badge at the top left (default 1500 ms). Resolves after ~700 ms, so awaiting it before a navigation keeps it on screen long enough to be recorded. |
| `__demo.caption(text)` | Lower-third caption at the bottom left. It stays until replaced. `caption("")` hides it. |

## Readiness before recording

Complete `readiness.md` before synthesis and capture: verify the local preflight, MCP
capabilities, reachable entry pages, authentication and actual viewport. Do not rehearse
state-changing actions. State-dependent pages are checked when naturally reached.
Record brief results in `demos/<slug>/readiness.md`; old results never substitute for
checking the current session. Capability discovery does not prove capture succeeds.

## Recording start and stop

1. `list_pages({})` → note the selected `pageId`.
2. `resize_page({ pageId, width: <viewport.width>, height: <viewport.height> })` (default 1920×1080).
   Then check what you got:
   `evaluate_script({ pageId, function: "() => [innerWidth, innerHeight]" })`.
   The window can't be taller than the screen, so on a laptop a 1920×1080 request can
   come back as 1920×906. The screencast records the real window, so keep the aspect ratio
   instead: `resize_page` again to `round(innerHeight × width / height)` × `innerHeight`
   (1611×906 for 16:9). `compose.py` scales the recording to `viewport`, and on a
   Retina screen the recording is at 2× pixel density, so the output stays sharp. Don't use `emulate`'s
   `viewport` for this: the screencast crops the emulated page to the real window.
3. `navigate_page({ pageId, type: "url", url: "<first scene url>" })`.
4. Inject the overlay (see above).
5. `screencast_start({ pageId, filePath: "<abs path>/demos/<slug>/raw.mp4" })`. The output is
   an `.mp4`. Like `sourcePath`, `filePath` must be inside a workspace root, or the call
   fails with `Access denied`. If it does, call `screencast_start({ pageId })` with no
   `filePath`. The result names the temp file
   (`Screencast recording started. The recording will be saved to /…/screencast.mp4`).
   After stopping, move that file to `demos/<slug>/raw.mp4`.
6. Flash the sync frame: `evaluate_script({ pageId, function: "() => __demo.sync()" })`.
   It fills the page with magenta for 0.4 s and returns the wall-clock milliseconds at
   which the flash was painted, such as `1760900000123`.
7. Mark the recording start with that number. It appends to the timeline; compose uses
   only the events after the last `rec_start`, so a re-recording needs no cleanup:
   `python3 <skill dir>/scripts/mark.py demos/<slug>/timeline.jsonl rec_start --sync-ms <ms>`

   Chrome starts the recording on its own clock and never says when, and the agent takes
   seconds between tool calls, so the time of this mark says nothing about where the video
   begins. Compose finds the flash in `raw.mp4` and lines the timeline up on it. Without the
   flash, every cut lands seconds early, and each action plays at the phrase of the one
   after it.

Then, for each scene in spec order:

1. If the browser isn't on the scene's `url` yet, `navigate_page` there and re-inject the
   overlay. Do this **before** the start marker, so the page load falls in the gap between
   scenes, which compose drops.
2. Evaluate this scene's `prerequisites` using the checks in `readiness.md`, with fresh
   snapshots and at most 10 seconds per condition. On failure or ambiguity, stop the take.
   Only after they pass: `python3 <skill dir>/scripts/mark.py demos/<slug>/timeline.jsonl start <scene-id>`
3. Run the scene's actions with the recipes below. Right before each action that has an
   `at`, mark a beat, after any snapshot that action needs but before its first visible
   step (the `point` of a click):
   `python3 <skill dir>/scripts/mark.py demos/<slug>/timeline.jsonl beat <scene-id>`
4. Don't wait to fill out the narration. Compose drops every frame where nothing moves, so a
   pause on a still page never reaches the video: a scene lasts only as long as its motion
   (glides, scrolls, ripples, page changes). Compose holds the screen until each `at`
   phrase, and holds the last frame for the rest of the narration.
5. Evaluate `expected_outcomes` with fresh evidence before the end marker. If they pass:
   `python3 <skill dir>/scripts/mark.py demos/<slug>/timeline.jsonl end <scene-id>`.
   Record each result in `readiness.md`. With no declared checks, report “not declared,”
   not “verified.”

Each mark call prints the JSON line it wrote, such as
`{"scene": "home", "event": "start", "t_ms": 1760900000000}`.

If any condition fails or remains ambiguous, call `screencast_stop` immediately, retain
the raw recording/timeline for diagnosis, and report the scene and condition. Do not
compose or deliver this take; preserve diagnostics before a full retry after correction.
If stopping itself fails, report that failure and try to stop through the same MCP session;
never claim capture ended without confirmation.

At the end: `screencast_stop({ pageId })`. It returns
`The screencast recording has been stopped and saved to <path>`.

## Action recipes

### point
1. `take_snapshot({ pageId })` → choose the uid that matches the target description.
2. `evaluate_script({ pageId, function: "(el) => __demo.point(el)", args: ["<uid>"] })`

### click
1. `take_snapshot({ pageId })` → choose the uid.
2. `evaluate_script({ pageId, function: "(el) => __demo.point(el)", args: ["<uid>"] })`
3. `click({ pageId, uid: "<uid>" })`. The overlay draws the ripple.
4. If the result says `Page navigated to ...`, re-inject the overlay.

**A link that loads a new page** (a plain `<a href>`, not a single-page-app route) swaps
the document out within a frame or two of the click, so the ripple never reaches the
recording. Hold the navigation for 700 ms so the ripple plays first. Between steps 2 and 3:

```
evaluate_script({ pageId, args: ["<uid>"], function: "(el) => { const a = el.closest('a[href]'); a.addEventListener('click', (e) => { e.preventDefault(); setTimeout(() => { location.href = a.href; }, 700); }, { once: true }); }" })
```

The `click` result then won't say `Page navigated to ...`, because the navigation starts
after it returns. Instead, `wait_for({ pageId, text: ["<text on the new page>"] })` (its
result includes a fresh snapshot of the new page), then re-inject the overlay.

### dblclick
1. `take_snapshot({ pageId })` → choose the uid.
2. `evaluate_script({ pageId, function: "(el) => __demo.point(el)", args: ["<uid>"] })`
3. `click({ pageId, uid: "<uid>", dblClick: true })`. The overlay draws two ripples and the
   yellow double ring.

### key
`press_key({ pageId, key: "<key>" })`. Use Playwright-style names: `"Enter"`, `"Space"`,
`"ArrowLeft"`, `"Escape"`, `"Tab"`, and combinations such as `"Meta+K"`,
`"Control+Shift+R"` (modifiers: `Control`, `Shift`, `Alt`, `Meta`). The keycap appears at the
bottom right: `⌘ K`, `Space`, `Enter`, `←`, `⇧ Tab`. If the key causes a navigation,
re-inject the overlay.

### type
1. Focus the field with the **click** recipe (point, then click).
2. `type_text({ pageId, text: "<text>" })`. Add `submitKey: "Enter"` to press a key after the
   text. The typed characters are combined into one keycap (`hello demo`).

### scroll
There is no scroll tool. Use a smooth scroll and wait for it to finish:

- To an element: `evaluate_script({ pageId, function: "async (el) => { el.scrollIntoView({ behavior: 'smooth', block: 'center' }); await new Promise((r) => { addEventListener('scrollend', r, { once: true }); setTimeout(r, 1500); }); }", args: ["<uid>"] })`
- By an amount: `evaluate_script({ pageId, function: "async () => { scrollBy({ top: 600, behavior: 'smooth' }); await new Promise((r) => { addEventListener('scrollend', r, { once: true }); setTimeout(r, 1500); }); }" })` (a negative `top` scrolls up).

`press_key` with `"Space"` or `"PageDown"` also scrolls, and it shows a keycap.

### back
1. `evaluate_script({ pageId, function: "() => __demo.badge('◀ Back')" })`. The returned promise is awaited, so the badge is on screen before the page changes.
2. `navigate_page({ pageId, type: "back" })`
3. Re-inject the overlay.

### wait
- To let an animation or transition finish before the next action:
  `evaluate_script({ pageId, function: "() => new Promise((r) => setTimeout(r, <ms>))" })`.
  A wait on a still page adds nothing to the video, because compose drops frozen frames.
- For content to appear: `wait_for({ pageId, text: ["<visible text>"], timeout: 10000 })`.

## Checking a frame

`take_screenshot({ pageId })` returns the current viewport. Overlay effects are brief
(ripple ~0.9 s, keycap ~1.5 s, badge ~1.5 s), so a screenshot taken a few calls later won't
show them. To check the recording, pull frames from the video with ffmpeg instead.
`take_snapshot` doesn't list overlay elements, because the overlay host is `aria-hidden`.
