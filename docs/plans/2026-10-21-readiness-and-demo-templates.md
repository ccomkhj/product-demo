# Recording Readiness and Demo Templates Implementation Plan

> **Execution:** Use the installed `executing-plans` skill to implement this plan task by task.

**Goal:** Help users choose an effective demo structure and catch recording blockers before capturing a take.

**Architecture:** Keep `demo.json` as the source of truth. Add optional purpose and scene-check fields, a stdlib-only local preflight CLI, and three writing templates consumed by the existing agent workflow. Browser readiness and outcome checks run through the existing Chrome DevTools MCP session; the Python CLI must not pretend it can inspect that session.

**Tech Stack:** Python 3.11+, pytest, existing ffmpeg/ffprobe commands, Markdown templates, and the existing Chrome DevTools MCP recording recipes.

---

## Scope and current behavior

This plan covers recommendation 5 (recording readiness) and recommendation 7 (purpose-based templates). It does not implement a preview UI, scene replacement, privacy masks, new TTS backends, automatic publishing, or a browser automation framework.

Relevant existing files:

- `SKILL.md`: interview, script approval, narration, manual login, recording, composition, verification.
- `scripts/demo_spec.py`: spec validation and shared voice-backend selection.
- `scripts/tts.py`: produces per-scene WAV files and `audio/durations.json`.
- `scripts/compose.py`: subtitle capability detection and final composition.
- `scripts/narration.py`: current narration parsing abstraction; preserve it.
- `references/mcp-recipes.md`: live MCP navigation, target resolution, recording and markers.
- `references/demo-spec.md`: public spec contract.
- `references/verify.md`: existing output verification.

The working tree contains ongoing changes to these areas. Before implementation, inspect the current diff and preserve that work. Do not apply this plan against a presumed clean historical version. The planning change itself adds only this file.

Retain the existing ADR decisions: narration anchors, sync flash, burned subtitles by default with warning fallback, and existing backend precedence. New checks do not change those defaults.

## Intended user experience

Example request: “Make a 30-second announcement for CSV export.”

1. Select the feature-announcement template from the request. Ask only for missing audience, outcome, pages/actions, and authentication details.
2. Draft a short sequence showing the problem, feature, visible result, and next step. Show scene narration and the duration target for the existing script approval.
3. Run local readiness checks before generating narration. Identify the chosen voice backend and actionable blockers.
4. Confirm browser tooling and page access in the recording browser. Complete existing manual sign-in if required. Do not rehearse state-changing actions.
5. Generate narration and compare its measured length with the target. If it is substantially too long, propose a shorter script and obtain the existing script approval for changed wording.
6. Check each scene's prerequisites immediately before its start marker; check its expected result after its actions.
7. If a check fails, stop the take cleanly and explain the scene and failed condition. Do not hand off that take as a completed demo.

Templates are optional guidance. Existing custom demos remain valid.

## Data contracts

Add these optional top-level fields:

```json
{
  "purpose": "feature-announcement",
  "outcome": "Show existing customers how to export last month's report",
  "target_duration_s": 30
}
```

- `purpose`: `feature-announcement`, `customer-tutorial`, `sales-walkthrough`, or `custom`; absent means custom behavior.
- `outcome`: non-empty string when supplied; expresses what the viewer should understand or accomplish.
- `target_duration_s`: finite positive number, excluding booleans. This is an editorial target, not a rendering limit.
- Do not silently add a duration default to old specs. The agent copies the selected template's suggested target only if the user has not supplied one.

Add optional scene fields:

```json
{
  "prerequisites": [
    {"type": "visible_text", "text": "Reports"},
    {"type": "visible_target", "target": "the enabled Export button"}
  ],
  "expected_outcomes": [
    {"type": "visible_text", "text": "Export ready"}
  ]
}
```

- Both fields are lists; empty lists are valid.
- A check is either `visible_text` with non-empty `text`, or `visible_target` with a non-empty natural-language `target`.
- Reject unknown types, missing payloads, non-object entries, and non-list containers with scene and check-position context.
- Check targets use fresh snapshots, just like actions. Do not persist browser element UIDs.
- A visible-target check means the agent can identify the described visible element unambiguously, including any explicitly described enabled/disabled state. Ambiguity is unresolved, never a pass.
- Do not add arbitrary JavaScript, selectors, network requests, or filesystem assertions to this initial check language.
- Existing `login` remains the authentication hint; a loaded page alone never proves the user is signed in.

## Task 1: Extend the optional spec contract

**Files**

- Modify: `scripts/demo_spec.py`
- Modify: `tests/test_demo_spec.py`
- Modify: `references/demo-spec.md`

**Steps**

1. Add tests accepting all four purposes, omitted metadata, a positive duration, and both scene-check types.
2. Add parameterized rejection cases: unknown purpose, blank outcome, zero/negative/boolean/non-finite duration, invalid lists, unknown check types, and empty check payloads.
3. Run `uv run pytest tests/test_demo_spec.py -q`; confirm new cases fail for the missing behavior.
4. Add small validation helpers for optional metadata and scene-check lists, called by `validate_spec`. Continue returning a deep copy and preserve narration validation through the current `Narration` abstraction.
5. Document fields and one complete example in `references/demo-spec.md`. Existing sample specs must remain accepted without edits.
6. Run `uv run pytest tests/test_demo_spec.py tests/test_demo_spec_reference.py -q`; expect all tests to pass.

**Acceptance:** An old spec retains its existing behavior; malformed new fields fail before TTS or recording.

**Suggested commit:** `feat: add demo purpose and scene readiness contracts`

## Task 2: Add local preflight checks

**Files**

- Create: `scripts/preflight.py`
- Create: `tests/test_preflight.py`
- Create: `tests/test_preflight_cli.py`
- Modify if needed: `scripts/compose.py` only to reuse subtitle capability detection without changing behavior

**CLI contract**

```bash
python3 scripts/preflight.py demos/export/demo.json
python3 scripts/preflight.py demos/export/demo.json --json
python3 scripts/preflight.py demos/export/demo.json --backend kokoro
python3 scripts/preflight.py demos/export/demo.json --burn-subs
python3 scripts/preflight.py demos/export/demo.json --no-burn-subs
```

Human output contains one status and fix per check. JSON output has `schema_version: 1`, `scope: "local"`, `ready`, `selected_backend`, and a `checks` list. Each check has `id`, `status` (`pass`, `warning`, `fail`), `message`, and optional `remedy`. Exit 0 means no local blockers, 1 means blockers, and 2 is argparse usage failure. Invalid specs are reported as blockers, including in JSON mode.

Checks:

| Check | Behavior |
|---|---|
| Spec | Reuse `load_spec`; do not duplicate validation. |
| ffmpeg and ffprobe | Require both executables; use bounded version probes to catch broken executables. |
| Output location | Create and remove a temporary probe file beside the spec. Never overwrite demo assets. |
| Voice backend | Reuse `choose_backend` with the same precedence as TTS. |
| Paid backend | Require the selected backend's non-blank key; never print its value. Key presence does not prove remote validity or credit availability. |
| Kokoro | Require `uv`; report that first use may install dependencies/download models. Do not launch synthesis or download during preflight. |
| macOS say | Require `say`; if a voice is specified, verify it against the local voice listing. |
| Subtitles | Reuse existing libass/rsvg capability rules. Missing renderers warn by default, fail for explicit `--burn-subs`, and do not block `--no-burn-subs`. |
| Browser scope | State clearly that browser access, login, and capture capability still require the MCP workflow. Local success is not permission to skip those checks. |

**Steps**

1. Add unit cases for missing binaries, timeout/nonzero probes, unwritable output, backend precedence, blank credentials, and all three subtitle policies.
2. Define a small `CheckResult` dataclass and `run_checks(spec_path, backend=None, burn_subs=None)` boundary. Inject or monkeypatch executable lookup and process probes in tests; pass argv arrays with subprocess timeouts.
3. Implement checks and deterministic text/JSON formatting. Catch expected spec, filesystem, and subprocess errors; a routine failure must not produce a traceback.
4. Add CLI tests asserting exit codes, parseable JSON, actionable remedies, and absence of a sentinel secret from stdout/stderr.
5. Prove tests make no HTTP requests and invoke no TTS synthesis/model installation. Missing dependencies must not trigger installation automatically.
6. Run `uv run pytest tests/test_preflight.py tests/test_preflight_cli.py tests/test_backend_choice.py tests/test_compose_cli.py -q`; expect all tests to pass.

**Acceptance:** Missing ffmpeg or a required key is found before narration; unavailable optional subtitle rendering follows the existing ADR fallback.

**Suggested commit:** `feat: add local recording preflight command`

## Task 3: Integrate browser readiness and scene outcomes

**Files**

- Modify: `SKILL.md`
- Modify: `references/mcp-recipes.md`
- Modify: `references/verify.md`
- Create: `references/readiness.md`

**Steps**

1. Insert local preflight after spec approval and before TTS. Pass the same explicit backend/subtitle choices to subsequent commands.
2. Move browser connection and login readiness before TTS. Confirm required MCP capabilities are available, including screencast start/stop, navigation, snapshots, and input. Tool discovery is a capability check, not proof that capture will succeed.
3. Inspect independently accessible scene entry pages in the same browser session. For state-dependent scenes, defer page checks until the scene can be reached naturally; do not mark them passed or navigate away from required setup state.
4. Check the actual viewport with the existing resize/aspect-ratio recipe. Restore the first scene's entry state before recording. Keep one-tab capture and overlay reinjection rules.
5. For `login: true`, retain manual sign-in and confirmation. Verify the resulting page against its prerequisites; never collect credentials or infer successful login solely from navigation success.
6. Document check evaluation: use fresh snapshots and visible-element evidence, wait up to 10 seconds for asynchronous content, and treat timeout/ambiguous matches as failed or unresolved. These are agent-executed checks, not a new Python browser runner.
7. Immediately before each scene's start marker, evaluate `prerequisites`. Evaluate `expected_outcomes` after the last action but before the end marker. Intermediate states belong in separate scenes in this version.
8. On failure during recording, call `screencast_stop`, retain diagnostic artifacts, and report the scene/condition. Do not compose or deliver the incomplete take. Retry through the existing full recording workflow after correction; scene-only retry is outside this plan.
9. Write a short per-demo `readiness.md` report recording local results, browser observations, deferred checks, and scene outcomes. Include condition IDs/scene IDs and brief evidence, not copied page content, cookies, or credentials. Refresh checks each run; the report is evidence, not a reusable authorization token.
10. Add the browser acceptance walkthrough below to `references/readiness.md`; update final verification to ensure expected outcomes passed.

**Acceptance walkthrough**

Use a local fixture page with a Reports heading, an Export button, and a click handler that reveals “Export ready.” Serve it on localhost from a temporary directory.

- With the button present, the prerequisite passes; clicking reveals the outcome and the scene completes.
- With the button removed, readiness stops before recording that scene.
- With the click handler disabled, outcome verification stops the active take and no final artifact is delivered.
- With delayed content, a condition appearing within the timeout passes; a longer delay reports a bounded failure.
- A state-dependent second scene is deferred during initial inspection and checked upon actual entry.
- In a user-provided authenticated test app, an expired session triggers the existing sign-in handoff; after login, rerun the page check.

**Acceptance:** The workflow distinguishes “local tools ready,” “browser entry ready,” and “scene result verified” without claiming that a shell command inspected an authenticated browser.

**Suggested commit:** `feat: check browser readiness and scene outcomes`

## Task 4: Add three purpose-based writing templates

**Files**

- Create: `references/templates/feature-announcement.md`
- Create: `references/templates/customer-tutorial.md`
- Create: `references/templates/sales-walkthrough.md`
- Modify: `SKILL.md`
- Modify: `references/demo-spec.md`

**Template definitions**

| Template | Default target | Suggested sequence | Required context |
|---|---:|---|---|
| Feature announcement | 30 seconds | Problem → new action → visible result → next step | Existing audience, what changed, benefit, action to demonstrate |
| Customer tutorial | 90 seconds | Goal and prerequisites → steps → success confirmation | User task, starting state, required access, what success looks like |
| Sales walkthrough | 120 seconds | Buyer problem → relevant workflow → result → next step | Buyer role, use case, supported value proposition, desired next step |

**Steps**

1. Write each template with its purpose, default duration, missing-information prompts, suggested scene beats, and a short example outline. Scene counts remain flexible.
2. Include guidance to describe demonstrated outcomes, avoid unsupported claims, anchor narrated actions with `at`, and derive prerequisites/outcomes only from states that can actually be observed.
3. Update the interview to infer the purpose when explicit (“announcement”, “tutorial”, “sales demo”). If unclear, ask one concise purpose question alongside other missing details; allow `custom` without forcing a template.
4. Make explicit user duration and structure override template defaults. A request for a 60-second announcement must retain 60 seconds.
5. Read only the chosen template. Produce a normal `demo.json`; do not introduce a second template engine or placeholder-filled runnable specs.
6. Include purpose, audience, outcome, target duration, scene titles, and narration in the existing approval presentation. Template choice itself requires no separate approval round.
7. Manually walk through three sample requests and one custom request. Verify each draft is a valid spec and does not invent missing URLs, UI controls, or sales metrics.

**Acceptance:** A user can request an announcement, tutorial, or sales walkthrough without designing the script structure; existing bespoke walkthroughs still work.

**Suggested commit:** `feat: add purpose-based demo script templates`

## Task 5: Check duration targets after narration

**Files**

- Modify: `scripts/preflight.py`
- Modify: `tests/test_preflight.py`
- Modify: `tests/test_preflight_cli.py`
- Modify: `SKILL.md`
- Modify: `references/readiness.md`

**CLI extension**

```bash
python3 scripts/preflight.py demos/export/demo.json --durations demos/export/audio/durations.json
```

**Steps**

1. Add an optional `--durations` input for the existing TTS duration artifact. Without it, do not guess a precise running time from word count.
2. Validate that every scene has a finite positive numeric duration, excluding booleans. Report missing or malformed values as blockers; ignore extra entries for scenes outside this spec.
3. When a target is present, sum durations for current scenes and warn if narration alone exceeds the target by more than 10%. Keep the warning non-blocking and label it “narration duration”; final video can be longer because composition pads scenes.
4. When no target exists, report measured narration duration without imposing template defaults. Never auto-truncate narration or speed up speech to satisfy a template.
5. Update the skill to run the duration check after TTS, propose shortening if needed, and reuse the existing script approval if wording changes. Re-run TTS after approved changes.
6. Test exact target, the 110% boundary, just above the boundary, missing target, missing scene duration, malformed input, and non-finite/boolean values.
7. Run `uv run pytest tests/test_preflight.py tests/test_preflight_cli.py -q`; expect all cases to pass.

**Acceptance:** A 30-second announcement with 45 seconds of generated speech is flagged before recording, with a clear editing recommendation rather than an unsupported promise of a 30-second final video.

**Suggested commit:** `feat: warn when demo narration exceeds duration target`

## Task 6: Documentation and end-to-end acceptance

**Files**

- Modify: `README.md`
- Modify: `references/readiness.md`
- Modify: `references/verify.md`

**Steps**

1. Add one request example for each template and document the preflight CLI, its local-only scope, optional scene checks, and duration warnings.
2. Run `uv run pytest`. Report environment-dependent skips explicitly; do not interpret skipped overlay/model tests as verified behavior.
3. Run the local browser fixture walkthrough from Task 3, including both failure cases, through the actual MCP workflow. Record observed results and any environment blockers.
4. Generate a short feature-announcement demo from that fixture using an available backend. Verify the spec passes, the duration check reports correctly, and the final MP4/SRT/transcript still satisfy existing verification recipes.
5. Check an existing sample spec still validates and that its composition behavior was not changed by optional metadata. Do not regenerate or overwrite checked-in sample videos solely for this feature.
6. Review the diff for unintended changes to backend precedence, subtitle fallback, narration timing, and sync-flash behavior. Commit only this feature's changes, respecting existing user work and the repository's no-AI-coauthor rule.

**Suggested commit:** `docs: explain demo templates and recording readiness`

## Completion criteria

- Three templates are available and selected through the existing interview.
- Existing specs remain compatible; optional fields are validated and documented.
- Local preflight identifies missing dependencies, output failures, and selected-backend configuration issues with actionable messages and stable exit codes.
- Browser access and authentication are checked in the actual recording session, with deferred state-dependent checks handled honestly.
- Prerequisite and outcome failures prevent delivery of an invalid take.
- Measured narration exceeding the target produces a useful warning before capture.
- Existing automated checks pass, and the browser acceptance results are recorded with any unverified cases identified.

## Delivery order and review checkpoints

Implement Tasks 1–3 first for recording reliability, then Tasks 4–5 for guided scripts, then Task 6 for integrated verification. Review after each commit-sized task. No implementation, recording, or publishing is authorized merely by writing this plan.

## Implementation verification — 2026-10-21

Implemented Tasks 1–5 and the documentation/regression portions of Task 6. Browser checks
are instructions executed by the recording agent; the local CLI does not enforce browser
outcomes or certify an authenticated session.

- Baseline: 156 tests passed before implementation.
- Final automated suite: `uv run pytest -q -rs` — **238 passed**, no pytest skips.
- Direct overlay suite: `node --test tests/overlay/overlay.test.mjs` — **10 passed**, no
  skips, using headless Chrome outside the sandbox. A sandboxed direct run was interrupted
  after failing to progress; the permitted rerun passed.
- Existing sample: validates without adding a duration target. Real local preflight passes
  with Kokoro selected and the rsvg subtitle fallback available; browser/model caveats are
  reported as warnings.
- Template draft checks: announcement with an explicit 60-second override, 90-second
  tutorial, 120-second sales walkthrough, and custom without a target all validate.
  These checks verify schema compatibility, not an end-to-end agent interview.
- Composition regression: both legacy specs and specs carrying purpose/readiness metadata
  produce the expected H.264 video, audio, subtitles and transcript using synthetic media.
- System Python here is 3.9; the new CLI reports the Python 3.11+ requirement in text/JSON
  without a traceback. `uv run python` uses the supported project runtime.
- Live MCP acceptance remains **unverified**: this session exposes no Chrome DevTools MCP
  tools, and no authenticated test app was provided. The fixture page and reproducible
  walkthrough are in `tests/fixtures/readiness.html` and `references/readiness.md`.
  No fixture-based narrated browser capture or authentication claim is made.

Changes are left uncommitted alongside the user's existing edits, which the implementation
depends on. Committing the combined work would also include unrelated changes.
