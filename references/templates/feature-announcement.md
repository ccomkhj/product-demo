# Feature announcement

Purpose: `feature-announcement`. Suggested target: **30 seconds**. Use the user's duration
and structure when specified. Copy this target into the spec only when no duration was given.

Ask only for missing context: who already uses the product, what changed, the practical
benefit, the action to demonstrate, its URL, and any login requirement.

Suggested beats (scene count is flexible):

1. Show the problem or starting state in one sentence.
2. Demonstrate the new action with narration anchored to it using `at`.
3. Show the visible result and explain its benefit.
4. Give the viewer one next step.

Example outline for CSV export: Reports page → choose the requested reporting period →
export → show the actual completion state → invite the viewer to try their own report.
Confirm the period selector and completion state exist before writing them as facts.

Write plain, short narration. Avoid invented usage numbers, speed claims, or sales metrics.
Use observed entry states as `prerequisites` and visible results as `expected_outcomes`.
A button click alone does not prove a download completed. If there is no observable
completion state, describe what can be verified and ask for missing context.
Produce a normal `demo.json`; never insert placeholder URLs or targets into a runnable spec.
