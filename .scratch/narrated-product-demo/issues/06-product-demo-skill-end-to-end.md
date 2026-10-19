# 06: `product-demo` skill, end to end

**What to build:** The `product-demo` Claude Code skill, which turns a short interview into a finished, narrated demo video, installed with a single symlink into the Claude skills directory. The workflow runs: interview → demo spec → user approves the transcript → narration audio (TTS) → live recording → compose → verify.

The interview asks for the product, the audience and an ordered list of pages. For each page it asks for the URL, what to do there, the key message, and whether the page needs a login. If it does, the agent pauses so the user can sign in by hand in the MCP browser. The profile persists, so there is no auth code. Each demo lives in its own per-demo folder under a slug.

Recording protocol:
1. `resize_page` to the viewport.
2. Navigate to the first URL and inject the overlay.
3. Start the screencast, writing into the demo folder, and mark `rec_start`.
4. For each scene: mark start → run the actions using the recipes → stay until the time on screen roughly covers that scene's narration length → mark end.
5. Stop the screencast.

The verify step checks the final length against the total narration length. It also extracts one frame per scene and looks at each frame, to confirm the overlay and page state are right before handing the video over.

SKILL.md stays short and focused on *why*. Schemas and call sequences go in the references: the demo spec reference (schema + one worked example) and the MCP recipes reference from 05.

**Blocked by:** 01 (Narrate a demo spec with macOS `say`), 03 (Compose a fitted video from a recording and timeline), 04 (Subtitles, transcript and burn-in), 05 (Visible-interaction overlay and MCP recipes)

**Status:** resolved

- [x] SKILL.md covers each workflow step and why it's there: record-then-edit, audio first, re-inject after every navigation, single tab, and natural-language targets resolved at record time.
- [x] The demo spec reference documents the top-level fields (`title`, `product`, `audience`, `viewport`, `voice`, `scenes[]`), the scene fields and the action types, with one worked example that validates.
- [x] The skill is installed by a single symlink, so edits in the project folder take effect at once.
- [x] The workflow supports re-running only compose, and re-running TTS + compose after a narration edit.
- [x] End-to-end smoke run: a 3-scene demo on a public site (example.com → link click → Back, plus a Space press on a scrollable page). The final length matches the planned total, and the extracted frame for each scene shows the cursor, ripple and key badge where expected.

## Comments

2026-10-19: Resolved on `narrated-product-demo` (merge dee31b7). Symlink ~/.claude/skills/product-demo installed. E2E run: planned 15.42 s, final 15.40 s; after the review fixes it is frame-exact at 15.400 s. The Space scene uses iana.org/domains/reserved, because example.com no longer scrolls.
