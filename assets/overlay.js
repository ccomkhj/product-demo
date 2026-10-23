/*
 * product-demo visible-interaction overlay.
 *
 * Inject with Chrome DevTools MCP as a classic script:
 *   evaluate_script(pageId, sourcePath="<skill>/assets/overlay.js", format="script")
 * or, when the path is outside the MCP workspace roots, pass this whole file inline:
 *   evaluate_script(pageId, function="<this file>", format="script")
 * Re-inject after EVERY navigation. Injecting again on the same document is a
 * no-op apart from re-attaching the overlay host if the page removed it.
 *
 * API (single global):
 *   __demo.point(el | selector)  glide the fake cursor to the element centre (~600 ms); resolves when done
 *   __demo.badge(text)           transient badge, e.g. "◀ Back"; resolves once it has been on screen a moment
 *   __demo.caption(text)         lower-third caption; caption("") or caption(null) hides it
 *   __demo.sync()                full-screen magenta flash (~400 ms) that compose finds in the
 *                                recording; resolves to the wall-clock ms at which it was painted
 * Clicks, double-clicks and key presses (real CDP input from MCP) are drawn
 * automatically by capture-phase listeners: ripple, double ring, keycap.
 *
 * Every overlay element is pointer-events:none inside a shadow root on a
 * max-z-index host, raised into the top layer above open dialogs; listeners
 * are passive and never cancel or stop events.
 */
(() => {
  const VERSION = 4;
  const existing = window.__demo;
  if (existing && existing.version === VERSION) {
    existing._ensureHost();
    return "overlay already present";
  }

  const Z = "2147483647";
  const GLIDE_MS = 600;
  const POS_KEY = "__demo_cursor_pos";

  const CSS = `
    :host { all: initial; }
    .layer { position: fixed; inset: 0; pointer-events: none; overflow: hidden;
      font: 600 15px/1.2 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
    .layer * { pointer-events: none; box-sizing: border-box; }
    .cursor { position: absolute; left: 0; top: 0; width: 28px; height: 28px;
      transform: translate(-100px, -100px); will-change: transform;
      filter: drop-shadow(0 2px 3px rgba(0,0,0,.45)); }
    .cursor svg { display: block; width: 100%; height: 100%;
      transform-origin: 4px 2px; transition: transform 120ms ease-out; }
    /* A click squeezes the arrow towards its tip, like a finger pressing a button. */
    .cursor.pressed svg { transform: scale(.78); }
    /* A held dot plus two expanding waves (~120 px), red with a white edge so it reads on
       light and dark pages alike, and lasting long enough to survive a 1.5x speed-up. */
    .ripple { position: absolute; width: 0; height: 0; }
    .ripple i { position: absolute; left: -12px; top: -12px; width: 24px; height: 24px; border-radius: 50%; }
    .ripple .core { background: rgba(255, 59, 48, .6); border: 2px solid #fff;
      box-shadow: 0 0 0 2px rgba(255, 59, 48, .95), 0 2px 6px rgba(0,0,0,.35);
      animation: core 900ms ease-out forwards; }
    @keyframes core { 0% { transform: scale(.4); opacity: 1; } 15%, 60% { transform: scale(1); opacity: 1; }
      100% { transform: scale(.8); opacity: 0; } }
    .ripple .wave { border: 3px solid rgba(255, 59, 48, .95); opacity: 0;
      box-shadow: 0 0 0 1px rgba(255,255,255,.8), inset 0 0 0 1px rgba(255,255,255,.8);
      animation: wave 900ms cubic-bezier(.2, .6, .3, 1) forwards; }
    .ripple .wave + .wave { animation-delay: 180ms; }
    @keyframes wave { from { transform: scale(.5); opacity: 1; } to { transform: scale(5); opacity: 0; } }
    .double-ring { position: absolute; width: 0; height: 0; }
    .double-ring i { position: absolute; left: -12px; top: -12px; width: 24px; height: 24px; border-radius: 50%;
      border: 3px solid rgba(255, 196, 0, 1); box-shadow: 0 0 0 2px rgba(0,0,0,.25);
      animation: ring 800ms ease-out forwards; }
    .double-ring i + i { animation-delay: 140ms; opacity: 0; }
    @keyframes ring { 0% { transform: scale(.5); opacity: 1; } 100% { transform: scale(3.6); opacity: 0; } }
    /* Bottom-right: clear of the lower-third caption (bottom-left) and the Back badge (top-left). */
    .keys { position: absolute; right: 32px; bottom: 40px;
      display: flex; flex-direction: column-reverse; align-items: flex-end; gap: 8px; }
    .keycap, .badge { position: relative; overflow: hidden; color: #fff; background: rgba(20, 20, 24, .88);
      border: 1px solid rgba(255,255,255,.25); border-radius: 10px; box-shadow: 0 6px 18px rgba(0,0,0,.35);
      animation: pop 160ms ease-out; white-space: pre; }
    .keycap { padding: 10px 18px 12px; font-size: 26px; letter-spacing: .02em; min-width: 54px; text-align: center; }
    .badge { position: absolute; left: 24px; top: 24px; padding: 10px 18px 12px; font-size: 22px; }
    /* A shrinking timer bar keeps every frame different, so ffmpeg mpdecimate keeps the badge on screen. */
    .keycap::after, .badge::after { content: ""; position: absolute; left: 0; bottom: 0; height: 4px; width: 100%;
      background: #4da3ff; transform-origin: left; animation: timer var(--life, 1500ms) linear forwards; }
    @keyframes timer { from { transform: scaleX(1); } to { transform: scaleX(0); } }
    @keyframes pop { from { transform: scale(.85); opacity: 0; } to { transform: scale(1); opacity: 1; } }
    .fade { transition: opacity 250ms ease; opacity: 0 !important; }
    /* Pure magenta: a colour web pages rarely fill the viewport with, so compose can find it. */
    .sync { position: absolute; inset: 0; background: #ff00ff; }
    .caption { position: absolute; left: 48px; bottom: 48px; max-width: 70%; padding: 14px 22px;
      color: #fff; background: rgba(20, 20, 24, .85); border-left: 6px solid #4da3ff; border-radius: 6px;
      font-size: 28px; line-height: 1.25; white-space: pre-wrap; animation: slide 300ms ease-out; }
    @keyframes slide { from { transform: translateY(16px); opacity: 0; } to { transform: none; opacity: 1; } }
  `;

  const CURSOR_SVG =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 28 28">' +
    '<path d="M4 2 L4 23 L9.5 17.8 L13.3 26 L17 24.4 L13.3 16.4 L21 16.4 Z" ' +
    'fill="#fff" stroke="#000" stroke-width="1.6" stroke-linejoin="round"/></svg>';

  let host, layer, cursorEl, keysEl, captionEl;
  let cursorPos = loadPos();
  let typingRun = null; // {el, text, timer} while consecutive printable keys are coalesced

  function loadPos() {
    try {
      const p = JSON.parse(sessionStorage.getItem(POS_KEY));
      if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) return p;
    } catch (_) {}
    return { x: innerWidth / 2, y: innerHeight / 2 };
  }
  function savePos() {
    try { sessionStorage.setItem(POS_KEY, JSON.stringify(cursorPos)); } catch (_) {}
  }

  function el(tag, cls, parent) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (cls) e.setAttribute("data-demo", cls);
    if (parent) parent.appendChild(e);
    return e;
  }

  function buildHost() {
    host = document.createElement("demo-overlay");
    host.setAttribute("data-demo-overlay", "");
    host.setAttribute("aria-hidden", "true");
    host.popover = "manual";
    const s = host.style;
    s.setProperty("all", "initial", "important");
    s.setProperty("position", "fixed", "important");
    s.setProperty("inset", "0", "important");
    s.setProperty("pointer-events", "none", "important");
    s.setProperty("z-index", Z, "important");
    s.setProperty("display", "block", "important");
    const root = host.attachShadow({ mode: "open" });
    // Constructable stylesheet: not subject to the page's CSP style-src.
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(CSS);
    root.adoptedStyleSheets = [sheet];
    layer = el("div", "layer", root);
    layer.removeAttribute("data-demo");
    keysEl = el("div", "keys", layer);
    cursorEl = el("div", "cursor", layer);
    // Static, trusted markup; built via DOMParser to stay clear of Trusted Types innerHTML sinks.
    const svg = new DOMParser().parseFromString(CURSOR_SVG, "image/svg+xml").documentElement;
    cursorEl.appendChild(document.importNode(svg, true));
    placeCursor(cursorPos.x, cursorPos.y);
  }

  function ensureHost() {
    if (!host) buildHost();
    const parent = document.documentElement;
    if (host.parentNode !== parent || parent.lastElementChild !== host) parent.appendChild(host);
    // An open modal <dialog> sits in the top layer, above any z-index; re-entering it keeps us on top.
    if (host.showPopover) {
      try { host.hidePopover(); host.showPopover(); } catch (_) {}
    }
    return layer;
  }

  function placeCursor(x, y) {
    // Arrow tip sits at (4, 2) inside the 28px box.
    cursorEl.style.transform = `translate(${x - 4}px, ${y - 2}px)`;
  }

  let glide = null;
  function glideTo(x, y, ms) {
    ensureHost();
    if (glide) glide.cancel();
    const from = { ...cursorPos };
    cursorPos = { x, y };
    savePos();
    return new Promise((resolve) => {
      if (!ms) { placeCursor(x, y); glide = null; resolve(); return; }
      const t0 = performance.now();
      let raf = 0;
      let done = false;
      const finish = () => { if (!done) { done = true; placeCursor(x, y); resolve(); } };
      const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
      const step = (now) => {
        const t = Math.min(1, (now - t0) / ms);
        const k = ease(t);
        placeCursor(from.x + (x - from.x) * k, from.y + (y - from.y) * k);
        if (t < 1) raf = requestAnimationFrame(step); else finish();
      };
      raf = requestAnimationFrame(step);
      // rAF is throttled in background tabs; never leave the caller hanging.
      const fallback = setTimeout(finish, ms + 300);
      glide = { cancel() { cancelAnimationFrame(raf); clearTimeout(fallback); finish(); } };
    });
  }

  function transient(node, life) {
    node.style.setProperty("--life", life + "ms");
    setTimeout(() => node.classList.add("fade"), life);
    setTimeout(() => node.remove(), life + 300);
  }

  function resolveTarget(target) {
    if (typeof target === "string") target = document.querySelector(target);
    if (!target || typeof target.getBoundingClientRect !== "function") {
      throw new Error("__demo.point: element not found");
    }
    return target;
  }

  function point(target, ms = GLIDE_MS) {
    const node = resolveTarget(target);
    let r = node.getBoundingClientRect();
    const visible = r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
    if (!visible) {
      node.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
      r = node.getBoundingClientRect();
    }
    return glideTo(r.left + r.width / 2, r.top + r.height / 2, ms);
  }

  function badge(text, life = 1500) {
    const layerEl = ensureHost();
    for (const old of layerEl.querySelectorAll(".badge")) old.remove();
    const b = el("div", "badge", layerEl);
    b.textContent = String(text);
    transient(b, life);
    // Resolve after the badge has been visible long enough to be recorded,
    // so `await __demo.badge(...)` before a navigation still shows it.
    return new Promise((resolve) => setTimeout(resolve, Math.min(life, 700)));
  }

  function caption(text) {
    const layerEl = ensureHost();
    if (captionEl) { captionEl.remove(); captionEl = null; }
    if (text == null || text === "") return;
    captionEl = el("div", "caption", layerEl);
    captionEl.textContent = String(text);
  }

  function sync(life = 400) {
    const layerEl = ensureHost();
    const flash = el("div", "sync", layerEl);
    return new Promise((resolve) => {
      // The first callback runs before the frame that paints the flash, the second
      // once it has been painted: that is the moment the recording shows it.
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const paintedAt = Date.now();
        setTimeout(() => { flash.remove(); resolve(paintedAt); }, life);
      }));
    });
  }

  // ---- automatic visuals for real input events ----

  const NAMED = {
    " ": "Space", Enter: "Enter", Tab: "Tab", Escape: "Esc", Backspace: "⌫", Delete: "Del",
    ArrowLeft: "←", ArrowRight: "→", ArrowUp: "↑", ArrowDown: "↓",
    PageUp: "PgUp", PageDown: "PgDn", Home: "Home", End: "End",
  };
  const MODIFIER_KEYS = new Set(["Meta", "Control", "Alt", "Shift", "CapsLock", "Fn", "OS", "AltGraph"]);

  function keyLabel(e) {
    let name = NAMED[e.key] ?? e.key;
    if (name.length === 1) name = name.toUpperCase();
    const printable = e.key.length === 1;
    const mods = [];
    if (e.ctrlKey) mods.push("⌃");
    if (e.altKey) mods.push("⌥");
    // Shift is implicit in a printed character ("A", "?"), so only show it for chords and named keys.
    if (e.shiftKey && (!printable || e.ctrlKey || e.altKey || e.metaKey || e.key === " ")) mods.push("⇧");
    if (e.metaKey) mods.push("⌘");
    return [...mods, name].join(" ");
  }

  function showKeycap(text, life = 1500) {
    ensureHost();
    const k = el("div", "keycap", keysEl);
    k.textContent = text;
    transient(k, life);
    while (keysEl.childElementCount > 6) keysEl.firstElementChild.remove();
    return k;
  }

  function onKeydown(e) {
    if (e.repeat || MODIFIER_KEYS.has(e.key) || e.isComposing) return;
    const plainChar = e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey;
    // Typed text is coalesced into one keycap ("hello world") instead of a keycap per letter.
    if (plainChar && typingRun && typingRun.el.isConnected) {
      typingRun.text += e.key;
      typingRun.el.textContent = typingRun.text;
      clearTimeout(typingRun.timer);
      typingRun.timer = setTimeout(() => { typingRun = null; }, 1000);
      return;
    }
    if (plainChar && e.key !== " ") {
      const k = showKeycap(e.key, 1800);
      typingRun = { el: k, text: e.key, timer: setTimeout(() => { typingRun = null; }, 1000) };
      return;
    }
    typingRun = null;
    showKeycap(keyLabel(e));
  }

  function onMousedown(e) {
    if (!e.isTrusted) return;
    const layerEl = ensureHost();
    glideTo(e.clientX, e.clientY, 0);
    press();
    // Under the cursor, so the arrow stays visible on top of the click.
    const r = layerEl.insertBefore(el("div", "ripple"), cursorEl);
    r.style.left = e.clientX + "px";
    r.style.top = e.clientY + "px";
    el("i", "core", r);
    el("i", "wave", r);
    el("i", "wave", r);
    setTimeout(() => r.remove(), 1200);
  }

  // CDP sends mousedown and mouseup back to back, so the press is timed rather than held
  // until mouseup: otherwise it would never reach a recorded frame.
  let pressTimer = 0;
  function press() {
    cursorEl.classList.add("pressed");
    clearTimeout(pressTimer);
    pressTimer = setTimeout(() => cursorEl.classList.remove("pressed"), 180);
  }

  function onDblclick(e) {
    if (!e.isTrusted) return;
    const layerEl = ensureHost();
    const d = layerEl.insertBefore(el("div", "double-ring"), cursorEl);
    d.style.left = e.clientX + "px";
    d.style.top = e.clientY + "px";
    el("i", null, d);
    el("i", null, d);
    setTimeout(() => d.remove(), 1000);
  }

  const opts = { capture: true, passive: true };
  window.addEventListener("mousedown", onMousedown, opts);
  window.addEventListener("dblclick", onDblclick, opts);
  window.addEventListener("keydown", onKeydown, opts);

  const api = { version: VERSION, point, badge, caption, sync, _ensureHost: ensureHost };
  Object.defineProperty(window, "__demo", { value: Object.freeze(api), configurable: true, writable: false, enumerable: false });

  if (document.documentElement) ensureHost();
  else document.addEventListener("DOMContentLoaded", ensureHost, { once: true });
  return "overlay installed";
})();
