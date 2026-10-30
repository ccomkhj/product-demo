# Phone frame module: one owner for where a phone scene's page goes

**Goal:** The phone frame (GLOSSARY) gets one module that lays out and draws a phone scene.
Today the layout is spread over five places: `edit_plan.PhonePage.crop`, compose's
`phone_crops` (aspect refusal), `phone_frames` (no-room refusal, one frame per page shape),
`PhoneFrame` (a shell that runs two commands) and `screen_filters`, while
`phone_frame.py` only holds geometry and command builders. All of it is tested only through
real-ffmpeg compose CLI runs (`tests/test_compose_cli.py`).

**Principles:** DRY, KISS. No change to the spec, timeline, edit map or video output (the
frame-hash tests prove it). No new CLI flag. ADR-0011 holds: framing stays in compose's stage.
One PR, test first.

**Decisions (settled in review, 2026-10-31):**
- Two calls. `layouts` is pure: phone pages by scene, take size, `spec["phone"]`, the
  video size and the pixels to keep clear below in, one `Layout` per scene out (crop,
  geometry, backdrop, screen filters). It raises `PhoneFrameError` for a page that is not
  phone-shaped or has no room above the subtitles. `draw` frames a fitted screen clip;
  `run` is passed in, like `avatar.burn_avatar`.
- `draw` makes the mask and body PNGs the first time it meets a screen size, named by that
  size, so scenes of one page shape share them. No `drawn` dict in compose.
- The crop maths moves from `PhonePage.crop` into `phone_frame`; `PhonePage` stays in
  `edit_plan` as data (window, page, dpr). `phone_frame` does not import `edit_plan`.
- Compose keeps `subtitle_band` (it knows how cues are burned) and passes one number.
- `compose.main` catches `PhoneFrameError` with its other errors; hints are unchanged.
- Out of scope: encoder settings (`fps`, `x264` still passed in, see the house-encode idea),
  the timeline module.

---

## Task 1: pure layout tests (red)

`tests/test_phone_frame.py`, no ffmpeg:
- crop: window 100x240, page 80x240, dpr 1 in a 320x240 take is `(110, 0, 80, 240)`;
  a page larger than the take is clipped to it.
- a desktop-shaped page (320x240) is refused with the scene id and "emulat".
- an emulation that did not take (500x836 at dpr 0.25 in 320x240) is refused.
- a long subtitle band leaves no room: refused with "shorter sentences".
- two page shapes (80x240, 120x240) get the same screen height, widths 2:3.
- a 180x320 video fills more than 70% of its height with the phone.
- the frame ends above the clear line.

## Task 2: `scripts/phone_frame.py` (green)

`PhoneFrameError`, `Layout` (with `screen_filters()`), `layouts(...)`, `draw(...)`.
`too_wide` and `geometry` become internal to `layouts`; `frame_cmd` takes the layout and
the two PNG paths.

## Task 3: compose through it

Replace `phone_crops`, `PhoneFrame`, `phone_frames` and `screen_filters` with one
`phone_layouts` helper (filter phone scenes, measure the band, probe the take only when
there is a phone scene) and `phone_frame.draw` in the scene loop. Delete `PhonePage.crop`.

## Task 4: trim the CLI tests

Drop the CLI tests the layout tests now cover: desktop aspect, emulation not taken,
own page shape, vertical fills height. Keep: framed on the backdrop, subtitles never cover
the frame, bubble over a phone scene, both desktop-unchanged hash tests, missing page size,
and the no-room hint (proves `PhoneFrameError` reaches stderr without a traceback).

## Task 5: docs

`phone_frame.py` and `compose.py` docstrings; GLOSSARY "Phone frame" already names the owner.
