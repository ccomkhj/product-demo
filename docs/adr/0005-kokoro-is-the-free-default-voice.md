# 0005: Kokoro is the free default voice

Status: accepted (2026-10-19).

## Context

Without an API key, narration came from macOS `say`, which sounds robotic and makes the
demo sound cheap. The paid OpenAI and ElevenLabs voices sound natural but cost money per
demo. Kokoro-82M is an open-weight (Apache-2.0) model that runs on the CPU, about four
times faster than real time on an M4, and sounds close to the paid voices.

## Decision

Add a `kokoro` backend. Auto-detect picks it after the API keys and before `say`, when `uv`
is on PATH. `scripts/kokoro_tts.py` declares its dependency (`kokoro-onnx`) inline, and
`tts.py` runs it with `uv run --script`, so `tts.py` stays stdlib only. The model files are
downloaded once into `~/.cache/product-demo/kokoro` and checked against pinned SHA-256
digests.

## Consequences

- The first Kokoro run downloads about 340 MB, and uv installs onnxruntime into its cache.
- Machines without `uv` still fall back to `say`, as before.
- English voices are the main target. The Spanish, French, Hindi, Italian and Portuguese
  voices also work. The Japanese and Mandarin voices are rejected, because kokoro-onnx
  doesn't bundle their phonemizers.
- The Kokoro tests skip until the model is cached, so the suite never starts a download.
