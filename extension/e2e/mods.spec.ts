// A mod's whole path, in real Chromium against a real demo core. Only Twitch is faked: its chat
// page, and its sign-in endpoints as the extension's own page calls them.

import { chromium, expect, test, type Page } from "@playwright/test";
import { createServer, type Server } from "node:http";
import { bar, CHANNEL, openChat, pair, startBrowser, startCore, type Browser, type Core } from "./harness";

const FRIEND = "friend_channel";

let core: Core;
let mod: Browser;
let nextPort = 47990;
const extra: { stop(): Promise<void> }[] = [];
let signIns: { devices: number; revoked: string[]; token: string };

test.beforeEach(async () => {
  core = await startCore(nextPort++);
  mod = await startBrowser();
  signIns = { devices: 0, revoked: [], token: "demo-brakkenhoff" };
  await mod.context.route("https://www.twitch.tv/activate**", (route) =>
    route.fulfill({ contentType: "text/html", body: "<h1>Twitch activate</h1>" }),
  );
});

test.afterEach(async () => {
  for (const thing of extra.splice(0)) await thing.stop();
  await mod.close();
  await core.stop();
});

/** Twitch's sign-in, as the join page calls it. Returns the page, ready to open a link. */
async function joinPage(browser: Browser): Promise<Page> {
  await browser.worker();
  const page = await browser.context.newPage();
  await page.route("https://id.twitch.tv/oauth2/**", async (route) => {
    const url = route.request().url();
    const body = new URLSearchParams(route.request().postData() ?? "");
    if (url.endsWith("/device")) {
      signIns.devices += 1;
      expect(body.get("scopes")).toBe(""); // it never asks for any permission
      await route.fulfill({
        json: {
          device_code: "dev",
          user_code: "MOTW1T3S",
          verification_uri: "https://www.twitch.tv/activate?device-code=MOTW1T3S",
          expires_in: 600,
          interval: 1,
        },
      });
    } else if (url.endsWith("/token")) {
      await route.fulfill({ json: { access_token: signIns.token, refresh_token: "r" } });
    } else if (url.endsWith("/revoke")) {
      signIns.revoked.push(body.get("token") ?? "");
      await route.fulfill({ status: 200, body: "" });
    } else {
      await route.abort();
    }
  });
  return page;
}

async function invite(target: Core = core): Promise<string> {
  const { url } = await target.api("POST", "/mods/invites");
  return url;
}

async function joinAsMod(browser: Browser = mod, target: Core = core): Promise<void> {
  const page = await joinPage(browser);
  await page.goto(await invite(target));
  await page.getByRole("button", { name: "Sign in with Twitch" }).click();
  await expect(page.getByRole("heading", { name: /You're in/ })).toBeVisible({ timeout: 10_000 });
  await page.close();
}

async function banRequest(target: Core, id: string, login: string): Promise<void> {
  await target.api("POST", "/demo/action", {
    message_id: id,
    login,
    text: "go back to where you came from",
    kind: "ban_request",
    status: "pending",
    score: 9.4,
    reason: "Targeted hate",
  });
}

test("a mod joins from an invite, and their sign-in is cancelled afterwards", async () => {
  const page = await joinPage(mod);
  await page.goto(await invite());
  await expect(page).toHaveURL(/^chrome-extension:.*join\.html/); // the site never sees sign-in
  await expect(page.getByRole("heading", { name: `Help moderate ${CHANNEL}` })).toBeVisible();

  await page.getByRole("button", { name: "Sign in with Twitch" }).click();
  await expect(page.getByRole("heading", { name: "You're in, brakkenhoff" })).toBeVisible({ timeout: 10_000 });
  expect(signIns.revoked).toEqual(["demo-brakkenhoff"]);

  const view = await core.api("GET", "/mods");
  expect(view.mods.map((m: { login: string }) => m.login)).toEqual(["brakkenhoff"]);
  expect(view.invites).toEqual([]);

  const chat = await openChat(mod);
  await expect(bar(chat).getByText("Watching")).toBeVisible();
  await expect(bar(chat).getByRole("switch")).toHaveCount(0); // pausing is the streamer's
  await expect.poll(async () => (await core.api("GET", "/mods")).mods[0].connected).toBe(true);
});

test("someone who isn't a mod is told why, and can try again once they are", async () => {
  signIns.token = "demo-some_stranger";
  const page = await joinPage(mod);
  await page.goto(await invite());
  await page.getByRole("button", { name: "Sign in with Twitch" }).click();
  await expect(page.getByRole("alert")).toContainText(`some_stranger isn't a moderator of ${CHANNEL}`, {
    timeout: 10_000,
  });
  expect(signIns.revoked).toEqual(["demo-some_stranger"]);
  expect((await core.api("GET", "/mods")).mods).toEqual([]);

  await core.api("POST", "/demo/mods", { logins: ["some_stranger"] });
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: "You're in, some_stranger" })).toBeVisible({ timeout: 10_000 });
});

test("a ban request reaches the streamer and the mod, and the mod's answer is credited", async () => {
  const streamer = await startBrowser();
  extra.push({ stop: () => streamer.close() });
  await pair(streamer, core);
  await joinAsMod();

  const own = await openChat(streamer);
  const theirs = await openChat(mod);
  await expect(bar(own).getByText("Watching")).toBeVisible();
  await expect(bar(theirs).getByText("Watching")).toBeVisible();

  await banRequest(core, "hate-1", "xx_v0id_xx");
  await expect(bar(own).getByRole("alertdialog")).toBeVisible();
  await expect(bar(theirs).getByRole("alertdialog")).toBeVisible();
  // each side sees who else is looking
  await expect(bar(own).getByText("brakkenhoff is here too")).toBeVisible({ timeout: 5000 });
  await expect(bar(theirs).getByText(`${CHANNEL} is here too`)).toBeVisible({ timeout: 5000 });

  await bar(theirs).getByRole("button", { name: "Ban", exact: true }).click();

  await expect(bar(theirs).getByText("xx_v0id_xx banned by brakkenhoff")).toBeVisible();
  await expect(bar(own).getByText("xx_v0id_xx banned by brakkenhoff")).toBeVisible();
  await expect(bar(own).getByRole("alertdialog")).toHaveCount(0);
  await expect(bar(theirs).getByRole("alertdialog")).toHaveCount(0);

  const [action] = await core.api("GET", "/actions?actor=brakkenhoff");
  expect(action.status).toBe("done");
});

test("when Motwi moves, the mod is told, and the new link brings them back with no sign-in", async () => {
  await joinAsMod();
  const chat = await openChat(mod);
  await expect(bar(chat).getByText("Watching")).toBeVisible();
  expect(signIns.devices).toBe(1);

  const { link } = await core.api("POST", "/demo/move");
  await expect(bar(chat).getByText("Motwi moved")).toBeVisible({ timeout: 15_000 });
  await expect(bar(chat).getByText("Ask the streamer for the new link")).toBeVisible();

  const page = await joinPage(mod);
  await page.goto(link);
  await expect(page.getByRole("heading", { name: `You're back on ${CHANNEL}` })).toBeVisible();
  await expect(bar(chat).getByText("Watching")).toBeVisible({ timeout: 10_000 });
  expect(signIns.devices).toBe(1);
});

test("a fake new link never gets the mod's token", async () => {
  await joinAsMod();
  const chat = await openChat(mod);
  await expect(bar(chat).getByText("Watching")).toBeVisible();

  // something at another address that claims to be this channel's Motwi and answers wrongly
  const seen: { path: string; auth: string | undefined }[] = [];
  const fake: Server = createServer((req, res) => {
    const url = new URL(req.url ?? "/", "http://x");
    seen.push({ path: url.pathname, auth: req.headers.authorization });
    if (url.pathname === "/motwi-join") {
      res.setHeader("Content-Type", "text/html");
      res.end("<h1>Join Motwi</h1>");
    } else if (url.pathname === "/mod/hello") {
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({ app: "motwi", channel: CHANNEL, known: true, proof: "0".repeat(64) }));
    } else {
      res.statusCode = 404;
      res.end();
    }
  });
  await new Promise<void>((ok) => fake.listen(0, "127.0.0.1", ok));
  extra.push({
    stop: () =>
      new Promise((ok) => {
        fake.close(() => ok());
        fake.closeAllConnections();
      }),
  });
  const { port } = fake.address() as { port: number };

  const page = await joinPage(mod);
  await page.goto(`http://127.0.0.1:${port}/motwi-join`);
  await expect(page.getByRole("heading", { name: "You need an invite first" })).toBeVisible();

  expect(seen.map((s) => s.path)).not.toContain("/mod/chat");
  expect(seen.every((s) => s.auth === undefined)).toBe(true);
  // and the real connection wasn't touched
  await expect(bar(chat).getByText("Watching")).toBeVisible();
});

test("removing a mod in Motwi disconnects them", async () => {
  await joinAsMod();
  const chat = await openChat(mod);
  await expect(bar(chat).getByText("Watching")).toBeVisible();
  const { mods } = await core.api("GET", "/mods");
  await core.api("DELETE", `/mods/clients/${mods[0].id}`);
  await expect(bar(chat).getByText("Disconnected")).toBeVisible({ timeout: 5000 });
  await expect(bar(chat).getByText("Ask the streamer for a new invite")).toBeVisible();
});

test("one browser can be a streamer and a mod for someone else at once", async () => {
  const friend = await startCore(nextPort++, ["--channel", FRIEND]);
  extra.push(friend);
  await pair(mod, core);
  await joinAsMod(mod, friend);

  const own = await openChat(mod, CHANNEL);
  const theirs = await openChat(mod, FRIEND);
  await expect(bar(own).getByText("Watching")).toBeVisible();
  await expect(bar(theirs).getByText("Watching")).toBeVisible();
  await expect(bar(own).getByRole("switch")).toHaveCount(1);
  await expect(bar(theirs).getByRole("switch")).toHaveCount(0);

  // each only hears from its own Motwi
  await banRequest(friend, "hate-2", "kiwi_drift");
  await expect(bar(theirs).getByRole("alertdialog")).toBeVisible();
  await expect(bar(own).getByRole("alertdialog")).toHaveCount(0);

  const id = new URL((await mod.worker()).url()).host;
  const popup = await mod.context.newPage();
  await popup.goto(`chrome-extension://${id}/popup.html`);
  await expect(popup.getByText(CHANNEL, { exact: true })).toBeVisible();
  await expect(popup.getByText(FRIEND)).toBeVisible();
  await popup.getByRole("button", { name: "Stop moderating here" }).click();
  await expect(popup.getByText(FRIEND)).toHaveCount(0);
  await expect.poll(async () => (await friend.api("GET", "/mods")).mods.length).toBe(0);
  await expect(bar(own).getByText("Watching")).toBeVisible();
});

test("someone without the extension is told how to get it", async () => {
  const url = await invite();
  const plain = await chromium.launch({ channel: "chromium", headless: true });
  extra.push({ stop: () => plain.close() });
  const page = await plain.newPage();
  await page.goto(url);
  await expect(page.getByRole("heading", { name: "Install Motwi's extension first" })).toBeVisible({ timeout: 5000 });
  const download = page.getByRole("link", { name: "Download the extension" });
  const resp = await page.request.get(new URL((await download.getAttribute("href"))!, page.url()).href);
  expect(resp.status()).toBe(200);
  expect(resp.headers()["content-type"]).toContain("zip");
});
