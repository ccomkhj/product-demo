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

<!-- click_sounds, music (#20) -->

## `exports`

Extra files made from the finished `<slug>-post.mp4`, each named `<slug>-post…`.

<!-- size_cap_mb, gif, poster (#22) -->

### `contact_sheet`

`<slug>-post-contact.png`: a grid of frames, each labelled with its time in the output.
Needs `rsvg-convert`.

- `columns`: 1 to 8, default 4.
- `interval_s`: seconds between frames, default the length / 12 (12 frames). At most 60
  frames.
