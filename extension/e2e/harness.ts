// Real Chromium with the real built extension, talking to a real demo core. Only Twitch's
// page is a fixture: its DOM is copied from a live probe, including the React fiber that
// carries each message's ID.

import { chromium, type BrowserContext, type Page } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
export const EXTENSION = resolve(here, "../dist");
const CORE = resolve(here, "../../core");

export const CHANNEL = "official_h4ck3r";

export interface Core {
  port: number;
  proc: ChildProcess;
  api(method: string, path: string, body?: unknown): Promise<any>;
  stop(): Promise<void>;
}

export async function startCore(port: number, args: string[] = []): Promise<Core> {
  const proc = spawn(
    "uv",
    ["run", "python", "-m", "motwi.sim.demo", "--port", String(port), "--empty", ...args],
    { cwd: CORE, stdio: "pipe", shell: process.platform === "win32" },
  );
  await new Promise<void>((ok, fail) => {
    const timer = setTimeout(() => fail(new Error("demo core didn't start")), 40_000);
    proc.stdout!.on("data", (d: Buffer) => {
      if (d.toString().includes("Motwi demo:")) {
        clearTimeout(timer);
        ok();
      }
    });
    proc.stderr!.on("data", () => {});
    proc.on("exit", () => fail(new Error("demo core exited")));
  });
  return {
    port,
    proc,
    async api(method, path, body) {
      const resp = await fetch(`http://127.0.0.1:${port}/api${path}`, {
        method,
        headers: { Authorization: "Bearer demo", "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!resp.ok) throw new Error(`${method} ${path}: ${resp.status} ${await resp.text()}`);
      return resp.json();
    },
    async stop() {
      if (process.platform === "win32" && proc.pid) {
        spawn("taskkill", ["/pid", String(proc.pid), "/t", "/f"], { stdio: "ignore" });
      } else {
        proc.kill();
      }
      await new Promise((r) => setTimeout(r, 500));
    },
  };
}

export interface Browser {
  context: BrowserContext;
  worker(): Promise<import("@playwright/test").Worker>;
  close(): Promise<void>;
}

export async function startBrowser(): Promise<Browser> {
  const profile = mkdtempSync(join(tmpdir(), "motwi-e2e-"));
  const context = await chromium.launchPersistentContext(profile, {
    channel: "chromium",
    headless: true,
    viewport: { width: 420, height: 720 },
    args: [`--disable-extensions-except=${EXTENSION}`, `--load-extension=${EXTENSION}`],
  });
  return {
    context,
    async worker() {
      return context.serviceWorkers()[0] ?? context.waitForEvent("serviceworker");
    },
    async close() {
      await context.close();
      rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
    },
  };
}

/** Pairs through the real page, the way a click in Motwi's window does. */
export async function pair(browser: Browser, core: Core): Promise<void> {
  await browser.worker();
  const { url } = await core.api("POST", "/extension/pair");
  const page = await browser.context.newPage();
  await page.goto(url);
  await page.getByRole("heading", { name: "This browser is connected" }).waitFor();
  await page.close();
}

export interface Line {
  id?: string | null;
  login: string;
  text: string;
}

const CHAT_PAGE = (theme: string) => `<!doctype html>
<html class="tw-root--hover js-focus-visible tw-root--theme-${theme}">
<head><style>
  html, body { margin: 0; height: 100%; font: 13px sans-serif; background: ${theme === "dark" ? "#18181b" : "#fff"}; color: ${theme === "dark" ? "#efeff1" : "#0e0e10"}; }
  .stream-chat, .chat-room { height: 100%; }
  .chat-room { display: flex; flex-direction: column; }
  .chat-room__content { flex: 1; min-height: 0; display: flex; flex-direction: column; }
  .chat-list--default { flex: 1; min-height: 0; display: flex; flex-direction: column; }
  .scrollable-area { flex: 1; min-height: 0; overflow-y: auto; }
  .chat-scrollable-area__message-container { min-height: 100%; display: flex; flex-direction: column; justify-content: flex-end; }
  .chat-line__message { padding: 4px 14px; }
</style></head>
<body><div class="stream-chat"><section class="chat-room" data-test-selector="chat-room-component-layout" data-a-target="chat-theme-${theme}">
<div class="chat-room__content">
  <div class="chat-list--default"><div class="scrollable-area" data-a-target="chat-scroller">
    <div class="chat-scrollable-area__message-container" data-test-selector="chat-scrollable-area__message-container"></div>
  </div></div>
  <div class="chat-input">Send a message</div>
</div></section></div>
<script>
  window.addLine = function (line) {
    const el = document.createElement("div");
    el.className = "chat-line__message";
    el.setAttribute("data-a-target", "chat-line-message");
    el.setAttribute("data-a-user", line.login);
    el.innerHTML = '<span class="chat-line__username"><span class="chat-author__display-name" data-a-target="chat-message-username"></span></span>'
      + '<span aria-hidden="true">: </span>'
      + '<span data-a-target="chat-line-message-body" dir="auto"><span class="text-fragment" data-a-target="chat-message-text"></span></span>';
    el.querySelector(".chat-author__display-name").textContent = line.login;
    el.querySelector(".text-fragment").textContent = line.text;
    if (line.id) {
      // what React leaves on a real line, as seen on live Twitch
      el.__reactFiber$probe = { memoizedProps: { className: "x" }, return: { memoizedProps: { bits: 0 }, return: { memoizedProps: { message: { id: line.id } }, return: null } } };
    }
    document.querySelector(".chat-scrollable-area__message-container").appendChild(el);
    const scroller = document.querySelector(".scrollable-area");
    scroller.scrollTop = scroller.scrollHeight;
    return el;
  };
</script>
</body></html>`;

export async function openChat(
  browser: Browser,
  channel = CHANNEL,
  theme: "dark" | "light" = "dark",
): Promise<Page> {
  const page = await browser.context.newPage();
  await page.route("https://www.twitch.tv/**", (route) =>
    route.fulfill({ contentType: "text/html", body: CHAT_PAGE(theme) }),
  );
  await page.goto(`https://www.twitch.tv/popout/${channel}/chat`);
  return page;
}

export async function addLine(page: Page, line: Line): Promise<void> {
  // through the page's main world, so the isolated script sees what it would on Twitch
  await page.evaluate((l) => {
    (window as any).addLine(l);
  }, line);
}

export const bar = (page: Page) => page.locator("motwi-bar");
export const lineFor = (page: Page, login: string) =>
  page.locator(`.chat-line__message[data-a-user="${login}"]`);
