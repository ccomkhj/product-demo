// Run with: node --test tests/overlay/*.test.mjs
// Drives a real headless Chrome over CDP; skipped when no Chrome is installed.
import assert from "node:assert/strict";
import { after, before, beforeEach, describe, test } from "node:test";
import { chromePath, startBrowser } from "./harness.mjs";

// Overlay elements live in the shadow root of the single [data-demo-overlay] host.
const texts = (sel) =>
  `[...document.querySelector("[data-demo-overlay]").shadowRoot.querySelectorAll(${JSON.stringify(sel)})].map((e) => e.textContent)`;

// CDP modifier bit flags.
const META = 4;
const SHIFT = 8;

const count = (sel) =>
  sel === "[data-demo-overlay]"
    ? `document.querySelectorAll("[data-demo-overlay]").length`
    : `(document.querySelector("[data-demo-overlay]")?.shadowRoot?.querySelectorAll(${JSON.stringify(sel)}).length ?? 0)`;

describe("overlay", { skip: !chromePath && "Chrome not found" }, () => {
  let browser;
  let page;

  before(async () => {
    browser = await startBrowser();
    page = browser.page;
  });
  after(() => browser?.close());
  beforeEach(() => page.goto("/"));

  test("injecting twice leaves one cursor, one root and one set of listeners", async () => {
    await page.inject();
    await page.inject();

    assert.equal(await page.eval(count("[data-demo-overlay]")), 1);
    assert.equal(await page.eval(count('[data-demo="cursor"]')), 1);

    await page.click(360, 220);
    assert.equal(await page.eval(count('[data-demo="ripple"]')), 1);

    await page.key("a", { code: "KeyA", keyCode: 65, text: "a" });
    assert.equal(await page.eval(count('[data-demo="keycap"]')), 1);
  });

  test("key presses show readable keycaps with modifiers", async () => {
    await page.inject();
    await page.key("Meta", { code: "MetaLeft", keyCode: 91, modifiers: META });
    await page.key("k", { code: "KeyK", keyCode: 75, modifiers: META });
    await page.key(" ", { code: "Space", keyCode: 32, text: " " });
    await page.key("Enter", { code: "Enter", keyCode: 13, text: "\r" });
    await page.key("ArrowLeft", { code: "ArrowLeft", keyCode: 37 });
    await page.key("Tab", { code: "Tab", keyCode: 9, modifiers: SHIFT });

    assert.deepEqual(await page.eval(texts('[data-demo="keycap"]')), ["⌘ K", "Space", "Enter", "←", "⇧ Tab"]);
  });

  test("typed text is coalesced into one keycap", async () => {
    await page.slowTimers();
    await page.inject();
    for (const ch of "hi there") {
      await page.key(ch, { text: ch });
    }
    assert.deepEqual(await page.eval(texts('[data-demo="keycap"]')), ["hi there"]);
  });

  test("a click presses the cursor and draws a ripple wide enough to see in a 1080p video", async () => {
    await page.slowTimers();
    await page.inject();
    await page.click(360, 220);
    const pressed = await page.eval(`document.querySelector("[data-demo-overlay]").shadowRoot
      .querySelector('[data-demo="cursor"]').classList.contains("pressed")`);
    assert.equal(pressed, true);

    const later = await page.eval(`new Promise((r) => setTimeout(() => {
      const root = document.querySelector("[data-demo-overlay]").shadowRoot;
      const wave = root.querySelector('[data-demo="wave"]').getBoundingClientRect();
      r({ width: wave.width, pressed: root.querySelector('[data-demo="cursor"]').classList.contains("pressed") });
    }, 450))`);
    assert.ok(later.width > 60, `wave is ${later.width}px wide half-way through`);
    assert.equal(later.pressed, false);
  });

  test("double-click draws a double ring that a single click does not", async () => {
    await page.inject();
    await page.click(360, 220);
    assert.equal(await page.eval(count('[data-demo="double-ring"]')), 0);
    await page.click(360, 220, 2);
    assert.equal(await page.eval(count('[data-demo="double-ring"]')), 1);
  });

  test("point glides the cursor tip to the element centre in about 600 ms", async () => {
    await page.inject();
    const result = await page.eval(`(async () => {
      const t0 = performance.now();
      await __demo.point(document.querySelector("#btn"));
      const elapsed = performance.now() - t0;
      const c = document.querySelector("[data-demo-overlay]").shadowRoot
        .querySelector('[data-demo="cursor"]').getBoundingClientRect();
      return { elapsed, tipX: c.left + 4, tipY: c.top + 2 };
    })()`);
    // #btn is 120x40 at (300, 200): centre (360, 220).
    assert.ok(Math.abs(result.tipX - 360) < 1, `tipX ${result.tipX}`);
    assert.ok(Math.abs(result.tipY - 220) < 1, `tipY ${result.tipY}`);
    assert.ok(result.elapsed > 450 && result.elapsed < 1200, `elapsed ${result.elapsed}`);
  });

  test("badge and caption show their text", async () => {
    await page.inject();
    await page.eval(`__demo.badge("◀ Back")`);
    await page.eval(`__demo.caption("Step 1: sign in")`);
    assert.deepEqual(await page.eval(texts('[data-demo="badge"]')), ["◀ Back"]);
    assert.deepEqual(await page.eval(texts('[data-demo="caption"]')), ["Step 1: sign in"]);
    await page.eval(`__demo.caption("")`);
    assert.equal(await page.eval(count('[data-demo="caption"]')), 0);
  });

  test("upload names one file, or counts several by kind, in a badge", async () => {
    await page.inject();
    const cases = [
      [["/demo/clips/beach.mp4"], "📎 beach.mp4"],
      [["a.mp4", "b.MOV", "c.webm"], "📎 3 videos"],
      [["a.jpg", "b.png"], "📎 2 images"],
      [["a.mp4", "b.jpg"], "📎 2 files"],
      [["data.csv", "notes"], "📎 2 files"],
    ];
    for (const [files, label] of cases) {
      await page.eval(`__demo.upload(${JSON.stringify(files)})`);
      assert.deepEqual(await page.eval(texts('[data-demo="badge"]')), [label]);
    }
  });

  test("the upload badge keeps changing, so mpdecimate keeps it like other badges", async () => {
    await page.inject();
    await page.eval(`__demo.upload(["a.mp4", "b.mp4"])`);
    const timer = await page.eval(`getComputedStyle(document.querySelector("[data-demo-overlay]")
      .shadowRoot.querySelector('[data-demo="badge"]'), "::after").animationName`);
    assert.equal(timer, "timer");
  });

  test("sync flashes the whole viewport magenta and resolves to when it was painted", async () => {
    await page.inject();
    const before = await page.eval(`Date.now()`);
    const pending = page.eval(`__demo.sync().then((t) => ({ t, gone: !document.querySelector("[data-demo-overlay]").shadowRoot.querySelector('[data-demo="sync"]') }))`);
    const during = await page.eval(`(() => {
      const f = document.querySelector("[data-demo-overlay]").shadowRoot.querySelector('[data-demo="sync"]');
      const r = f.getBoundingClientRect();
      // client size, not inner size: a classic (non-overlay) scrollbar is not part of the layer
      const { clientWidth: w, clientHeight: h } = document.documentElement;
      return { color: getComputedStyle(f).backgroundColor, full: r.width === w && r.height === h };
    })()`);
    const result = await pending;
    assert.deepEqual(during, { color: "rgb(255, 0, 255)", full: true });
    assert.ok(result.t >= before && result.t < before + 1000, `painted at ${result.t}, asked at ${before}`);
    assert.equal(result.gone, true);
  });

  test("overlay never intercepts the pointer: a real link click still navigates", async () => {
    await page.inject();
    await page.eval(`__demo.caption("covering caption")`);
    // Hit-testing at the link must find the link, not the overlay.
    const hit = await page.eval(`(() => {
      const r = document.querySelector("#next").getBoundingClientRect();
      return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2).id;
    })()`);
    assert.equal(hit, "next");

    const rect = await page.eval(`(() => {
      const r = document.querySelector("#next").getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    })()`);
    const loaded = page.loaded();
    await page.click(rect.x, rect.y);
    await loaded;
    assert.equal(await page.eval(`location.pathname`), "/second");
  });

  test("phone mode: a tap draws a fingertip and ripple where it lands, and no arrow cursor", async () => {
    await page.slowTimers();
    await page.inject();
    await page.eval(`__demo.device("phone")`);
    await page.click(360, 220);
    const drawn = await page.eval(`(() => {
      const root = document.querySelector("[data-demo-overlay]").shadowRoot;
      const centre = (sel) => { const r = root.querySelector(sel).getBoundingClientRect();
        return [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)]; };
      return { finger: centre('[data-demo="finger"]'), ripple: centre('[data-demo="core"]'),
        cursor: getComputedStyle(root.querySelector('[data-demo="cursor"]')).display };
    })()`);
    assert.deepEqual(drawn, { finger: [360, 220], ripple: [360, 220], cursor: "none" });
  });

  test("phone mode: point hovers a changing fingertip on the target for as long as a glide", async () => {
    await page.inject();
    await page.eval(`__demo.device("phone")`);
    const result = await page.eval(`(async () => {
      const root = document.querySelector("[data-demo-overlay]").shadowRoot;
      const cursorBefore = root.querySelector('[data-demo="cursor"]').style.transform;
      const t0 = performance.now();
      const done = __demo.point("#btn");
      await new Promise((r) => setTimeout(r, 200));
      const f = root.querySelector('[data-demo="finger"]').getBoundingClientRect();
      const animation = getComputedStyle(root.querySelector('[data-demo="finger"]')).animationName;
      await done;
      return { elapsed: performance.now() - t0, centre: [Math.round(f.left + f.width / 2), Math.round(f.top + f.height / 2)],
        animation, cursorMoved: root.querySelector('[data-demo="cursor"]').style.transform !== cursorBefore };
    })()`);
    assert.deepEqual(result.centre, [360, 220]);
    assert.equal(result.animation, "hover");
    assert.equal(result.cursorMoved, false);
    assert.ok(result.elapsed > 450 && result.elapsed < 1200, `elapsed ${result.elapsed}`);
  });

  test("the mode changes on a page that already has the overlay, and back to desktop draws as before", async () => {
    await page.slowTimers();
    await page.inject();
    await page.eval(`__demo.device("phone")`);
    assert.equal(await page.inject(), "overlay already present");
    await page.click(360, 220);
    assert.equal(await page.eval(count('[data-demo="finger"]')), 1);

    await page.eval(`__demo.device("desktop")`);
    await page.click(360, 220);
    const after = await page.eval(`(() => {
      const c = document.querySelector("[data-demo-overlay]").shadowRoot.querySelector('[data-demo="cursor"]');
      return { display: getComputedStyle(c).display, pressed: c.classList.contains("pressed") };
    })()`);
    assert.equal(await page.eval(count('[data-demo="finger"]')), 1);
    assert.equal(await page.eval(count('[data-demo="ripple"]')), 2);
    assert.deepEqual(after, { display: "block", pressed: true });
  });

  test("tap draws a click's ripple on an element without clicking it, as a fingertip in phone mode", async () => {
    await page.slowTimers();
    await page.inject();
    await page.eval(`document.querySelector("#btn").onmousedown = () => { window.pressed = true; }`);
    await page.eval(`__demo.tap("#btn")`);
    assert.equal(await page.eval(count('[data-demo="ripple"]')), 1);
    assert.equal(await page.eval(count('[data-demo="finger"]')), 0);
    await page.eval(`__demo.device("phone")`);
    await page.eval(`__demo.tap(document.querySelector("#btn"))`);
    assert.equal(await page.eval(count('[data-demo="ripple"]')), 2);
    assert.equal(await page.eval(count('[data-demo="finger"]')), 1);
    assert.equal(await page.eval(`window.pressed ?? false`), false);
  });

  test("re-injecting after navigation takes the mode it is given", async () => {
    await page.inject();
    await page.eval(`__demo.device("phone")`);
    await page.goto("/second");
    await page.inject();
    await page.eval(`__demo.device("phone")`);
    const display = await page.eval(`getComputedStyle(document.querySelector("[data-demo-overlay]").shadowRoot
      .querySelector('[data-demo="cursor"]')).display`);
    assert.equal(display, "none");
    await assert.rejects(page.eval(`__demo.device("tablet")`), /unknown mode/);
  });

  test("re-injecting after navigation installs the overlay on the new page", async () => {
    await page.inject();
    await page.goto("/second");
    assert.equal(await page.eval(`typeof window.__demo`), "undefined");
    assert.equal(await page.inject(), "overlay installed");
    assert.equal(await page.inject(), "overlay already present");
    assert.equal(await page.eval(count('[data-demo="cursor"]')), 1);
  });
});
