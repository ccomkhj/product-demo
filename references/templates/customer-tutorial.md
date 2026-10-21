# Customer tutorial

Purpose: `customer-tutorial`. Suggested target: **90 seconds**. Explicit user duration
and structure take precedence; use this target only when none was supplied.

Ask only for missing context: viewer's task, starting page and state, required access,
ordered steps, and how the viewer recognizes success. Confirm URLs and login requirements.

Suggested beats (scene count is flexible):

1. State what the viewer will accomplish and show the starting state.
2. Explain prerequisites without recording credentials or login steps.
3. Demonstrate one meaningful step at a time, anchoring narrated actions with `at`.
4. Show the final state and explain how to recognize success.

Example outline for CSV export: open Reports → select the required period → export →
confirm the product's visible result. Include only controls and results confirmed in the
actual product; do not fabricate an “Export ready” toast.

Keep narration instructional and concrete. Use scene `prerequisites` for visible starting
conditions and `expected_outcomes` for visible success. Split intermediate states into
separate scenes when they need their own checks. Do not infer a completed background job
from a loading indicator. Produce a normal `demo.json` with real URLs and natural-language
targets, and present the script for the existing approval.
