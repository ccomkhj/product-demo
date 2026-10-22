# Configurable Local Voice Cloning Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
> In this workspace, the available equivalent is `executing-plans`; use that skill when implementing.

**Goal:** Generate demo narration resembling the user's voice entirely on an M4 Mac with 16 GB RAM, using a configurable Qwen3-TTS 0.6B backend and a local reference recording.

**Architecture:** Extend the existing `voice` object in `demo.json` and backend selection rather than introduce a separate configuration system. Run MLX Audio in an isolated `uv` script, loading the model once per demo and synthesizing scenes sequentially. Keep WAV normalization and duration measurement in the existing TTS pipeline.

**Tech Stack:** Python 3.11+, uv inline script dependencies, MLX Audio, Qwen3-TTS 12Hz 0.6B Base bf16, ffmpeg/ffprobe, pytest.

---

## Configuration contract

```json
{
  "voice": {
    "backend": "qwen3",
    "model": "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16",
    "reference_audio": "voice/reference.wav",
    "reference_text": "The exact words spoken in the reference recording.",
    "language": "English"
  }
}
```

- `backend` selects the engine; `model` selects a compatible model within that engine. `voice.voice` continues to mean a preset voice name or provider voice ID for existing backends.
- For `qwen3`, omitted `model` defaults to the identifier above; omitted `language` defaults to `English`. Require nonblank `reference_audio` and `reference_text`. Reject a nonempty preset `voice` for Qwen Base with an explanation.
- Initially support the named 0.6B Base model and a local snapshot directory of that model. Resolve relative model directories and reference recordings against the directory containing `demo.json`, independent of the working directory. Do not imply support for arbitrary MLX models, CustomVoice, or VoiceDesign. Additional compatible models can be added to a small explicit supported-model list later.
- Preserve selection precedence: CLI `--backend`, then `voice.backend`, then current auto-detection. Qwen is explicitly selected, never auto-detected. Explicit Qwen selection must stay local even when paid API keys exist; failure must not fall back to a remote provider.
- Validate field types while loading the spec. Validate required Qwen fields and file accessibility after choosing the effective backend, so `--backend qwen3` is checked and overrides to other engines do not require a recording.
- Keep reference files on disk; no upload or automatic transcription. Dependencies/model weights need an initial download; subsequent generation must work with the network disconnected and caches populated.
- Keep existing preset-only specs working. Do not add global profiles, a configuration UI, model training, or a background server in this change.

## Current implementation and decision compatibility

`scripts/demo_spec.py` owns `BACKENDS` and selection precedence. `scripts/tts.py` dispatches one scene at a time through `SYNTHESIZERS`, then writes 48 kHz mono PCM16 WAVs and `durations.json`. `scripts/kokoro_tts.py` demonstrates the isolated dependency pattern. `scripts/preflight.py` performs local checks without installing dependencies or downloading weights.

ADR-0005 remains valid: Kokoro stays the free default. Add an ADR for opt-in local cloning rather than replacing that decision. No changes to recording, subtitle timing, or composition are required.

## Task 1: Define and validate voice configuration

**Files:** Modify `scripts/demo_spec.py`; create `scripts/voice_config.py`; modify `tests/test_demo_spec.py` and `tests/test_backend_choice.py`; create `tests/test_voice_config.py`.

1. Add `qwen3` to `BACKENDS`. Put the default model, supported model IDs, field defaults, and effective-backend configuration resolver in `voice_config.py` so preflight and synthesis share them. Keep this module stdlib-only and avoid circular imports.
2. Add structural validation for the `voice` object and string-valued fields. Preserve omitted configuration and existing defaults; explicitly cover existing null/empty voice behavior before tightening validation.
3. Implement `resolve_voice_config(voice, backend, spec_dir)` returning a normalized dictionary. Resolve filesystem paths without mutating the loaded spec; do not download or import MLX. Keep actual audio decoding in synthesis/preflight.
4. Add behavioral tests for defaults, malformed values, missing clone fields, unsupported model IDs, missing local directories, paths containing spaces, absolute paths, and relative paths with a different working directory.
5. Test CLI/spec/environment precedence, including paid keys plus explicit Qwen. Retain the registry parity test; complete its Qwen registration in Task 3 before the full suite.

Run: `uv run pytest tests/test_demo_spec.py tests/test_voice_config.py -q`

Expected: all selected tests pass without models, internet, or Apple hardware.

## Task 2: Implement the isolated local model worker

**Files:** Create `scripts/qwen3_tts.py` and `tests/test_qwen3_tts.py`.

1. Confirm the current MLX Audio release supports the documented Qwen Base call below; pin the working dependency version in PEP 723 metadata. Record the tested model snapshot revision in the new ADR. Verify the selected revision's license and required Python/macOS versions from its model card and package metadata.
2. Give the worker `--request PATH --out-dir DIR`. The JSON request contains normalized voice configuration and an ordered list of scene IDs/texts. Use generated numeric filenames for temporary raw output and a small manifest mapping scene IDs to files; do not interpolate text into shell commands.
3. Check native Apple Silicon execution before importing MLX; report an actionable error on unsupported platforms or an x86 Python running under Rosetta.
4. Load the model once, then process scenes sequentially. Use the documented interface as the starting point:

   ```python
   from mlx_audio.tts.utils import load_model

   model = load_model(config["model"])
   for scene in scenes:
       results = model.generate(
           text=scene["narration"],
           ref_audio=config["reference_audio"],
           ref_text=config["reference_text"],
           language=config["language"],
       )
       # Consume every result in order, preserving the model's sample rate.
       # Write a raw WAV for this scene before advancing to the next scene.
   ```

5. Confirm result/sample-rate fields against the pinned version. Handle every returned audio chunk, not only the first. Release each scene's samples after writing; avoid keeping all demo audio in memory. Reject empty/nonfinite output and report the failing scene ID.
6. For repository model IDs, resolve/download the pinned snapshot once and pass its local path to the loader. Support fully cached offline runs and an explicit local snapshot path; missing offline assets must produce a clear error. Do not execute remote model code.
7. Test the worker's orchestration with a fake loader/generator: one load for multiple scenes, transcript/language forwarding, multiple chunks, empty audio, and a scene-specific model failure. Keep heavyweight imports deferred so these tests run without MLX installed.

Run: `uv run pytest tests/test_qwen3_tts.py -q`

Expected: orchestration tests pass with no downloads or GPU requirement.

## Task 3: Integrate demo-wide synthesis

**Files:** Modify `scripts/tts.py`; modify `tests/test_tts_cli.py`, `tests/test_backend_choice.py`, and `tests/test_http_backends.py` where signatures change.

1. Pass the spec directory from `main()` into configuration resolution. Preserve compatibility for direct `generate()` callers, using an optional `spec_dir` parameter if needed.
2. Adapt the synthesizer boundary to accept an ordered scene collection and return raw paths. Wrap the existing single-scene backends in one small adapter; Qwen invokes its worker once for the entire demo. Keep one registry and one normalization/duration loop rather than duplicating the pipeline for Qwen.
3. Pass the request via a temporary JSON file and invoke `uv run --quiet --script scripts/qwen3_tts.py` with argument lists using the existing process helper. Clean temporary files on success and failure.
4. Decode/probe the reference recording before expensive model loading, reject empty or invalid audio, and normalize a temporary copy if the pinned model requires it. Keep the original recording untouched.
5. Retain the public outputs: one 48 kHz mono PCM16 WAV per scene, measured durations rounded as today, and unchanged CLI reporting. Write `durations.json` only after all scenes succeed.
6. Test two-scene orchestration using a fake worker that writes valid raw WAVs. Assert one worker launch, correct output format/durations, reference resolution from another working directory, no HTTP calls, and useful failure reporting. Run existing HTTP/preset regression tests.

Run: `uv run pytest tests/test_backend_choice.py tests/test_tts_cli.py tests/test_http_backends.py tests/test_voice_config.py tests/test_qwen3_tts.py -q`

Expected: all available tests pass; existing platform/model-dependent skips remain explicit.

## Task 4: Extend local preflight

**Files:** Modify `scripts/preflight.py`, `tests/test_preflight.py`, `tests/test_preflight_cli.py`, and `references/readiness.md`.

1. Add an explicit Qwen branch checking uv, native macOS arm64 execution, and shared effective voice configuration. Continue the existing ffmpeg/ffprobe checks.
2. Check readable, decodable, nonempty reference audio with bounded ffprobe. Validate a configured local model directory without importing MLX. For remote model IDs, warn that first synthesis may download weights/dependencies and that cache completeness is verified by synthesis.
3. Preserve preflight's no-download/no-install/no-synthesis behavior and JSON report format. Do not claim model readiness from the presence of uv alone.
4. Test unsupported architecture, absent uv, missing/unreadable/invalid reference audio, missing transcript, explicit Qwen with API keys, and successful configuration. Assert that neither the model worker nor network transport is invoked.

Run: `uv run pytest tests/test_preflight.py tests/test_preflight_cli.py -q`

Expected: local checks report actionable failures and setup warnings without contacting any service.

## Task 5: Document configuration and the recording workflow

**Files:** Modify `README.md`, `references/demo-spec.md`, `SKILL.md`, and `.gitignore` if necessary; create `docs/adr/0007-local-voice-cloning-is-an-explicit-backend.md`; update `tests/test_demo_spec_reference.py` if documentation assertions require it.

1. Document the configuration example above, defaults, allowed model forms, effective-backend precedence, and path resolution. Explain that preset voices and reference-based cloning have different inputs.
2. Document recording approximately 10–20 seconds of clean speech, entering an exact transcript, and listening to a short preview before rendering the demo. Keep the user's voice file in an ignored local directory; use placeholders in committed examples.
3. Update the skill workflow and backend lists so future demo generation respects the selected voice configuration and reuses the supplied recording. Do not auto-select a paid backend when Qwen fails.
4. Add the ADR explaining the M4/16 GB target, 0.6B default, isolated dependency choice, one model load per demo, dependency/model revision pins, and compatibility with ADR-0005.
5. Document setup versus offline operation, cache locations discovered during the smoke run, and how to configure a local model snapshot. Avoid unmeasured speed or fidelity promises.

Run: `uv run pytest tests/test_demo_spec_reference.py -q`

Expected: documented examples remain consistent with validation.

## Task 6: Verify on the target Mac

**Files:** Create an ignored local demo at `demos/voice-clone-smoke/demo.json`; record measured results in the new ADR or implementation PR.

1. Obtain the user's reference WAV and exact transcript. This is the only required user input for the personal-voice listening check; implementation and fake-worker tests do not depend on it.
2. Create two short scenes using the configuration above, then run:

   ```bash
   uv run python scripts/preflight.py demos/voice-clone-smoke/demo.json --backend qwen3
   /usr/bin/time -l uv run python scripts/tts.py demos/voice-clone-smoke/demo.json --backend qwen3
   ```

3. Record cold setup/download separately from warm generation time, total generated audio duration, peak process memory, and observed system memory pressure. On the 16 GB machine, verify completion without out-of-memory errors; do not substitute a benchmark from another model or machine.
4. Repeat the warmed run with networking disabled, writing to a different output directory. Offline flags alone are not proof of no network use. Confirm both outputs exist, their durations match ffprobe within 0.05 seconds, and audio is 48 kHz mono PCM16.
5. Have the user listen for resemblance, accent, pronunciation, and consistency across scenes. Record this as a subjective acceptance result, separate from automated checks. Do not claim voice fidelity from file-format tests.
6. Run `uv run pytest -q` and report any environment-specific skips or failures. Fix regressions in existing backends before completion.

## Completion criteria

- A demo selects Qwen and its model/reference inputs entirely through `voice` configuration.
- Default Qwen selection uses the 0.6B Base model, with one model load per demo.
- Explicit local generation never calls a paid speech API or uploads the recording.
- Two scenes synthesize successfully on the user's M4/16 GB Mac, including a warmed run with networking disabled.
- Existing narration output contracts, preset configurations, and selection precedence remain valid.
- Preflight and ordinary tests neither install nor download models.
- Personal voice similarity has a recorded listening result; hardware speed and memory claims have measured evidence.

## Sources and implementation checkpoints

- [MLX Audio Qwen3-TTS documentation](https://blaizzy.github.io/mlx-audio/models/tts/qwen3-tts/): documents the 0.6B Base identifier and `generate(ref_audio=..., ref_text=...)`; Base has no preset speakers.
- [MLX Audio voice cloning guide](https://blaizzy.github.io/mlx-audio/guides/voice-cloning/): reference audio and transcript workflow.
- Repository sources: `scripts/demo_spec.py`, `scripts/tts.py`, `scripts/kokoro_tts.py`, `scripts/preflight.py`, and ADR-0005.

Dependency versions, snapshot revision, exact loader cache behavior, and hardware measurements must be verified during implementation; this plan does not invent tested values.

## Implementation status (2026-10-22)

Tasks 1–5 implemented. Configuration and worker tests are consolidated in
`tests/test_qwen3_tts.py`. Task 6 completed using a synthetic macOS reference for hardware,
format, duration, and OS-enforced offline checks; personal-voice listening acceptance
awaits the user's recording and exact transcript. See ADR-0007 for measured results.
The implementation uses the pinned library's `lang_code` argument rather than the
illustrative `language` keyword above. No commits or publishing were performed.
