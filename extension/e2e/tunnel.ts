// The mod path over a real Cloudflare quick tunnel, on demo data with Twitch faked:
//
//     npm run build && npm run live:tunnel
//
// It needs the internet and cloudflared (packaging/build.py downloads it). The tunnel exists
// only for the seconds this runs, and the demo core behind it has no real data or tokens.

import { addLine, bar, CHANNEL, openChat, startBrowser, startCore, type Core } from "./harness.ts";

const STEP_MS = 60_000;

function say(what: string): void {
  console.log(`  ok   ${what}`);
}

async function until<T>(what: string, check: () => Promise<T | false | null | undefined>): Promise<T> {
  const deadline = Date.now() + STEP_MS;
  let last: unknown;
  while (Date.now() < deadline) {
    try {
      const value = await check();
      if (value) return value;
    } catch (e) {
      last = e;
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`gave up waiting for ${what}${last ? `: ${last}` : ""}`);
}

async function main(): Promise<void> {
  let core: Core | null = null;
  let mod: Awaited<ReturnType<typeof startBrowser>> | null = null;
  try {
    core = await startCore(47985, ["--real-tunnel"]);
    const c = core;
    mod = await startBrowser();
    await mod.worker();

    const { url } = await c.api("POST", "/mods/invites");
    if (!url?.startsWith("https://") || !url.includes(".trycloudflare.com/motwi-join#c=")) {
      throw new Error(`unexpected invite: ${url}`);
    }
    say(`tunnel up, invite at ${new URL(url).host}`);

    const page = await mod.context.newPage();
    await mod.context.route("https://www.twitch.tv/activate**", (r) =>
      r.fulfill({ contentType: "text/html", body: "<h1>Twitch activate</h1>" }),
    );
    await page.route("https://id.twitch.tv/oauth2/**", async (route) => {
      const path = route.request().url();
      if (path.endsWith("/device")) {
        await route.fulfill({
          json: {
            device_code: "d",
            user_code: "MOTW1T3S",
            verification_uri: "https://www.twitch.tv/activate?device-code=MOTW1T3S",
            expires_in: 600,
            interval: 1,
          },
        });
      } else if (path.endsWith("/token")) {
        await route.fulfill({ json: { access_token: "demo-brakkenhoff", refresh_token: "r" } });
      } else {
        await route.fulfill({ status: 200, body: "" });
      }
    });

    // Motwi holds the link back until it resolves, so this has to work the first time
    await page.goto(url, { timeout: 30_000 });
    await page.waitForURL(/^chrome-extension:.*join\.html/, { timeout: STEP_MS });
    say("the invite opened the extension's join page");

    await page.getByRole("button", { name: "Sign in with Twitch" }).click();
    await page.getByRole("heading", { name: "You're in, brakkenhoff" }).waitFor({ timeout: STEP_MS });
    say("joined through the tunnel");

    const chat = await openChat(mod);
    await bar(chat).getByText("Watching").waitFor({ timeout: STEP_MS });
    await until("Motwi to see the mod connected", async () => {
      const { mods } = await c.api("GET", "/mods");
      return mods[0]?.connected;
    });
    say("the WebSocket is open through Cloudflare");

    await addLine(chat, { id: "hate-1", login: "xx_v0id_xx", text: "go back to where you came from" });
    await c.api("POST", "/demo/action", {
      message_id: "hate-1",
      login: "xx_v0id_xx",
      text: "go back to where you came from",
      kind: "ban_request",
      status: "pending",
      score: 9.4,
      reason: "Targeted hate",
    });
    await bar(chat).getByRole("alertdialog").waitFor({ timeout: STEP_MS });
    say("a ban request reached the mod");

    await bar(chat).getByRole("button", { name: "Ban", exact: true }).click();
    await bar(chat).getByText("xx_v0id_xx banned by brakkenhoff").waitFor({ timeout: STEP_MS });
    say("the mod's answer came back, credited to them");

    const [action] = await c.api("GET", "/actions?actor=brakkenhoff");
    if (action?.status !== "done") throw new Error(`action is ${action?.status}`);
    say(`recorded on ${CHANNEL}'s Motwi as done by brakkenhoff`);
    console.log("\nReal tunnel run passed.");
  } finally {
    await mod?.close();
    await core?.stop();
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
