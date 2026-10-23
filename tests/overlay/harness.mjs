// Minimal headless-Chrome harness for overlay tests. No npm dependencies:
// spawns Chrome with a DevTools port, talks CDP over Node's built-in WebSocket,
// and serves fixture pages from a local HTTP server.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

const CHROME_CANDIDATES = [
  process.env.CHROME_PATH,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
].filter(Boolean);

export const chromePath = CHROME_CANDIDATES.find((p) => existsSync(p));

export const OVERLAY_SOURCE = readFileSync(
  new URL("../../assets/overlay.js", import.meta.url),
  "utf8",
);

const PAGES = {
  "/": `<!doctype html><html><body style="margin:0;font:16px sans-serif">
    <h1>Fixture</h1>
    <a id="next" href="/second" style="display:inline-block;padding:20px">Go to second</a>
    <button id="btn" style="position:absolute;left:300px;top:200px;width:120px;height:40px">Button</button>
    <div style="height:3000px"></div>
  </body></html>`,
  "/second": `<!doctype html><html><body><h1 id="second">Second page</h1></body></html>`,
};

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = [];
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
      } else if (msg.method) {
        for (const l of this.listeners) l(msg);
      }
    });
  }
  send(method, params = {}) {
    const id = this.nextId++;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
  once(method) {
    return new Promise((resolve) => {
      const l = (msg) => {
        if (msg.method === method) {
          this.listeners = this.listeners.filter((x) => x !== l);
          resolve(msg.params);
        }
      };
      this.listeners.push(l);
    });
  }
}

export async function startBrowser() {
  const server = createServer((req, res) => {
    const body = PAGES[req.url];
    res.writeHead(body ? 200 : 404, { "content-type": "text/html" });
    res.end(body ?? "not found");
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const origin = `http://127.0.0.1:${server.address().port}`;

  const profile = mkdtempSync(join(tmpdir(), "overlay-test-"));
  const proc = spawn(
    chromePath,
    [
      "--headless=new",
      "--remote-debugging-port=0",
      `--user-data-dir=${profile}`,
      "--no-first-run",
      "--no-default-browser-check",
      "--window-size=1280,800",
      "about:blank",
    ],
    { stdio: "ignore" },
  );

  let ws;
  async function close() {
    ws?.close();
    proc.kill("SIGKILL");
    server.close();
    await sleep(200);
    rmSync(profile, { recursive: true, force: true });
  }

  // A cold Chrome on a CI runner can take over 10 s to open its DevTools port.
  const portFile = join(profile, "DevToolsActivePort");
  for (let i = 0; i < 300 && !existsSync(portFile); i++) await sleep(100);
  let cdp;
  try {
    const port = readFileSync(portFile, "utf8").split("\n")[0];
    const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    const pageTarget = targets.find((t) => t.type === "page");
    ws = new WebSocket(pageTarget.webSocketDebuggerUrl);
    await new Promise((r, j) => {
      ws.addEventListener("open", r, { once: true });
      ws.addEventListener("error", j, { once: true });
    });
    cdp = new Cdp(ws);
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
  } catch (err) {
    // Leave nothing running, or node waits on Chrome and the server instead of exiting.
    await close();
    throw err;
  }

  const page = {
    origin,
    async goto(path) {
      const loaded = cdp.once("Page.loadEventFired");
      await cdp.send("Page.navigate", { url: origin + path });
      await loaded;
    },
    async eval(expression) {
      const r = await cdp.send("Runtime.evaluate", {
        expression,
        awaitPromise: true,
        returnByValue: true,
      });
      if (r.exceptionDetails) {
        throw new Error(r.exceptionDetails.exception?.description ?? r.exceptionDetails.text);
      }
      return r.result.value;
    },
    inject() {
      return this.eval(OVERLAY_SOURCE);
    },
    // Stretch the page's timers so slow CDP round trips (CI runners) cannot outlast
    // short-lived overlay state; their order, and so what the test checks, is unchanged.
    slowTimers(factor = 10) {
      return this.eval(`{ const st = setTimeout;
        window.setTimeout = (f, ms = 0, ...a) => st(f, ms * ${factor}, ...a); }`);
    },
    async click(x, y, clickCount = 1) {
      for (let n = 1; n <= clickCount; n++) {
        const base = { x, y, button: "left", clickCount: n };
        await cdp.send("Input.dispatchMouseEvent", { type: "mousePressed", ...base });
        await cdp.send("Input.dispatchMouseEvent", { type: "mouseReleased", ...base });
      }
    },
    async key(key, { code = key, keyCode = 0, modifiers = 0, text } = {}) {
      const base = { key, code, windowsVirtualKeyCode: keyCode, modifiers };
      await cdp.send("Input.dispatchKeyEvent", { type: text ? "keyDown" : "rawKeyDown", text, ...base });
      await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
    },
    loaded() {
      return cdp.once("Page.loadEventFired");
    },
  };

  return { page, close };
}
