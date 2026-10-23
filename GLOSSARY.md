# Product demo

Turns an interview into a narrated, subtitled screen recording of a web app, recorded live
by an agent and edited to fit its voice.

## Language

### Spec

**Demo spec**:
The `demo.json` holding a demo's purpose, settings and ordered scenes.
_Avoid_: config, script file

**Scene**:
One page-sized part of a demo with its own narration and actions.
_Avoid_: step, slide, segment

**Action**:
One browser interaction in a scene: click, double-click, key, type, scroll or back.

**At phrase**:
Words in a scene's narration that an action starts on.
_Avoid_: cue, trigger

**Purpose**:
Feature announcement, customer tutorial, sales walkthrough or custom; picks the template.

**Prerequisites / Expected outcomes**:
Observable entry and success states checked before and after a scene.

**Script approval**:
The user's sign-off on the scene titles and narration before any audio or recording.

### Recording

**Take**:
One recording attempt: `raw.mp4` plus its timeline.

**Timeline**:
The marks (`rec_start`, scene start and end, beats) linking a take to the spec.

**Beat**:
A mark right before an action that has an at phrase.

**Sync flash**:
The magenta flash that lines the timeline up with video time.

**Overlay**:
The injected layer that draws the cursor, ripples, keycaps and badges.
_Avoid_: HUD, annotations

**Readiness**:
Local preflight plus browser checks before recording, recorded in `readiness.md`.

### Edit and voice

**Compose**:
Cutting each scene from the take, dropping frozen frames, fitting it to its narration and
burning in subtitles.

**Fit**:
How compose matches a scene to its narration: hold, speed-up or pad.

**Edit map**:
The `<slug>.editmap.json` compose writes next to the video: total length, each scene's
start and end, its actions, with the start of those compose knows, and whether the
subtitles are burned in. Post-processing's only link to the demo's structure.

**Click sound**:
The short sound post-processing plays one cursor glide after each click, double-click and
field click (type) whose start is in the edit map.

**Post recipe**:
The `post.json` in a demo folder: one optional key per post-processing operation (trim,
speed, exports, ...), times against the source video or a scene.
_Avoid_: edit config, post config

**Post plan**:
What post-processing builds from the post recipe, the source's length, cues and edit map:
the time map, retimed cues, warnings and the steps in their fixed order.

**Time map**:
Source time to output time: the kept ranges after trim and cuts, then the speed, then a
title card's offset.

**Own voice**:
The user's registered recording, cloned by the qwen3 backend.
_Avoid_: custom voice

**Presenter**:
The round photo bubble in a corner whose halo glows with the voice (spec field `avatar`).
_Avoid_: avatar (outside the field), talking head

**Defaults**:
Saved settings written by configure mode.
_Avoid_: preferences, profile settings
