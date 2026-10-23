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

## `watermark`

A logo image or a short text, in a corner for the whole video.

- `image`: a PNG next to the recipe, or `text`: a short string (one of the two).
- `corner`: `top-right` (default), `top-left`, `bottom-left`, `bottom-right`. The default
  keeps clear of the overlay's keycaps (bottom-right), its Back badge (top-left) and the
  presenter bubble (`bottom-left` by default; with the bubble at `top-right`, the default
  is `bottom-left`).
- `size`: the image's height or the text's font size, as a share of the video's height,
  `0.02` to `0.3`, default `0.06`.
- `opacity`: `0.1` to `1`, default `0.8`.

A `text` watermark needs `rsvg-convert`; an `image` one does not.

## `callouts`

A list of arrows and text labels, each shown in its own window and faded in and out
(0.25 s). Positions are pixels of the source video (`[x, y]`, from the top-left).

- `{"type": "arrow", "from": [x, y], "to": [x, y], "start": t, "end": t}`: the head is at `to`.
- `{"type": "text", "text": "Click Export", "position": [x, y], "start": t, "end": t}`:
  a dark box whose top-left corner is at `position`. Keep the text short (one line).
- `colour`: optional, `"#rrggbb"` (default `#ff3b30`): the arrow, or the box's border.

`start` and `end` are recipe times (scene anchors stay put across cuts and speed). A
window edge inside a cut is an error. Warns when a callout reaches into the burned-in
subtitle band (the bottom 20%) while a cue is showing, or covers the presenter bubble
(from `demo.json`'s `avatar`). Needs `rsvg-convert`.

## `progress_bar`

A thin bar along an edge that fills from left to right over the demo. It covers only the
demo: overlays are drawn before the cards are added, so cards have no bar.

- `edge`: `bottom` (default) or `top`.
- `colour`: `"#rrggbb"`, default `#4da3ff`.
- `thickness`: pixels, `1` to `40`, default `6`.

<!-- cards (#19) -->

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
