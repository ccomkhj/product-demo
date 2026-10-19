# Verifying a composed demo

Recipes for step 7 of SKILL.md. Run them from the directory holding `demos/`.

## Compose output

`compose.py` prints one tab-separated line per scene, then the totals:

```
<id>  <hold|speedup|pad>  <speed>x  video <s>s  audio <s>s  scene <s>s
total  <s>s  <final.mp4 path>
subtitles  <n> cues  <final.srt path>
transcript  <transcript.md path>
```

- `video`: the scene's length after frozen frames are dropped.
- `scene`: its length in the final video, a whole number of frames at 30 fps. A padded
  scene is longer than its `audio`.
- A scene's `offset` (where it starts in `final.mp4`) is the sum of the earlier scenes'
  `scene` values.

## Length

```
ffprobe -v error -show_entries format=duration -of csv=p=0 demos/<slug>/final.mp4
```

It must match the `total` line to within 1/30 s. Compare against `total`, not the sum of the
narration lengths, because padded scenes run longer than their narration.

## Frames: contact sheet per scene

Sample the part of the scene where its actions play: from `offset`, for
`active` = its `video` value ÷ its speed. ffmpeg won't create `frames/`, so make it first.

```
mkdir -p demos/<slug>/frames
ffmpeg -v error -y -ss <offset> -t <active> -i demos/<slug>/final.mp4 \
  -vf "fps=6/<active>,scale=960:-1,tile=2x3" -frames:v 1 demos/<slug>/frames/<scene-id>.png
```

Look at each sheet with Read. Confirm the page is the scene's page, the cursor sits on each
pointed target, clicks show a ripple, keys show their keycap and Back shows its badge.

## Frames: dense tile around one moment

A ripple lasts about 0.65 s, so six samples often miss it. Before calling one missing, tile
every frame of that second, cropped around the target (`x`, `y` in output-video pixels):

```
ffmpeg -v error -y -ss <time> -t 1 -i demos/<slug>/final.mp4 \
  -vf "crop=480:270:<x-240>:<y-135>,tile=6x5" -frames:v 1 demos/<slug>/frames/<scene-id>-dense.png
```

A link click with no ripple at all means the hold-the-navigation step of the click recipe
was skipped.
