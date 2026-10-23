# 0008: Reuse the registered own_voice before recording

Status: accepted (2026-10-22), amended by ADR-0009 (2026-10-23).

## Context

The user wants their existing personal recording reused without another question. If
no usable recording is registered, the agent should guide them through recording once.
This extends ADR-0007's initial per-demo configuration with one repository-local profile.

## Decision

Store the `own_voice` entry in the per-user `~/.config/product-demo/config.json` (honouring `XDG_CONFIG_HOME`; formerly the ignored in-repo `.voices/`, copied from once), with a local WAV beside it.
Demos select `voice.profile: own_voice`; absent an explicit backend this selects Qwen
before API-key auto-detection. CLI and explicit spec backends retain precedence.
Profile paths resolve against the registry; per-demo model/language overrides are retained.

`scripts/own_voice.py --status` checks the profile and reference audio without prompting.
Normal invocation exits immediately when ready; otherwise it serves a token-protected
loopback recording page and opens the browser. Only the user starts microphone capture.
They preview the recording and verify its transcript before saving. Save converts audio
locally with ffmpeg, checks duration and signal level, atomically registers the result,
and shuts down the recorder. A valid existing profile is never replaced by this flow.

## Consequences

Future demos reuse the voice without another recording or reuse-approval question.
Recording still needs browser microphone permission and the user's participation.
No audio is uploaded to an external service. The local registry and recordings are
excluded from Git. A broken or absent registration invokes setup rather than paid TTS.
Basic validation cannot prove that the transcript is correct or that the voice resembles
the user; the recording page asks the user to check what they said.

## Amendment (2026-10-23)

When no voice is saved and `own_voice` is missing, the agent asks one question: record now,
or use the free Kokoro voice. Choosing Kokoro saves it as the default voice (ADR-0009), so
the question is not asked again. Paid backends are still never chosen silently.
