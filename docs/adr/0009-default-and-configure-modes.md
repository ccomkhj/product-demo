# 0009: Default and configure modes

Status: accepted (2026-10-23).

## Context

The skill should be quick to run and still configurable. Asking about voice, presenter and
subtitles on every demo slows the common case. Without a way to ask, though, those
settings can't be changed.

## Decision

`/product-demo` runs in default mode: it asks no setting questions. The gates stay: missing
pages, script approval, manual sign-in, and the missing own voice choice from ADR-0008.
`/product-demo --configure` asks every setting with AskUserQuestion, records the voice if
chosen, and saves the answers under `defaults` in the ignored `.voices/config.json`. The
voice and photo are registered in the same file.

`own_voice.py --status` reports `defaults`, so default mode still needs one call.
`--save-defaults` merges top-level fields (`voice`, `avatar`, `viewport`, `burn_subs`) after
validating them as a demo spec would. `avatar: false` means no presenter even when a photo
is saved. Each setting is resolved in this order: the user's request, then `defaults`, then
the built-in fallback.

## Consequences

Purpose and length are per demo and are never saved. Pronunciations stay in the demo spec.
The defaults are copied into each `demo.json`, so a spec still describes its demo on its
own. Changing them later affects only new demos.
