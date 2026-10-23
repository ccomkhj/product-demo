# Post-processing

Details behind `/product-demo:postprocessing`. The [README](../README.md) has the short
version; the exact fields and limits of every key are in the
[post recipe reference](../references/post-recipe.md).

## What it does

It edits a demo that `/product-demo:run` already composed, without touching the original.
You say what you want in plain words. The agent writes the edits as a **post recipe**,
`demos/<slug>/post.json`, runs one command, checks the result and hands it over.

The originals stay as they are: `<slug>.mp4`, `<slug>.srt`, `transcript.md` and
`<slug>.editmap.json`. You can always start over. Every new file is named `<slug>-post…`.

Use it for a finished video: speed it up, cut dead time, brand it, add subtitles in other
languages, or export a GIF. To change the script, the voice or what was clicked, re-run
`/product-demo:run` instead.

## Trigger it

```
/product-demo:postprocessing
```

On its own, it finds your demos (asking which one if there are several), shows every option
below with a one-line example, and you answer in plain words ("1.25x and a title card"). If
you already know what you want, say it straight away:

```
/product-demo:postprocessing csv-export: make it 1.25x and cut the first two seconds
/product-demo:postprocessing csv-export: add our logo logo.png in the top-right corner
/product-demo:postprocessing csv-export: add a title card and a closing card with "Try it today"
/product-demo:postprocessing csv-export: Korean and German subtitles, and chapters for YouTube
/product-demo:postprocessing csv-export: give me a GIF of the export scene for the PR
/product-demo:postprocessing csv-export: a version under 10 MB for Slack
```

The agent finds `demos/<slug>/` under the current folder. If several demos match, it asks
which one. You never write JSON yourself; the examples below show what the agent writes.

## Outputs

All next to the original, in `demos/<slug>/`:

| File | When |
|------|------|
| `post.json` | Always: the recipe. Kept for the next run. |
| `<slug>-post.mp4` | Always: the edited video. |
| `<slug>-post.srt` | Always: the subtitles, retimed to the edited video. |
| `<slug>-post.<lang>.srt` | One per translation. |
| `<slug>-post-chapters.txt` | With chapters: the list to paste into a YouTube description. |
| `<slug>-post-<cap>mb.mp4` | With a size cap, e.g. `<slug>-post-10mb.mp4`. |
| `<slug>-post-gif.gif` | With a GIF export. |
| `<slug>-post-poster.png` | With a poster export. |
| `<slug>-post-contact.png` | With a contact sheet export. |

## Times and anchors

Every time in the recipe is against the **original** video, never the edited one. So
adding a cut or changing the speed never moves the other times. A time is either:

- seconds in the original, e.g. `14.0`, or
- a scene anchor: `{"scene": "export", "at": "click Export", "offset_s": -0.5}`.
  `scene` is a scene id. `at` is one of that scene's `at` phrases; without it, the anchor
  is the scene's start. `offset_s` moves it earlier (negative) or later.

The agent prefers anchors when you talk about a scene or an action ("cut the pause before
the export click"). Scene ids and `at` phrases come from `<slug>.editmap.json`, which
compose writes. A demo composed before the edit map existed has none: anchors, chapters
and click sounds need it, so re-compose the demo with `/product-demo:run` first.

A time before 0, past the end, or inside a cut is an error.

To find a moment in seconds ("the slow page load"), the agent makes a contact sheet of the
original first: a recipe with only `exports.contact_sheet` labels its frames with
original times.

## Operations

Each operation is one optional key. A missing key means the operation is off.

### Trim and cuts

"Cut the first two seconds, stop three seconds after the outro starts, and remove the slow
page load at 14 s."

```json
{
  "trim": {
    "start": 2,
    "end": {"scene": "outro", "offset_s": 3},
    "cuts": [{"from": 14.0, "to": 17.5}]
  }
}
```

`start` and `end` cut the ends; `cuts` removes middle ranges. Each cut gets a short audio
fade so there is no click. Subtitles inside a cut are dropped; a subtitle that crosses a
cut is clipped, with a warning (the cut is mid-sentence; the agent offers to move it).

### Speed

"Make it 1.25x."

```json
{"speed": 1.25}
```

One factor for the whole video, 0.5 to 2. The voice keeps its pitch. Subtitles, callouts,
chapters and click sounds move with it.

### Watermark

"Put our logo in the corner." or "Add 'acme.com' as a watermark, bottom right."

```json
{"watermark": {"image": "logo.png"}}
```

```json
{"watermark": {"text": "acme.com", "corner": "bottom-right", "opacity": 0.6}}
```

An image (a PNG in the demo folder) or a short text, for the whole video. The default
corner is top-right, which keeps clear of the overlay's keycaps, its Back badge and the
presenter bubble (bottom-left when the bubble sits top-right). Default size is 6% of the
video's height, opacity 0.8.

### Callouts

"Point an arrow at the Export button when the voice says 'click Export', with a label 'New in
v2'."

```json
{
  "callouts": [
    {"type": "arrow", "from": [1400, 300], "to": [1580, 180],
     "start": {"scene": "export", "at": "click Export", "offset_s": -0.5},
     "end": {"scene": "export", "at": "click Export", "offset_s": 2}},
    {"type": "text", "text": "New in v2", "position": [1300, 320],
     "start": {"scene": "export", "at": "click Export", "offset_s": -0.5},
     "end": {"scene": "export", "at": "click Export", "offset_s": 2}}
  ]
}
```

Arrows and short text labels, each shown for its own window and faded in and out.
Positions are pixels of the original video, from the top-left; the agent picks them from a
contact sheet or a full-size frame. The default colour is red (`#ff3b30`). The run warns
when a callout reaches into the subtitle band (bottom 20%) of burned-in subtitles, or
covers the presenter bubble.

### Progress bar

"Add a progress bar at the top."

```json
{"progress_bar": {"edge": "top"}}
```

A thin bar that fills from left to right. Default: bottom edge, 6 px, `#4da3ff`. It
covers the demo only, not the title or closing card.

### Title and closing cards

"Add a title card and a closing card that says 'Try CSV export today'."

```json
{
  "cards": {
    "title": {},
    "closing": {"text": "Try CSV export today"}
  }
}
```

A still before and/or after the demo, 3 s each by default (1 to 10), white text on
`#111111`, fading into the demo. The title card defaults to the demo's title (from
`demo.json`, else the slug); add `subtitle` or a `logo` image. The closing card shows your
call to action and defaults its `url` to the last scene's URL. Music, if any, plays under
the cards.

### Background music

"Add background music, music.mp3, quietly."

```json
{"music": {"file": "music.mp3", "level": 0.15}}
```

Plays under the whole video, cards included. It loops when shorter, fades out at the end
(2 s) and ducks while the narration speaks. Default level 0.2. The agent never downloads
or generates music: give it a file in the demo folder that you have the rights to.

### Click sounds

"Add click sounds."

```json
{"click_sounds": {}}
```

A short click on each click, double-click and field click, timed to when the cursor
lands. Default level 0.5; set `file` for your own sound. It needs the edit map. Clicks it
cannot place get no sound and a warning each; giving that action an `at` phrase and
re-composing fixes it.

### Translations

"Add Korean and German subtitles."

```json
{"translations": ["ko", "de"]}
```

The agent translates the subtitles itself (no external service) and writes
`<slug>.ko.srt` and `<slug>.de.srt` next to `<slug>.srt`: same cues, same timings, only
the text translated. Product names, UI labels, URLs and numbers stay as written.

You get `<slug>-post.ko.srt` and `<slug>-post.de.srt`, and the mp4 gets one selectable
subtitle track per language plus the original. If the original has burned-in subtitles,
viewers see two at once; the run warns, and the agent suggests re-composing with
`--no-burn-subs` first.

### Chapters

"Add chapters for YouTube."

```json
{"chapters": true}
```

One chapter per scene, named after its title, embedded in the mp4 and written to
`<slug>-post-chapters.txt` for the YouTube description. With a title card, the first
chapter is "Intro". The run warns when YouTube would ignore them: fewer than 3, or one
under 10 s.

### Exports

Extra files made from the finished `<slug>-post.mp4`, all under `exports`.

"A version under 10 MB for Slack, a GIF of the export scene, a thumbnail and a contact
sheet."

```json
{
  "exports": {
    "size_cap_mb": 10,
    "gif": {"from": {"scene": "export"}, "to": {"scene": "outro"}, "width": 480},
    "poster": {},
    "contact_sheet": {}
  }
}
```

- **Size cap**: a second mp4 under the cap. It lowers the bitrate first and the resolution
  only when text would get unreadable (1080, 720, 540, 360 px). If the cap is out of reach
  even at 360 px, the run fails with the smallest size it could reach. It keeps every
  subtitle track and the chapters.
- **GIF**: looping, with its own palette so UI colours do not band. Default the whole
  video, 640 px wide, 10 fps. Over 10 MB warns.
- **Poster**: one full-size frame. Default the middle of the first scene left after the
  trim.
- **Contact sheet**: a grid of frames labelled with their time. Default 4 columns, 12
  frames.

## Order

The operations always run in the same order, whatever order you ask for them in:

1. trim and cuts
2. speed
3. watermark, callouts, progress bar
4. title and closing cards
5. subtitle tracks and chapters
6. click sounds and music
7. exports

So the watermark and the progress bar cover the demo but not the cards, and every export
is made from the finished video.

## Change it later

The recipe stays in `demos/<slug>/post.json`. Ask again, e.g.
`/product-demo:postprocessing csv-export: make it 1.5x instead`. The agent edits only the
key you mention and keeps the rest, then re-runs. An unchanged recipe gives the same
video every time. To start over, delete `post.json` and the `<slug>-post…` files.

The script can also be run directly from a clone:

```bash
uv run python scripts/postprocess.py demos/csv-export/post.json
```

It checks the whole recipe before any encoding. A typo, an unknown key or an out-of-range
value fails in a second, and nothing is written. A later failure (an ffmpeg step, a size
cap it cannot meet) can leave the outputs of the steps before it; fix it and re-run, which
overwrites them. It prints one line per operation, then the total length and the output
paths.

## Warnings

Warnings do not stop the run (`postprocess: warning: ...`). The agent passes them on:

- a cut splits a subtitle (mid-sentence)
- a callout over the band of burned-in subtitles, or over the presenter bubble
- a click that gets no sound
- no edit map, so no scene anchors
- translated tracks over burned-in subtitles
- chapters YouTube would ignore
- a GIF over 10 MB

## Requirements

- `ffmpeg`, and [`uv`](https://docs.astral.sh/uv/) with Python 3.11+, as for
  `/product-demo:run`.
- `rsvg-convert` (`brew install librsvg`) for anything with drawn text: a text watermark,
  callouts, cards and the contact sheet (ADR-0010). An image watermark does not need it;
  without it only a recipe that draws text fails, with the install hint.
- `<slug>.editmap.json` for scene anchors, chapters and click sounds.

## Limits

- Burned-in subtitles are part of the picture. They are cut and sped up with it, but
  cannot be removed or restyled here. Re-compose with `--no-burn-subs` for clean video.
- One speed for the whole video; no per-scene speed.
- No new narration, re-recording or changes to what was clicked: use `/product-demo:run`.
- Music is never downloaded or generated, and a URL is rejected.
- Callout positions are fixed pixels; they do not follow anything moving on screen.
