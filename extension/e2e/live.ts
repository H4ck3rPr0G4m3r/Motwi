// Runs the built extension on a live public Twitch chat, with a demo core pretending to be
// that channel's Motwi. It marks real messages but never reaches Twitch: every action goes to
// the demo's fake executor, and the browser is anonymous, so it can't do anything in chat.
//
//   npm run live [-- channel]     picks the top live channel if none is given

import { chromium } from "@playwright/test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { EXTENSION, pair, startCore, type Browser } from "./harness.ts";

const OUT = process.env.MOTWI_LIVE_OUT ?? tmpdir();
const PORT = 47925;

async function topChannel(): Promise<string> {
  const b = await chromium.launch({ headless: true });
  const page = await b.newPage();
  await page.goto("https://www.twitch.tv/directory/all", { waitUntil: "domcontentloaded" });
  const link = page.locator('a[data-a-target="preview-card-image-link"]').first();
  await link.waitFor({ timeout: 30_000 });
  const href = await link.getAttribute("href");
  await b.close();
  return href!.split("/")[1].toLowerCase();
}

const channel = process.argv[2]?.toLowerCase() ?? (await topChannel());
console.log("channel:", channel);
const core = await startCore(PORT, ["--channel", channel]);
const profile = mkdtempSync(join(tmpdir(), "motwi-live-"));
const context = await chromium.launchPersistentContext(profile, {
  channel: "chromium",
  headless: true,
  viewport: { width: 400, height: 900 },
  args: [`--disable-extensions-except=${EXTENSION}`, `--load-extension=${EXTENSION}`],
});
const browser: Browser = {
  context,
  worker: async () => context.serviceWorkers()[0] ?? context.waitForEvent("serviceworker"),
  close: async () => {},
};

const results: [string, boolean, string?][] = [];
const check = (name: string, ok: boolean, detail?: string) => {
  results.push([name, ok, detail]);
  console.log(ok ? "PASS" : "FAIL", name, detail ?? "");
};

/** Marks the newest real lines with no pop-up in the way, and photographs them. */
async function markLines(page: import("@playwright/test").Page, label: string) {
  const real = await page.evaluate(() =>
    [...document.querySelectorAll(".chat-line__message[data-motwi-id]")].slice(-3).map((l) => ({
      id: l.getAttribute("data-motwi-id")!,
      login: l.getAttribute("data-a-user")!,
      text: l.querySelector('[data-a-target="chat-line-message-body"]')?.textContent ?? "",
    })),
  );
  const specs = [
    { kind: "delete", score: 8.1, reason: "Spoils the ending" },
    { kind: "timeout", duration_s: 60, score: 7.8, reason: "Insults the streamer" },
    { kind: "timeout", duration_s: 60, status: "suggested", score: 7.1, reason: "Borderline insult" },
  ];
  for (let i = 0; i < real.length; i++) {
    await core.api("POST", "/demo/action", {
      message_id: real[i].id,
      login: real[i].login,
      text: real[i].text,
      ...specs[i],
    });
  }
  await page.waitForTimeout(600);
  await page.screenshot({ path: join(OUT, `live-${label}-marks.png`) });
}

async function run(path: string, theme: "1" | "0", label: string, mark: boolean) {
  const page = await context.newPage();
  await page.addInitScript((t) => localStorage.setItem("twilight.theme", t), theme);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`https://www.twitch.tv/${path}`, { waitUntil: "domcontentloaded" });
  await page.locator(".chat-line__message").first().waitFor({ timeout: 45_000 });
  // an anonymous profile gets Twitch's cookie dialog, which would cover half the chat
  await page
    .getByRole("button", { name: "Reject" })
    .click({ timeout: 4000 })
    .catch(() => {});
  await page.waitForTimeout(5000);

  const bar = page.locator("motwi-bar");
  check(`${label}: bar mounted`, (await bar.count()) === 1);
  check(`${label}: bar says Watching`, await bar.getByText("Watching").isVisible());
  const stats = await page.evaluate(() => {
    const all = [...document.querySelectorAll(".chat-line__message")];
    return { lines: all.length, tagged: all.filter((l) => l.hasAttribute("data-motwi-id")).length };
  });
  check(
    `${label}: lines carry message ids`,
    stats.lines > 0 && stats.tagged === stats.lines,
    `${stats.tagged}/${stats.lines}`,
  );
  check(
    `${label}: theme`,
    await page.evaluate(
      (t) => document.documentElement.className.includes(t === "1" ? "theme-dark" : "theme-light"),
      theme,
    ),
  );

  const strays = await bar.locator(".floats > *").count();
  const card = await page.evaluate(() => {
    const host = document.querySelector("motwi-bar")!.getBoundingClientRect();
    const hit = document.elementFromPoint(host.left + host.width / 2, host.bottom + 30);
    return hit ? `${hit.tagName.toLowerCase()}.${String(hit.className).slice(0, 60)}` : "nothing";
  });
  console.log(`  under the bar: ${card}; Motwi floats: ${strays}`);

  await markLines(page, label);

  if (mark) {
    const real = await page.evaluate(() =>
      [...document.querySelectorAll(".chat-line__message[data-motwi-id]")].slice(-4).map((l) => ({
        id: l.getAttribute("data-motwi-id")!,
        login: l.getAttribute("data-a-user")!,
        text: l.querySelector('[data-a-target="chat-line-message-body"]')?.textContent ?? "",
      })),
    );
    const specs = [
      { kind: "delete", score: 8.1, reason: "Spoils the ending" },
      { kind: "timeout", duration_s: 60, score: 7.8, reason: "Insults the streamer" },
      { kind: "warn", status: "suggested", score: 6.4, reason: "Borderline insult" },
      { kind: "ban_request", status: "pending", score: 9.4, reason: "Targeted hate" },
    ];
    for (let i = 0; i < specs.length; i++) {
      await core.api("POST", "/demo/action", {
        message_id: real[i].id,
        login: real[i].login,
        text: real[i].text,
        ...specs[i],
      });
    }
    await page.waitForTimeout(1500);
    const marked = await page.evaluate(
      (ids) =>
        ids.filter((id) =>
          document.querySelector(`.chat-line__message[data-motwi-id="${id}"] motwi-mark`),
        ).length,
      real.map((r) => r.id),
    );
    check(`${label}: real lines got marks`, marked >= 3, `${marked}/${real.length}`);
    check(`${label}: pop-up up`, await bar.getByRole("alertdialog").isVisible());
    await page.screenshot({ path: join(OUT, `live-${label}.png`) });

    const follows = await page.locator('[data-a-target="chat-scroller"]').evaluate(async (root) => {
      const find = (el: Element): Element | null => {
        for (const c of [el, ...el.querySelectorAll("*")]) {
          if (c.scrollHeight > c.clientHeight + 1 && /auto|scroll/.test(getComputedStyle(c).overflowY)) {
            return c;
          }
        }
        return null;
      };
      const sc = find(root);
      if (!sc) return "no scroller";
      await new Promise((r) => setTimeout(r, 4000));
      return Math.round(sc.scrollHeight - sc.scrollTop - sc.clientHeight);
    });
    check(
      `${label}: chat keeps following`,
      typeof follows === "number" && follows < 60,
      String(follows),
    );
    const paused = await page
      .getByText(/chat paused/i)
      .isVisible()
      .catch(() => false);
    check(`${label}: Twitch didn't pause chat`, !paused);

    await bar.getByRole("alertdialog").getByRole("button", { name: "Ban" }).click();
    await bar.locator(".resolved").waitFor({ timeout: 5000 });
    check(`${label}: ban answered through the fake`, await bar.locator(".resolved").isVisible());
    await page.screenshot({ path: join(OUT, `live-${label}-receipt.png`) });

    await bar.locator(".counts").click();
    await bar.getByRole("region", { name: "Recent actions" }).waitFor();
    await page.screenshot({ path: join(OUT, `live-${label}-log.png`) });
    await page.keyboard.press("Escape");
  } else {
    await page.screenshot({ path: join(OUT, `live-${label}.png`) });
  }

  const fonts = await page.evaluate(
    () =>
      [...document.fonts].filter((f) => f.status === "loaded" && f.family.includes("Motwi")).length,
  );
  check(`${label}: extension fonts loaded`, fonts > 0, String(fonts));
  check(
    `${label}: no page errors from us`,
    !errors.some((e) => /motwi/i.test(e)),
    errors.slice(0, 2).join(" | "),
  );
  await page.close();
}

try {
  await pair(browser, core);
  await run(`popout/${channel}/chat`, "1", "popout-dark", true);
  await run(`popout/${channel}/chat`, "0", "popout-light", false);
  await run(channel, "1", "channel-dark", false);
} finally {
  await context.close();
  await core.stop();
  rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
}
const failed = results.filter((r) => !r[1]);
console.log(`\n${results.length - failed.length}/${results.length} checks passed. Screenshots in ${OUT}`);
process.exit(failed.length ? 1 : 0);
