# 02: OpenAI and ElevenLabs narration backends

**What to build:** When `OPENAI_API_KEY` or `ELEVENLABS_API_KEY` is set, or the backend is forced with `--backend` or `voice.backend`, the TTS CLI narrates the demo spec with that service. The output is the same as the `say` path: one 48 kHz mono WAV per scene plus the durations file. OpenAI uses `gpt-4o-mini-tts`. `voice.voice` picks the voice on either service. HTTP calls use `urllib` only, and ffmpeg normalises the returned audio to WAV, so nothing downstream can tell which backend produced it.

**Blocked by:** 01 (Narrate a demo spec with macOS `say`)

**Status:** ready-for-agent

- [ ] Choosing `openai` or `elevenlabs` (by flag, spec or env auto-detect) produces WAVs and a durations file in the same format as `say`.
- [ ] The HTTP transport can be swapped out in tests. Tests check that each backend sends the configured voice and the scene's narration, and that a non-2xx response gives a clear error naming the backend and the scene.
- [ ] A missing API key for a forced backend fails with a clear message before any request is sent.
- [ ] No third-party dependencies are added.
- [ ] Manual smoke test, run only if a key is present: a 2-scene spec gives durations > 0 for every scene.
