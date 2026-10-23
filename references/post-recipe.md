# Post recipe

`demos/<slug>/post.json`, read by `${CLAUDE_PLUGIN_ROOT}/scripts/postprocess.py`. One
optional key per operation; an absent key means the operation is off. Unknown keys and
out-of-range values fail before any encoding. Whatever order the keys are written in, the
operations run in this fixed order: trim and cuts, speed, watermark / callouts / progress
bar, cards, subtitle tracks and chapters, click sounds and music, exports.

```json
{
  "trim": {"start": 1.2, "end": {"scene": "outro", "offset_s": 3}, "cuts": [{"from": 14.0, "to": 17.5}]},
  "speed": 1.25,
  "exports": {"contact_sheet": {"columns": 4, "interval_s": 2}}
}
```

## Times

Every time is in the **source** video (`<slug>.mp4`), never the output, either:

- seconds, e.g. `14.0`, or
- a scene anchor: `{"scene": "<id>", "at": "<at phrase>", "offset_s": <s>}`. `at` (one of
  the scene's `at` actions) and `offset_s` (may be negative) are optional; without `at` it
  is the scene's start. Ids and phrases are in `<slug>.editmap.json`; anchors need it.

A time before 0, past the end of the source, or (for operations after trim) inside a cut
is an error.

## `source`

File name of the composed video, next to the recipe. Default `<slug>.mp4`, the folder's
name being the slug. Its `.srt` and `.editmap.json` are read from next to it.

## `trim`

- `start`: cut everything before this time.
- `end`: cut everything after this time.
- `cuts`: list of `{"from": t, "to": t}` middle ranges to remove; overlapping ranges merge.

Times snap to whole frames (1/30 s). Each cut gets a short audio fade on both sides.
Subtitle cues inside a cut are dropped; cues across a cut boundary are clipped, with a
warning.

## `speed`

One factor for the whole video, `0.5` to `2`. Video is retimed by timestamps, audio by a
tempo change that keeps the pitch; subtitles are retimed with it.

<!-- watermark, callouts, progress_bar (#18) -->

## `cards`

A title card before the demo and/or a closing card after it, each a still with silence
(music, if any, plays under it). Each card fades in and out, and the demo fades where it
meets a card. `"cards": {"title": {}, "closing": {}}` is enough: text defaults come from
`demo.json` in the demo folder. Needs `rsvg-convert`.

- `title`: `title` (default: the demo's title, else the slug), `subtitle`, `logo` (image
  file in the demo folder, drawn above the title).
- `closing`: `text` (call to action, no default), `url` (default: the last scene's `url`).
- Both: `seconds` 1 to 10 (default 3), `background` (default `"#111111"`), `color` (text,
  default `"#ffffff"`), hex colours.

The title card's length shifts everything after it: subtitles, and every recipe time
(still given in source seconds) lands that much later in the output. The progress bar
covers only the demo, not the cards.

<!-- translations, chapters (#21) -->

## `click_sounds`

A short click one cursor glide (600 ms) after the start of each click, double-click and
field click (`type`) whose start is in `<slug>.editmap.json`: each scene's first action and
every `at` action. Times go through the trim, cuts and speed. Needs the edit map.

- `level`: 0 to 1, default 0.5.
- `file`: a custom sound next to the recipe; default the bundled
  `${CLAUDE_PLUGIN_ROOT}/assets/click.wav`.

Clicks it cannot place get no sound and a warning each (`no click sound for scene '<id>'
action <n> ...`): a start inside a cut, or a start compose does not know. Giving that
action an `at` phrase and re-composing gives it a sound.

## `music`

Background music under the whole video, cards included: looped when shorter, faded out at
the end, ducked by sidechain compression while the narration speaks. Never download music:
ask the user for a file they have the rights to.

- `file` (required): an audio file next to the recipe; a URL is rejected.
- `level`: 0 to 1, default 0.2.
- `duck`: compression ratio under the narration, 1 (no ducking) to 20, default 8.
- `fade_out_s`: fade at the end, default 2.

## `exports`

Extra files made from the finished `<slug>-post.mp4`, each named `<slug>-post…`.

### `size_cap_mb`

`<slug>-post-<cap>mb.mp4`, e.g. `"size_cap_mb": 10` for Slack or email: a two-pass encode
at the bitrate that fits the cap (95% of it, audio 64 kb/s). The resolution stays unless
the bitrate would drop below a readable floor (0.015 bits per pixel per frame); then it
steps down through 1080, 720, 540 and 360 px high. Below the floor at 360 px the run fails
with the smallest reachable size: cut the video shorter or raise the cap.

### `gif`

`<slug>-post-gif.gif`, looping, with a palette generated from the clip so UI colours do
not band.

- `from`, `to`: the source range (times as above), default the whole video. Cut parts
  are left out.
- `width`: 16 to 1920 px, default 640. `fps`: 1 to 30, default 10.

Over 10 MB warns: shorten the range or lower the width or fps.

### `poster`

`<slug>-post-poster.png`, one full-size frame.

- `time`: the frame's time, default the middle of the first scene that is left after the
  trim and any title card.

### `contact_sheet`

`<slug>-post-contact.png`: a grid of frames, each labelled with its time in the output.
Needs `rsvg-convert`.

- `columns`: 1 to 8, default 4.
- `interval_s`: seconds between frames, default the length / 12 (12 frames). At most 60
  frames.
