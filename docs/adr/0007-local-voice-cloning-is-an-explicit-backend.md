# 0007: Local voice cloning is an explicit backend

Status: accepted (2026-10-22).

## Context

The user wants narration resembling their voice without an external speech API on an
M4 Mac with 16 GB RAM. Kokoro's current integration only offers preset voices.

## Decision

Add explicit `voice.backend: qwen3`, with configurable model, local reference audio,
exact transcript, and language. Default to
`mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16`, revision
`1eccf1cb2519b5a4e8a95b5f0544f3303568164f` (Apache-2.0 model card).
Accept that identifier or a local 0.6B Base snapshot. Pin `mlx-audio==0.6.1` in an
isolated uv script requiring Python 3.11+; require native arm64 macOS execution.
The pinned package reports Python >=3.10; its transitive MLX wheels determine the
minimum supported macOS release, checked by installation on the target machine.

Load once per demo and synthesize sequentially. Share normalization and durations with
existing backends. Use `lang_code` in the pinned MLX public generation API. Prefer the
pinned local snapshot without checking the network on a warm run. Initial installation
and model download need network access; speech generation does not upload recordings.

Amended 2026-10-31 (#10): also accept `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16`,
revision `a6eb4f68e4b056f1215157bb696209bc82a6db48`, or its local snapshot; 0.6B stays
the default because its model card scores higher on English speaker similarity.

## Consequences

ADR-0005 remains valid: Kokoro is still the automatic free default. Existing backend
precedence and WAV outputs remain unchanged. Qwen never falls back to an API provider.
Preflight performs bounded local checks without importing MLX or downloading weights.
No model training, service process, or separate voice profile store is introduced.

A personal-voice listening check requires the user's recording and transcript. Speed,
peak memory, and fully disconnected operation must be measured rather than inferred
from model size; validation results are reported separately.

Sources: https://blaizzy.github.io/mlx-audio/models/tts/qwen3-tts/ and the pinned
model's Hugging Face config/model card.

## Implementation validation (2026-10-22)

On the target Apple Silicon machine, MLX Audio 0.6.1 installed successfully and
synthesized two scenes using a macOS `say` reference (not the user's voice).
Cold run including model download: 152.69 seconds for 5.20 seconds of audio.
Warm run: 16.55 seconds for 5.44 seconds of audio, under
`sandbox-exec -p '(version 1)(allow default)(deny network*)'` with `uv run --offline`.
Both runs produced 48 kHz mono PCM16 WAVs with verified durations; local preflight passed.
The warm run completed without an out-of-memory error. `/usr/bin/time -l` reported
668,467,200 bytes maximum resident set size, but this is not a reliable total of the
worker's Metal allocations or system memory pressure; no total-memory claim is made.

`uv run --with numpy pytest -q`: 286 tests passed. NumPy enables the isolated worker's
PCM edge-case tests; ordinary tests skip those four checks when NumPy is unavailable.
Voice resemblance remains unverified until the user supplies a recording and transcript.
