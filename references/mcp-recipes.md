# Chrome DevTools MCP recipes

The exact call sequences to use when recording a demo. Follow them literally. Every call
below was run against the Chrome DevTools MCP (`mcp__chrome-devtools__*`). If the tools
are deferred, load them first with
`ToolSearch("select:mcp__chrome-devtools__list_pages,mcp__chrome-devtools__navigate_page,mcp__chrome-devtools__resize_page,mcp__chrome-devtools__emulate,mcp__chrome-devtools__evaluate_script,mcp__chrome-devtools__take_snapshot,mcp__chrome-devtools__click,mcp__chrome-devtools__press_key,mcp__chrome-devtools__type_text,mcp__chrome-devtools__upload_file,mcp__chrome-devtools__wait_for,mcp__chrome-devtools__take_screenshot,mcp__chrome-devtools__screencast_start,mcp__chrome-devtools__screencast_stop")`.

Every tool takes `pageId`. Get it once with `list_pages` (the selected page is marked
`[selected]`) and use the same id for the whole recording.

## Rules

- **One tab.** The screencast records one page. Never call `new_page`. Follow links in the
  same tab, and avoid targets that open a new window.
- **Re-inject the overlay after every navigation**: after every `navigate_page`
  (url, back, forward, reload) and after any `click` whose result says
  `Page navigated to ...`. Injecting again on a page that already has the overlay is safe:
  it returns `"overlay already present"`. Every injection is followed by
  `__demo.device(...)` with the current scene's device (see Overlay injection).
- **Point before every click.** `__demo.point` makes the fake cursor glide to the target,
  so viewers see where the click is going.
- **Badge before Back.** Show `◀ Back` before `navigate_page` with `type: "back"`.
- **Resolve targets at record time.** Element targets in the demo spec are natural-language
  descriptions. Take a fresh `take_snapshot` on each page and pick the uid that matches the
  description. Never reuse uids from an earlier run or an earlier snapshot of a page that
  has since changed: uids are not stable. On a long page, pass `filePath: "demos/<slug>/snap.txt"`
  and grep it (the MCP writes only under the working directory).
- **Mark a beat before every `at` action.** Compose cuts the scene there and holds the
  screen until the narration reaches the phrase, so a missing or extra beat fails compose.
- **Use real input.** Clicks, double-clicks and key presses must go through `click`,
  `press_key` and `type_text`. They send real CDP input events, which the overlay draws on
  its own (ripple, double ring, keycap). Don't use `fill` or `fill_form` on screen: they
  set the value without key events, so nothing is shown.

## Overlay injection

`assets/overlay.js` is a classic script. Inject it with `format: "script"`:

```
evaluate_script({ pageId, sourcePath: "${CLAUDE_PLUGIN_ROOT}/assets/overlay.js", format: "script" })
```

It returns `"overlay installed"` the first time on a document and
`"overlay already present"` after that.

Then, after every injection, set the overlay's device to the device of the scene
being recorded (or about to be: the gap before a scene counts as that scene):

```
evaluate_script({ pageId, function: "() => __demo.device('phone')" })   // or 'desktop'
```

A scene's device is its own `device`, else the demo spec's top-level `device`, else
`"desktop"`. The device is per document, so a new page starts as desktop until told.

`sourcePath` only works when the file is inside one of the MCP server's workspace roots
(normally the directory Claude Code was started in). Otherwise the call fails with
`Access denied: path "..." is not within any of the configured workspace roots`. In that
case, read `${CLAUDE_PLUGIN_ROOT}/assets/overlay.js` and pass its full contents inline:

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
| `__demo.device(device)` | `"desktop"` or `"phone"`. On a phone the arrow cursor is hidden, clicks are drawn as a fingertip plus the ripple where they land, and `point` hovers a fingertip on the target instead of gliding. Keycaps, badges, captions and the flash are the same in both. |
| `__demo.point(el)` | Glides the cursor to the element's centre (~600 ms). Scrolls it into view first if it is off screen. Resolves when the glide ends. `el` may also be a CSS selector. On a phone a fingertip hovers on the element for the same ~600 ms instead. |
| `__demo.tap(el)` | Draws a click on the element (ripple, or fingertip and ripple on a phone) without sending one. Only for a click the recipe can't send (see upload). |
| `__demo.badge(text[, ms])` | Transient badge at the top left (default 1500 ms). Resolves after ~700 ms, so awaiting it before a navigation keeps it on screen long enough to be recorded. |
| `__demo.upload(files)` | Badge naming what an `upload` added: `📎 clip.mp4` for one file, a count and kind for several (`📎 3 videos`, `📎 2 images`; mixed kinds say `files`). Resolves like `badge`. |
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
   Do this at the desktop size **even when every scene is a phone scene**, with no
   emulation on: the take's pixel size is fixed when the screencast starts, and the sync
   flash must fill it. Phone scenes switch later, in the gap before them (Phone scenes below).
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
   `uv run --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/scripts/mark.py demos/<slug>/timeline.jsonl rec_start --sync-ms <ms>`

   Chrome starts the recording on its own clock and never says when, and the agent takes
   seconds between tool calls, so the time of this mark says nothing about where the video
   begins. Compose finds the flash in `raw.mp4` and lines the timeline up on it. Without the
   flash, every cut lands seconds early, and each action plays at the phrase of the one
   after it.

Then, for each scene in spec order:

1. If the scene's device differs from the previous scene's (the first scene counts as
   following a desktop one), switch now with the Phone scenes recipe below. If the browser
   isn't on the scene's `url` yet, `navigate_page` there and re-inject the overlay. Do both
   **before** the start marker, so the switch and the page load fall in the gap between
   scenes, which compose drops.
2. Evaluate this scene's `prerequisites` using the checks in `readiness.md`, with fresh
   snapshots and at most 10 seconds per condition. On failure or ambiguity, stop the take.
   Only after they pass: `uv run --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/scripts/mark.py demos/<slug>/timeline.jsonl start <scene-id>`.
   For a phone scene, add the sizes the switch read:
   `... start <scene-id> --window <W>x<H> --page <w>x<h> --dpr <dpr>`.
3. Run the scene's actions with the recipes below. Right before each action that has an
   `at`, mark a beat, after any snapshot that action needs but before its first visible
   step (the `point` of a click):
   `uv run --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/scripts/mark.py demos/<slug>/timeline.jsonl beat <scene-id>`
4. Don't wait to fill out the narration. Compose drops every frame where nothing moves, so a
   pause on a still page never reaches the video: a scene lasts only as long as its motion
   (glides, scrolls, ripples, page changes). Compose holds the screen until each `at`
   phrase, and holds the last frame for the rest of the narration.
5. Evaluate `expected_outcomes` with fresh evidence before the end marker. If they pass:
   `uv run --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/scripts/mark.py demos/<slug>/timeline.jsonl end <scene-id>`.
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
`The screencast recording has been stopped and saved to <path>`. If the last scene was a
phone scene, switch back to desktop afterwards, so the browser is left as it was found.

## Phone scenes

A phone scene is recorded in a phone-sized, touch-enabled page, so the app renders its own
phone layout; compose crops it and draws the phone frame (ADR-0011). The phone size is the
spec's `phone.width` × `phone.height` (default 390×844). The switch reloads the page, so a
phone scene starts from a fresh load: it loses page state and the overlay.

**Why `emulate` here, despite the warning above.** Only `emulate` turns on touch and
mobile, which apps read from `(pointer: coarse)`. The warning is about an emulated page
*bigger* than the real window, which the screencast crops. Here the window is resized
first and the emulated height is set to the height the window got, so the page is never
bigger than the window. Chrome's 500 px minimum window width leaves a strip of page
background beside the 390 px page; compose crops it away.

**Phone on**, in the gap before a phone scene that follows a desktop one:

1. `evaluate_script({ pageId, function: "() => devicePixelRatio" })` → `dpr` (read before
   emulating).
2. `resize_page({ pageId, width: <phone.width>, height: <phone.height> })`, then
   `evaluate_script({ pageId, function: "() => [innerWidth, innerHeight]" })` → the window
   `[W, H]`, such as `[500, 836]`. Let `h` be the smaller of the phone height and `H`.
3. `emulate({ pageId, viewport: "<phone.width>x<h>x<dpr>,mobile,touch" })`, such as
   `"390x836x1,mobile,touch"`. The page reloads.
4. Re-inject the overlay, then `__demo.device('phone')`.
5. Check `[innerWidth, innerHeight]` is `[<phone.width>, h]` and
   `matchMedia('(pointer: coarse)').matches` is `true`. Otherwise stop the take: the app
   would render its desktop layout. The start mark takes `--window <W>x<H> --page
   <phone.width>x<h> --dpr <dpr>`. A later phone scene right after this one needs no new
   switch; its start mark repeats the same three values.

**Phone off**, in the gap before a desktop scene that follows a phone one:

1. `emulate({ pageId })` with no `viewport`: each call replaces every emulation setting.
   The page reloads.
2. `resize_page` back to the desktop size the take started at (such as 1486×836) and check
   `[innerWidth, innerHeight]` is that size.
3. Re-inject the overlay, then `__demo.device('desktop')`.

Still one tab, one screencast and one sync flash per take (ADR-0004). The flash always
runs before the first switch, at the desktop size. Inside a phone scene, every action
recipe below works unchanged, `at` phrases and beats included; the overlay draws taps and
hovers instead of the cursor.

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

### upload
The native file picker can't be recorded, so the tap on the control and a badge stand in
for it. Make each `files` path absolute first: relative paths are relative to the demo
folder (`demos/<slug>/`), like `avatar.image`. Local preflight has already checked they exist.

1. `take_snapshot({ pageId })` → choose the uid of the visible control the target names
   (the "Add videos" button, the drop zone's "Browse" link). Most apps hide the real
   `<input type="file">` behind such a control; target the control, not the hidden input,
   so the tap lands where a user would tap. Only when the input itself is visible on screen
   is it the target.
2. If the action has an `at`, mark a beat now.
3. `evaluate_script({ pageId, function: "(el) => __demo.point(el)", args: ["<uid>"] })`
4. `upload_file({ pageId, uid: "<uid>", filePaths: ["<absolute path>", ...] })`, all files in
   one call. On a control that opens a file chooser it clicks the control (the overlay draws
   the ripple) and answers the chooser with the files; the page gets real files. The paths
   must be inside the MCP workspace roots, like `screencast_start`'s `filePath`; a demo
   folder under the working directory is.
   **Several files on a control** fail with `Multiple file uploads only work with <input
   type=file multiple>`, and nothing is clicked (an MCP limitation, desktop and phone
   alike). Upload to the input itself instead, and draw the tap with the overlay:
   1. `evaluate_script({ pageId, args: ["<uid>"], function: "(el) => __demo.tap(el)" })`.
      Don't `click` the control: with no upload waiting for it, the chooser it opens may be
      the native OS dialog, left open.
   2. Unhide the input next to it:
      `evaluate_script({ pageId, function: "() => { const f = document.querySelector('input[type=file]'); f.hidden = false; f.style.cssText = 'position:fixed;left:0;top:0;width:1px;height:1px;opacity:0.01'; }" })`
      (narrow the selector when the page has several file inputs).
   3. `take_snapshot({ pageId })` lists it as a `button "Choose Files"`; `upload_file` on
      that uid with all the files. The page gets one `change` with every file.
5. If the result says `Page navigated to ...`, re-inject the overlay.
6. `evaluate_script({ pageId, function: "() => __demo.upload(<paths>)" })` shows the badge.
   `args` takes element uids only, so the paths go inline: JSON-encode the list (double
   quotes, escaped), so a `'` or `"` in a file name can't break the script, e.g.
   `"() => __demo.upload([\"/demos/tour/it's.mp4\", \"/demos/tour/b.png\"])"`.

If `upload_file` reports that no file chooser opened, the uid is neither a file input nor
a control that opens one: take a fresh snapshot and pick the control again.

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
