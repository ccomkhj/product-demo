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

**Own voice**:
The user's registered recording, cloned by the qwen3 backend.
_Avoid_: custom voice

**Presenter**:
The round photo bubble in a corner whose halo glows with the voice (spec field `avatar`).
_Avoid_: avatar (outside the field), talking head

**Defaults**:
Saved settings written by configure mode.
_Avoid_: preferences, profile settings
