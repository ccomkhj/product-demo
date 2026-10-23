# Verifying a composed demo

Recipes for step 7 of SKILL.md. Run them from the directory holding `demos/`.

## Compose output

`compose.py` prints one tab-separated line per scene, each followed by one line per `at`
action in it, then the totals:

```
<id>  <hold|speedup|pad>  <speed>x  video <s>s  audio <s>s  scene <s>s
at  <id>  <n>  <s>s  "<phrase>"  [late <s>s]
sync  flash at <s>s in the raw video
total  <s>s  <final.mp4 path>
subtitles  <n> cues  <final.srt path>  <burned in (subtitles|overlay)|not burned in>
transcript  <transcript.md path>
```

- `video`: the scene's length after frozen frames are dropped and the holds before its
  `at` phrases are added.
- `scene`: its length in the final video, a whole number of frames at 30 fps. A padded
  scene is longer than its `audio`.
- `at`: the scene's `n`th action with an `at` phrase, and when it starts in the final video
  (the scene's offset plus its place after the holds and the scene's speed). The phrase
  sits where it falls inside its sentence, so this is often seconds after its cue starts.
  `late` means an earlier segment ran past the phrase and pushed the action back by that
  much; mark the beats closer to their actions or move the phrase later.
- `sync`: present when `rec_start` has a `sync_ms`; where compose found the flash.
- A scene's `offset` (where it starts in `final.mp4`) is the sum of the earlier scenes'
  `scene` values.

## Length

```
ffprobe -v error -show_entries format=duration -of csv=p=0 demos/<slug>/final.mp4
```

It must match the `total` line to within 1/30 s. Compare against `total`, not the sum of the
narration lengths, because padded scenes run longer than their narration.

## Frames: contact sheet per scene

`at` phrases spread a scene's actions across its narration, so sample the whole scene:
from `offset`, for its `scene` value. ffmpeg won't create `frames/`, so make it first.

```
mkdir -p demos/<slug>/frames
ffmpeg -v error -y -ss <offset> -t <scene> -i demos/<slug>/final.mp4 \
  -vf "fps=8/<scene>,scale=960:-1,tile=2x4:padding=12:color=red" -frames:v 1 \
  demos/<slug>/frames/<scene-id>.png
```

Look at each sheet with Read. Confirm the page is the scene's page, the cursor sits on each
pointed target, clicks show a ripple, keys show their keycap, Back shows its badge and the
subtitle is legible at the bottom. Keep the red padding: consecutive frames of a scroll
line up across a bare tile seam and look like one torn frame.

## Sync

For each `at` line of the compose output, grab a frame 0.3 s after its time:

```
ffmpeg -v error -y -ss <at time + 0.3> -i demos/<slug>/final.mp4 -frames:v 1 \
  -vf scale=960:-1 demos/<slug>/frames/<scene-id>-sync-<n>.png
```

The action must be under way there: the cursor gliding to or resting on its target, the
scroll moving, the ripple playing. If it is already over, its beat was marked after the
action had started.

## Frames: dense tile around one moment

A ripple lasts about 0.9 s, so eight samples often miss it. Before calling one missing, tile
every frame of that second, cropped around the target (`x`, `y` in output-video pixels):

```
ffmpeg -v error -y -ss <time> -t 1 -i demos/<slug>/final.mp4 \
  -vf "crop=480:270:<x-240>:<y-135>,tile=6x5:padding=4:color=red" -frames:v 1 \
  demos/<slug>/frames/<scene-id>-dense.png
```

A link click with no ripple at all means the hold-the-navigation step of the click recipe
was skipped.

## Scene readiness and outcomes

Before delivering a new take, read `demos/<slug>/readiness.md`: all declared prerequisites
and expected outcomes must have passed for this take. Deferred, ambiguous, timed-out, or
failed checks prevent delivery. No checks declared means “not declared,” not proof of an
outcome. Existing demos without these optional fields remain compatible with the original
visual checks. Do not deliver an old `final.mp4` as the result of a failed retry.

Compare final measured duration with `target_duration_s` if present, and report the actual
duration. The pre-recording narration check is an editorial warning, not a guarantee about
final video length. Preserve the existing duration, frame and sync verification above.
