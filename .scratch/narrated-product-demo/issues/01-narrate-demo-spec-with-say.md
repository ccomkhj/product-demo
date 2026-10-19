# 01: Narrate a demo spec with macOS `say`

**What to build:** Running the TTS CLI on a demo spec writes one 48 kHz mono WAV per scene into the demo folder's audio directory, plus a durations file that maps each scene id to its length in seconds. This works with no API keys, using the macOS `say` backend. It is the first slice of the repo, so it also sets up the project (`uv`, pytest and a `.gitignore` for WAVs and raw recordings). It also adds two pure functions that later tickets share: demo spec validation and TTS backend choice.

Demo spec validation accepts a valid spec and fills in defaults (a 1920×1080 viewport when none is given). It rejects a malformed spec with a clear message: missing scene ids, duplicate ids, unknown action types (the allowed set is `point`, `click`, `dblclick`, `key`, `type`, `scroll`, `back`, `wait`) and empty narration. Backend choice is a pure function of (CLI flag, spec, environment), and the environment is passed in rather than read from the process. Precedence: `--backend` flag → `voice.backend` in the demo spec → auto-detect (`ELEVENLABS_API_KEY` → elevenlabs, else `OPENAI_API_KEY` → openai, else `say`). In this ticket, choosing a backend that isn't built yet fails with a clear "not implemented" error. `voice.voice` picks the `say` voice.

Only the Python stdlib plus ffmpeg/ffprobe may be used. ffmpeg normalises every output to WAV and ffprobe measures the lengths.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

**Before committing:** git puts this folder's repo root at `~`, not at `~/personal`. Ask the user whether `product-demo` should be a nested repo or be ignored in the parent repo. Do not commit until they answer. No AI co-author trailers.

- [ ] `uv run pytest` runs and passes. The `.gitignore` covers raw recordings and WAVs.
- [ ] Spec validation tests: a valid spec is accepted with the viewport default filled in, and each malformed case (missing id, duplicate id, unknown action type, empty narration) is rejected with a message that names the problem and the scene.
- [ ] Backend choice tests cover flag / spec / env combinations, with the env injected: the flag beats the spec, the spec beats the env, ElevenLabs beats OpenAI when both keys are set, and `say` is used when neither is set.
- [ ] The TTS CLI writes one WAV per scene (48 kHz, mono) and a durations file keyed by scene id.
- [ ] Manual smoke test: on a 2-scene spec with the `say` backend, every duration is > 0.
