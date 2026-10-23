---
name: postprocessing
description: Edit an already composed product-demo video without touching the original - speed it up, trim the start or end, cut middle ranges, add a watermark, arrows, text callouts or a progress bar, and review it as a contact sheet - by writing a post recipe and running one command. Use when the user wants to change a finished demos/<slug>/<slug>.mp4 ("make it 1.25x", "cut the first two seconds", "remove the slow page load") rather than re-record or re-compose it.
argument-hint: "<demo slug or folder> <what to change>"
---

# Post-processing

Turns `demos/<slug>/<slug>.mp4` (made by `/product-demo:run`) into `<slug>-post.mp4` and
`<slug>-post.srt`, driven by a **post recipe**, `demos/<slug>/post.json`. The originals
(`<slug>.mp4`, `<slug>.srt`, `transcript.md`, `<slug>.editmap.json`) are never modified, so
the user can always start over.

The plugin root is `${CLAUDE_PLUGIN_ROOT}`: scripts and references live there. In the
reference files, the plugin-root variable (`CLAUDE_PLUGIN_ROOT`) stands for this same
directory. Run the script through `uv` exactly as written below (it needs Python 3.11+ and
`ffmpeg`; `rsvg-convert` from librsvg for text: the contact sheet, a text watermark, callouts).

## 1. Find the demo and its recipe

Find the demo folder from the request: `demos/<slug>/` under the current working directory,
holding `<slug>.mp4`. If several could match, ask which. If `post.json` exists, read it and
**edit it**: change only what the user asked for and keep every other key, so a re-run
reproduces the rest exactly. Otherwise start from `{}`.

## 2. Write the recipe

Translate the request into recipe keys; the user never writes JSON. The keys, their fields
and limits are in `${CLAUDE_PLUGIN_ROOT}/references/post-recipe.md`. Times are always against
the original video (source seconds) or a scene anchor, never against the edited output, so
adding a cut or a speed never moves other times. Prefer scene anchors
(`{"scene": "<id>", "at": "<at phrase>", "offset_s": -0.5}`) when the user talks about a
scene or an action; the scene ids and `at` phrases are in `<slug>.editmap.json`. To find
source seconds for "the slow page load", make a contact sheet of the source (step 4).

Callout positions are source-video pixels. Pick them from a contact sheet of the source
(step 4): find the moment, then convert sheet coordinates (each frame is scaled down to at
most 480 px wide) by the video's width / the frame's width, or Read one full-size frame
(`ffmpeg -v error -ss <t> -i demos/<slug>/<slug>.mp4 -frames:v 1 <scratch>/frame.png`).
Keep callouts out of the bottom 20% (burned-in subtitles) and away from the presenter
bubble; the run warns when they overlap.

## 3. Run

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/scripts/postprocess.py demos/<slug>/post.json
```

It validates the whole recipe before any ffmpeg work, then prints one tab-separated line per
operation applied, then the totals:

```
trim  removed <s>s in <n> ranges  kept <s>s
speed  <x>x
watermark  text "<text>" | image <file>  <corner>
callouts  <n>  <type> <start>s-<end>s, ...
progress_bar  <edge> <n>px  0.000s-<s>s
contact_sheet  <n> frames  <png path>
total  <s>s  <video path>
subtitles  <n> cues  <.srt path>
```

Warnings go to stderr (`postprocess: warning: ...`): a cut that splits a subtitle cue (a cut
mid-sentence; offer to move it to a cue boundary), a callout over the subtitle band or the
presenter bubble (offer to move it), or a missing edit map. On
`postprocess: error: ...` nothing was written: fix the recipe and re-run. A demo without
`<slug>.editmap.json` cannot use scene anchors; tell the user to re-run compose
(`/product-demo:run`, re-compose the demo) to get one.

## 4. Verify, then hand over

- Length: `ffprobe -v error -show_entries format=duration -of csv=p=0 demos/<slug>/<slug>-post.mp4`
  must match the `total` line to within 1/30 s.
- Frames: add `"exports": {"contact_sheet": {}}` (or keep it) and Read
  `<slug>-post-contact.png`; each frame is labelled with its output time. Check the moments
  that changed: the frames either side of each cut, that nothing important was cut, and that each watermark, callout (inside its window) and progress bar shows where expected.
  For a closer look at one moment, use the per-moment contact sheet recipe in
  `${CLAUDE_PLUGIN_ROOT}/references/verify.md` on `<slug>-post.mp4`.
- For a contact sheet of the source (to pick cut times), run with a recipe holding only
  `exports.contact_sheet`: with no other operation its frame times are source times.

Hand over `<slug>-post.mp4` and `<slug>-post.srt` with the printed lines' summary and any
warnings. Burned-in subtitles move with the picture: they are cut and sped up with it and
cannot be removed or restyled here; mention this once when it matters.
