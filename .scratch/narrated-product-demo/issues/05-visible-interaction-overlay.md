# 05: Visible-interaction overlay and MCP recipes

**What to build:** A browser overlay script that the agent injects into the page during recording, so that every interaction is visible to viewers. It also adds the MCP recipes reference: the exact Chrome DevTools MCP call sequences the agent follows on every run.

**Overlay:** an idempotent script that is guarded and exposed as a single global `__demo`. Injecting it again (which happens after every navigation) breaks nothing and does not duplicate listeners or elements.
- `__demo.point(el)` glides a fake cursor to the element's centre in about 600 ms.
- `__demo.badge(text)` shows a transient badge, for example "◀ Back".
- `__demo.caption(text)` shows an optional lower-third caption.
- Capture-phase listeners on `mousedown`, `dblclick` and `keydown` draw these on their own (MCP sends real CDP input events): a ripple for a click, a visibly different double ring for a double-click, and keycap badges for key presses. Keycaps include modifiers ("⌘ K") and readable names for named keys ("Space", "Enter", "←").
- Every overlay element uses `pointer-events: none` and the highest z-index, and never changes the page's own behaviour.

**MCP recipes reference:** the call sequences for `point`, `click`, `dblclick`, `key`, `type`, `scroll`, `back` and `wait`, plus overlay injection and screencast start/stop:
- Call `__demo.point` on the target before every click.
- Call `__demo.badge("◀ Back")` before `navigate_page(type=back)`.
- Re-inject the overlay with `evaluate_script` after **every** navigation, because an init script only applies to the next navigation.
- Stay in a single tab, because the screencast is tied to one page.
- Element targets come from the demo spec as natural-language descriptions. They are resolved to snapshot uids at record time, because uids are not stable across runs.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Injecting the overlay twice into the same page leaves exactly one cursor and one set of listeners.
- [ ] Manual check through the Chrome DevTools MCP on example.com: after a `point` + click, a key press with a modifier, a Space press and a Back badge, a screenshot shows the cursor, ripple, keycap and badge.
- [ ] Clicking a real link with the overlay present still navigates (the overlay doesn't block anything).
- [ ] The MCP recipes reference documents every action type plus injection, re-injection, and screencast start/stop, in the form the agent will follow literally.
