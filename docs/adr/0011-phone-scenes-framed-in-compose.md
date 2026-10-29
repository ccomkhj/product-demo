# 0011: Phone scenes are framed in compose, recorded at a matching emulated size

Status: accepted (2026-10-29)

## Context

A phone scene must make the app render its real mobile layout, so the page has to report a
phone size and touch input (`(pointer: coarse)`, no `(hover: hover)`). Only `emulate` sets
touch and mobile, but the recipe warns against its `viewport` because the screencast records
the real window and crops an emulated page that is bigger than it. A spike (#33) recorded
one take on Chrome DevTools MCP: desktop scene, then resize and emulate, a phone scene,
then undo both, then a desktop scene. It ran on a 1920×1080, 1× screen, with the desktop
page at 1486×836. Its findings:

- The screencast kept running across both switches, in one tab, with one sync flash.
- `screencast_start` fixes the take's pixel size from the page at start
  (`Page.startScreenRecording` with `maxWidth`/`maxHeight`). Every later frame keeps that
  size. A smaller window is centred in it at 1:1 on black bars. A larger window is scaled
  down to fit. So the size never changes mid-stream, and compose's decimate step needs no
  change.
- The real window can't take a phone size. `resize_page` to 390×844 gave 500×836: Chrome
  has a 500 px minimum window width, and the screen limits the height. In the recording,
  the emulated 390 px page sits at the left of the 500 px window, and the 110 px strip to
  its right shows the page background. An emulated height taller than the window was cut
  off at the bottom. With the emulated height equal to the window's height, the whole page
  was recorded.
- Changing touch or mobile with `emulate` reloads the page, both when it is turned on and
  when it is undone. The page loses its state and the overlay.
- Under emulation, `pointer: coarse` matched and `hover: hover` didn't. After undoing it,
  both were back to normal, and the window was the desktop size again.
- MCP `click` and `upload_file` on a control that opens a file chooser both sent a trusted
  `mousedown`. The overlay drew its ripple.
- With emulation turned on before `screencast_start`, the take came out at the emulated
  size (390×652 here, with the 500×836 window scaled down to fit). The sync flash wasn't
  found: it only covers the emulated page, not the strip next to it.

## Decision

- Every take starts at the desktop size, without emulation. This includes a demo where
  every scene is a phone scene. The flash runs first, and the switch to phone happens in
  the gap before the first phone scene. That keeps the window at 1:1 in the recording, and
  the flash fills the whole frame.
- Phone on, in the gap before a phone scene: `resize_page` to the phone size, then read
  `[innerWidth, innerHeight]`. Then `emulate` with `viewport` set to
  `<phone width>x<H>x<dpr>,mobile,touch`, where `H` is the smaller of the phone height and
  the `innerHeight` the window got, and `dpr` is the screen's own `devicePixelRatio`, read
  before emulating. Re-inject the overlay, because the page has reloaded. The emulated
  height matches the window exactly, so no part of the page is lost. The width can't match
  (500 px minimum), but a narrower emulated page leaves a strip next to it and hides
  nothing. The recipe's warning is about a page bigger than the window, which this never
  creates.
- Phone off, in the gap before a desktop scene: `emulate` without `viewport` (each call
  replaces every emulation setting), `resize_page` back to the desktop size, then
  re-inject the overlay.
- Compose crops each phone scene to the recorded page area before it draws the frame. The
  recipe writes the window size and the emulated page size, in CSS pixels, on the scene's
  start mark. Compose places the window centred in the take at 1:1 times dpr, and the page
  at the window's top-left corner.
- The phone frame is a generic rounded device that compose draws with ffmpeg (as in
  ADR-0006). It is not drawn in the page. A frame drawn in the page would shrink the
  viewport the app sees. It would also vanish on every navigation and reload, and the
  app's own styles could cover it. In compose, it doesn't depend on the app, and it stays
  the same on every scene. A generic frame has no device brand to license, and it fits
  whatever page size the screen allowed.

## Consequences

- Turning on phone mode reloads the page. A phone scene starts from a fresh load of its
  URL, and state from the scene before it is gone unless the app keeps it (in storage or
  on its server). That suits a QR hand-off, where the phone opens its own page anyway.
- On a screen that can't fit 844 px, the phone page is shorter than 390×844. Readiness
  reports the size it actually got.
- An all-phone take is mostly black bars, which costs file size but not sharpness. Compose
  crops them away.
- `upload_file` with several files on a control that opens a chooser fails in the MCP
  ("Multiple file uploads only work with <input type=file multiple>"), even when the input
  allows several. Several files work only when the target is the `<input>` itself. The
  upload recipe has to deal with this.
- The spike ran on a 1× screen. On Retina, the same geometry should hold at dpr 2, but
  that wasn't recorded.
