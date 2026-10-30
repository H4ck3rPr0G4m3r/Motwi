import { expect, test } from "@playwright/test";
import {
  addLine,
  bar,
  CHANNEL,
  lineFor,
  openChat,
  pair,
  startBrowser,
  startCore,
  type Browser,
  type Core,
} from "./harness";

let core: Core;
let browser: Browser;
let nextPort = 47930;
let seq = 0;

test.beforeEach(async () => {
  core = await startCore(nextPort++);
  browser = await startBrowser();
});

test.afterEach(async () => {
  await browser.close();
  await core.stop();
});

function msg(login: string) {
  seq += 1;
  return { id: `msg-${seq}`, login, text: `message ${seq} from ${login}` };
}

async function act(line: { id: string; login: string; text: string }, extra: object = {}) {
  return core.api("POST", "/demo/action", {
    message_id: line.id,
    login: line.login,
    text: line.text,
    kind: "delete",
    score: 8.1,
    reason: "Spoils the ending",
    ...extra,
  });
}

test("the pair page says so when it has no code", async () => {
  await browser.worker();
  const page = await browser.context.newPage();
  await page.goto(`http://127.0.0.1:${core.port}/pair.html`);
  await expect(page.getByRole("heading", { name: "Nothing to connect" })).toBeVisible();
});

test("a used pairing link is refused, and the window sees the new browser", async () => {
  await browser.worker();
  const { url } = await core.api("POST", "/extension/pair");
  const page = await browser.context.newPage();
  await page.goto(url);
  await expect(page.getByRole("heading", { name: "This browser is connected" })).toBeVisible();
  const { clients } = await core.api("GET", "/extension");
  expect(clients).toHaveLength(1);
  expect(clients[0].label).toBeTruthy();

  await page.goto("about:blank");
  await page.goto(url);
  await expect(page.getByRole("heading", { name: "That didn't connect" })).toBeVisible();
  await expect(page.getByText("already been used")).toBeVisible();
});

test("it stays out of other channels and activates on the paired one", async () => {
  await pair(browser, core);
  const other = await openChat(browser, "somebody_else");
  await other.waitForTimeout(1500);
  await expect(bar(other)).toHaveCount(0);

  const mine = await openChat(browser);
  await expect(bar(mine).getByText("Watching")).toBeVisible();
});

test("an open Twitch tab activates as soon as pairing finishes", async () => {
  await browser.worker();
  const page = await openChat(browser);
  await page.waitForTimeout(1200);
  await expect(bar(page)).toHaveCount(0);
  await pair(browser, core);
  await expect(bar(page).getByText("Watching")).toBeVisible({ timeout: 4000 });
});

test("a deleted message is struck through with its tag, reason and undo", async () => {
  await pair(browser, core);
  const page = await openChat(browser);
  await expect(bar(page).getByText("Watching")).toBeVisible();

  const line = msg("pixelbandit_");
  await addLine(page, line);
  await act(line);

  const el = lineFor(page, line.login);
  await expect(el.locator("motwi-mark").getByText("Deleted")).toBeVisible();
  await expect(el.locator("motwi-mark").getByText("Spoils the ending")).toBeVisible();
  await expect(el).toHaveAttribute("data-motwi-state", "gone");
  const decoration = await el
    .locator('[data-a-target="chat-line-message-body"]')
    .evaluate((n) => getComputedStyle(n).textDecorationLine);
  expect(decoration).toBe("line-through");

  // spec 5.6: undo can't bring a delete back, and the words say so
  await el.locator("motwi-mark").getByRole("button", { name: "Undo" }).click();
  await expect(el.locator("motwi-mark").getByText("Marked as mistake")).toBeVisible();
  await expect(el.locator("motwi-mark").getByText("Twitch can't restore it")).toBeVisible();
  await expect(el).toHaveAttribute("data-motwi-state", "kept");
});

test("a line without a React id still matches on login and text", async () => {
  await pair(browser, core);
  const page = await openChat(browser);
  await expect(bar(page).getByText("Watching")).toBeVisible();
  const line = msg("no_fiber");
  await addLine(page, { ...line, id: null });
  await act(line);
  await expect(lineFor(page, line.login).locator("motwi-mark").getByText("Deleted")).toBeVisible();
});

test("when Twitch replaces the text, the mark keeps the original", async () => {
  await pair(browser, core);
  const page = await openChat(browser);
  await expect(bar(page).getByText("Watching")).toBeVisible();
  const line = msg("rude_one");
  await addLine(page, line);
  await act(line);
  const el = lineFor(page, line.login);
  await expect(el.locator("motwi-mark").getByText("Deleted")).toBeVisible();

  await el.locator(".text-fragment").evaluate((n) => (n.textContent = "<message deleted>"));
  await expect(el.locator("motwi-mark").getByText(line.text)).toBeVisible();
});

test("a re-rendered line gets its mark back, and only one", async () => {
  await pair(browser, core);
  const page = await openChat(browser);
  await expect(bar(page).getByText("Watching")).toBeVisible();
  const line = msg("rerendered");
  await addLine(page, line);
  await act(line);
  const el = lineFor(page, line.login);
  await expect(el.locator("motwi-mark").getByText("Deleted")).toBeVisible();

  await el.evaluate((n) => n.remove());
  await addLine(page, line);
  await expect(lineFor(page, line.login)).toHaveCount(1);
  await expect(lineFor(page, line.login).locator("motwi-mark")).toHaveCount(1);
  await expect(lineFor(page, line.login).locator("motwi-mark").getByText("Deleted")).toBeVisible();
});

test("chat keeps following new messages when a mark adds height", async () => {
  await pair(browser, core);
  const page = await openChat(browser);
  await expect(bar(page).getByText("Watching")).toBeVisible();
  for (let i = 0; i < 60; i++) await addLine(page, msg("filler"));
  const line = msg("last_one");
  await addLine(page, line);
  await act(line, { reason: "A long reason ".repeat(12) });
  await expect(lineFor(page, line.login).locator("motwi-mark").getByText("Deleted")).toBeVisible();
  const gap = await page
    .locator(".scrollable-area")
    .evaluate((n) => n.scrollHeight - n.scrollTop - n.clientHeight);
  expect(gap).toBeLessThanOrEqual(2);
});

test("a ban request pops up, waits for Twitch, then collapses to a receipt with undo", async () => {
  await pair(browser, core);
  const page = await openChat(browser);
  await expect(bar(page).getByText("Watching")).toBeVisible();
  const line = msg("xx_v0id_xx");
  await addLine(page, line);
  await act(line, { kind: "ban_request", status: "pending", score: 9.4, reason: "Targeted hate" });

  const popup = bar(page).getByRole("alertdialog");
  await expect(popup.getByText("Targeted hate")).toBeVisible();
  await expect(popup.getByText("Held in timeout while you decide")).toBeVisible();
  await expect(lineFor(page, line.login).locator("motwi-mark").getByText("Ban requested")).toBeVisible();

  await popup.getByRole("button", { name: "Ban" }).click();
  await expect(bar(page).getByText(`${line.login} banned by ${CHANNEL}`)).toBeVisible();
  await expect(popup).toHaveCount(0);
  await expect(lineFor(page, line.login).locator("motwi-mark").getByText("Banned")).toBeVisible();

  await bar(page).locator(".resolved").getByRole("button", { name: "Undo" }).click();
  await expect(lineFor(page, line.login).locator("motwi-mark").getByText("Unbanned")).toBeVisible();
});

test("a failed ban says so, stays open, and can be retried", async () => {
  await pair(browser, core);
  const page = await openChat(browser);
  await expect(bar(page).getByText("Watching")).toBeVisible();
  const line = msg("xx_fail_xx");
  await addLine(page, line);
  await act(line, { kind: "ban_request", status: "pending" });

  await core.api("POST", "/demo/fail", { on: true });
  const popup = bar(page).getByRole("alertdialog");
  await popup.getByRole("button", { name: "Ban" }).click();
  await expect(popup.getByText("Ban failed")).toBeVisible();
  await expect(popup.getByText("Twitch said no (demo)")).toBeVisible();
  await expect(popup.getByText("Nothing changed on Twitch")).toBeVisible();

  await core.api("POST", "/demo/fail", { on: false });
  await popup.getByRole("button", { name: "Retry" }).click();
  await expect(bar(page).getByText(`${line.login} banned by ${CHANNEL}`)).toBeVisible();
  await expect(popup).toHaveCount(0);
});

test("answering in one tab collapses the pop-up in another", async () => {
  await pair(browser, core);
  const one = await openChat(browser);
  const two = await openChat(browser);
  await expect(bar(one).getByText("Watching")).toBeVisible();
  await expect(bar(two).getByText("Watching")).toBeVisible();
  const line = msg("two_tabs");
  await act(line, { kind: "ban_request", status: "pending" });
  await expect(bar(two).getByRole("alertdialog")).toBeVisible();

  await bar(one).getByRole("alertdialog").getByRole("button", { name: "Deny" }).click();
  await expect(bar(two).getByRole("alertdialog")).toHaveCount(0);
  await expect(bar(two).getByText(`${line.login}'s ban denied by ${CHANNEL}`)).toBeVisible();
});

test("suggest-only shows buttons, and warn instead softens the action", async () => {
  await core.api("PATCH", "/settings", { suggest_only: true });
  await pair(browser, core);
  const page = await openChat(browser);
  await expect(bar(page).getByText("Suggesting")).toBeVisible();
  await expect(bar(page).getByText("Suggest only")).toBeVisible();

  const line = msg("grizzlecat");
  await addLine(page, line);
  await act(line, { kind: "timeout", duration_s: 60, status: "suggested", score: 7.6 });
  const el = lineFor(page, line.login);
  const mark = el.locator("motwi-mark");
  await expect(mark.getByText("Suggest timeout 1m")).toBeVisible();
  await expect(el).toHaveAttribute("data-motwi-state", "suggested");

  await mark.getByRole("button", { name: "Warn instead" }).click();
  await expect(mark.getByText("Warned")).toBeVisible();
  await expect(el).toHaveAttribute("data-motwi-state", "gone");

  const other = msg("kofi_draws");
  await addLine(page, other);
  await act(other, { kind: "delete", status: "suggested" });
  const otherMark = lineFor(page, other.login).locator("motwi-mark");
  await otherMark.getByRole("button", { name: "Ignore" }).click();
  await expect(otherMark.getByText("Dismissed")).toBeVisible();
});

test("the switch only moves once the core confirms", async () => {
  await pair(browser, core);
  const page = await openChat(browser);
  await expect(bar(page).getByText("Watching")).toBeVisible();
  const sw = bar(page).getByRole("switch");
  await expect(sw).toHaveAttribute("aria-checked", "true");
  await sw.click();
  await expect(bar(page).getByText("Paused")).toBeVisible();
  await expect(sw).toHaveAttribute("aria-checked", "false");
  expect((await core.api("GET", "/status")).paused).toBe(true);
  await sw.click();
  await expect(bar(page).getByText("Watching")).toBeVisible();
});

test("counts and the log catch actions whose lines aren't in view", async () => {
  await pair(browser, core);
  const page = await openChat(browser);
  await expect(bar(page).getByText("Watching")).toBeVisible();
  const line = msg("scrolled_away");
  await act(line, { kind: "timeout", duration_s: 600 });

  const counts = bar(page).locator(".counts");
  await expect(counts.getByText("1 to")).toBeVisible();
  await expect(lineFor(page, line.login)).toHaveCount(0);

  await counts.click();
  const log = bar(page).getByRole("region", { name: "Recent actions" });
  await expect(log.getByText(line.text)).toBeVisible();
  await log.getByRole("button", { name: "Undo" }).first().click();
  await expect(log.getByText("Timeout removed").first()).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(log).toHaveCount(0);
});

test("when Motwi stops, the bar says so at once and its buttons disable", async () => {
  await pair(browser, core);
  const page = await openChat(browser);
  await expect(bar(page).getByText("Watching")).toBeVisible();
  const line = msg("before_crash");
  await addLine(page, line);
  await act(line);
  await expect(lineFor(page, line.login).locator("motwi-mark").getByText("Deleted")).toBeVisible();

  await core.stop();
  await expect(bar(page).getByText("Motwi isn't running")).toBeVisible({ timeout: 5000 });
  await expect(bar(page).getByText("Watching")).toHaveCount(0);
  await expect(bar(page).getByRole("switch")).toBeDisabled();
  // what already happened stays visible, but can't be acted on
  await expect(lineFor(page, line.login).locator("motwi-mark").getByRole("button", { name: "Undo" })).toBeDisabled();
});

test("removing the browser in Motwi disconnects the tab", async () => {
  await pair(browser, core);
  const page = await openChat(browser);
  await expect(bar(page).getByText("Watching")).toBeVisible();
  const { clients } = await core.api("GET", "/extension");
  expect(clients[0].connected).toBe(true);
  await core.api("DELETE", `/extension/clients/${clients[0].id}`);
  await expect(bar(page).getByText("Disconnected")).toBeVisible({ timeout: 5000 });
});

test("light and dark themes follow Twitch", async () => {
  await pair(browser, core);
  const dark = await openChat(browser, CHANNEL, "dark");
  await expect(bar(dark).getByText("Watching")).toBeVisible();
  const light = await openChat(browser, CHANNEL, "light");
  await expect(bar(light).getByText("Watching")).toBeVisible();
  const bg = (page: typeof dark) =>
    bar(page)
      .locator(".bar")
      .evaluate((n) => getComputedStyle(n).backgroundColor);
  expect(await bg(dark)).toBe("rgb(20, 20, 23)");
  expect(await bg(light)).toBe("rgb(247, 247, 248)");
});

test("the bar's fonts load from the extension", async () => {
  await pair(browser, core);
  const page = await openChat(browser);
  await expect(bar(page).getByText("Watching")).toBeVisible();
  const loaded = await page.evaluate(async () => {
    await document.fonts.ready;
    return [...document.fonts].filter((f) => f.status === "loaded").map((f) => f.family);
  });
  expect(loaded.join()).toContain("Motwi Inter");
});

test("the toolbar popup reports the state and can disconnect", async () => {
  const worker = await browser.worker();
  const id = new URL(worker.url()).host;
  const page = await browser.context.newPage();
  await page.goto(`chrome-extension://${id}/popup.html`);
  await expect(page.getByText("isn't connected to Motwi yet")).toBeVisible();

  await pair(browser, core);
  await page.reload();
  await expect(page.getByText(CHANNEL, { exact: true })).toBeVisible();
  await expect(page.getByRole("status")).toHaveText("Watching");

  await page.getByRole("button", { name: "Disconnect this browser" }).click();
  await expect(page.getByText("isn't connected to Motwi yet")).toBeVisible();
  expect((await core.api("GET", "/extension")).clients).toHaveLength(0);
});
