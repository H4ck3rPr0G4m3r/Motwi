// The service worker: the only code holding tokens or talking to a Motwi.
//
// A browser can hold several connections: the streamer's own Motwi, and any channels it mods
// for through their tunnels. Each keeps one WebSocket open while a Twitch tab on its channel
// is connected. A keepalive every 20 s stops Chrome from suspending the worker meanwhile. If
// Chrome stops it anyway, the tabs' ports drop, they reconnect, and it starts over from storage.

import { currentOrigin } from "./address";
import { expectedProof, newNonce } from "../shared/proof";
import {
  PAIRED,
  PORT_NAME,
  type Action,
  type Chat,
  type JoinResult,
  type Link,
  type LinkInfo,
  type PairResult,
  type PortEvent,
  type PortRequest,
  type Request,
  type Role,
  type Snapshot,
} from "../shared/protocol";

interface Pair {
  role: Role;
  origin: string;
  token: string;
  channel: string | null;
  /** A mod's entry on that Motwi, which the proof is asked for. */
  clientId?: string;
}

const KEY = "pairs";
const OLD_KEY = "pair"; // one { port, token, channel }, from before mods
const KEEPALIVE_MS = 20_000;
const IDLE_CLOSE_MS = 30_000;
const REFRESH_DELAY_MS = 250;
const RETRY_MS = [1_000, 2_000, 5_000, 10_000, 15_000];

const conns = new Set<Conn>();
const popups = new Set<chrome.runtime.Port>();

chrome.storage.local.setAccessLevel?.({ accessLevel: "TRUSTED_CONTEXTS" });

const ready: Promise<void> = chrome.storage.local.get([KEY, OLD_KEY]).then(async (stored) => {
  const pairs = (stored[KEY] as Pair[] | undefined) ?? [];
  const old = stored[OLD_KEY] as { port: number; token: string; channel: string | null } | undefined;
  if (old) {
    pairs.push({
      role: "own",
      origin: `http://127.0.0.1:${old.port}`,
      token: old.token,
      channel: old.channel,
    });
    await chrome.storage.local.set({ [KEY]: pairs });
    await chrome.storage.local.remove(OLD_KEY);
  }
  for (const pair of pairs) conns.add(new Conn(pair));
});

function save(): Promise<void> {
  const pairs = [...conns].map((c) => c.pair).filter((p): p is Pair => p !== null);
  return chrome.storage.local.set({ [KEY]: pairs });
}

function tellPopups(): void {
  const list = [...conns].map((c) => c.snapshot());
  for (const port of popups) port.postMessage({ type: "list", items: list } satisfies PortEvent);
}

class Refused extends Error {}

/** One connected Motwi. A removed one stays alive for the tabs still showing it. */
class Conn {
  pair: Pair | null;
  link: Link = "connecting";
  chat: Chat | null = null;
  readonly role: Role;
  readonly tabs = new Set<chrome.runtime.Port>();

  #socket: WebSocket | null = null;
  #connecting = false;
  #retry = 0;
  #retryTimer: ReturnType<typeof setTimeout> | undefined;
  #idleTimer: ReturnType<typeof setTimeout> | undefined;
  #refreshTimer: ReturnType<typeof setTimeout> | undefined;

  constructor(pair: Pair) {
    this.pair = pair;
    this.role = pair.role;
  }

  get #api(): string {
    return this.role === "mod" ? "/mod" : "/api";
  }

  snapshot(): Snapshot {
    return { link: this.link, channel: this.pair?.channel ?? null, chat: this.chat, role: this.role };
  }

  #broadcast(event: PortEvent): void {
    for (const port of this.tabs) port.postMessage(event);
  }

  announce(): void {
    this.#broadcast({ type: "snapshot", snapshot: this.snapshot() });
    tellPopups();
  }

  #setLink(next: Link): void {
    if (next !== this.link) {
      this.link = next;
      this.announce();
    }
  }

  // HTTP

  async call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const pair = this.pair;
    if (!pair) throw new Refused("This browser isn't connected to Motwi.");
    let resp: Response;
    try {
      resp = await fetch(`${pair.origin}${this.#api}${path}`, {
        method,
        headers: {
          Authorization: `Bearer ${pair.token}`,
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new Error(this.role === "mod" ? "Can't reach Motwi." : "Motwi isn't running.");
    }
    const data = await resp.json().catch(() => ({}));
    if (resp.status === 401) {
      await this.revoked();
      throw new Refused("This browser was disconnected from Motwi.");
    }
    if (!resp.ok) throw new Error(data.error ?? `Motwi refused that (${resp.status}).`);
    return data as T;
  }

  async #refresh(): Promise<void> {
    try {
      const next = await this.call<Chat>("GET", "/chat");
      this.chat = next;
      const login = next.status.accounts.streamer?.login ?? null;
      if (login && this.pair && login !== this.pair.channel) {
        this.pair = { ...this.pair, channel: login };
        await save();
      }
      this.announce();
    } catch {
      // the socket decides whether the core is gone; a failed read just waits for the next one
    }
  }

  scheduleRefresh(): void {
    clearTimeout(this.#refreshTimer);
    this.#refreshTimer = setTimeout(() => void this.#refresh(), REFRESH_DELAY_MS);
  }

  #upsert(action: Action): void {
    if (!this.chat) return;
    const at = this.chat.recent.findIndex((a) => a.id === action.id);
    if (at >= 0) this.chat.recent[at] = action;
    else this.chat.recent.unshift(action);
  }

  // the socket

  /** A mod's Motwi lives at an address anyone could have taken over since: it has to prove
   * itself before the token goes anywhere near it. */
  async #verified(pair: Pair): Promise<"ok" | "moved" | "removed"> {
    const origin = await currentOrigin(pair);
    const answer = await askMotwi(origin, pair);
    if (answer === null) return "moved";
    if (answer.known === false) return "removed";
    return answer.proof && answer.proof === (await answer.expected) ? "ok" : "moved";
  }

  ensureSocket(): void {
    clearTimeout(this.#idleTimer);
    if (!this.pair || this.#socket || this.#connecting) return;
    if (this.tabs.size === 0 && popups.size === 0) return;
    clearTimeout(this.#retryTimer);
    if (this.role === "own") {
      this.#open(this.pair);
      return;
    }
    this.#connecting = true;
    const pair = this.pair;
    void this.#verified(pair).then((result) => {
      this.#connecting = false;
      if (this.pair !== pair) {
        this.ensureSocket(); // moved to a new address while this was being checked
        return;
      }
      if (result === "ok") this.#open(pair);
      else if (result === "removed") void this.revoked();
      else {
        this.#setLink("moved");
        this.#scheduleRetry();
      }
    });
  }

  #open(pair: Pair): void {
    const ws = new WebSocket(`${pair.origin.replace(/^http/, "ws")}${this.#api}/events`);
    this.#socket = ws;
    let keepalive: ReturnType<typeof setInterval> | undefined;
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "hello", token: pair.token }));
      keepalive = setInterval(() => ws.send('{"type":"ping"}'), KEEPALIVE_MS);
    };
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data as string);
      if (m.type === "hello") {
        this.#retry = 0;
        if (this.chat) this.chat.status = m.status;
        this.#setLink("open");
        void this.#refresh();
      } else if (m.type === "status") {
        if (this.chat) this.chat.status = m.status;
        this.announce();
        this.scheduleRefresh();
      } else if (m.type === "action") {
        this.#upsert(m.action);
        this.#broadcast({ type: "action", action: m.action });
        this.scheduleRefresh();
      } else if (m.type === "reminder") {
        this.scheduleRefresh();
      }
    };
    ws.onclose = (e) => {
      clearInterval(keepalive);
      if (this.#socket !== ws) return; // closed on purpose; a newer state already took over
      this.#socket = null;
      if (e.code === 4401) {
        void this.revoked();
        return;
      }
      if (!this.pair) return;
      this.#setLink("offline");
      this.#scheduleRetry();
    };
  }

  #scheduleRetry(): void {
    if (this.tabs.size === 0 && popups.size === 0) return;
    const delay = RETRY_MS[Math.min(this.#retry++, RETRY_MS.length - 1)];
    this.#retryTimer = setTimeout(() => this.ensureSocket(), delay);
  }

  closeSocket(): void {
    clearTimeout(this.#retryTimer);
    const ws = this.#socket;
    this.#socket = null;
    ws?.close();
  }

  idleSoon(): void {
    if (this.tabs.size > 0 || popups.size > 0) return;
    this.#idleTimer = setTimeout(() => {
      this.closeSocket();
      if (this.pair) this.link = "connecting";
    }, IDLE_CLOSE_MS);
  }

  /** Point at a new pair: the same Motwi somewhere else, or a fresh pairing of this browser. */
  async repoint(pair: Pair): Promise<void> {
    this.closeSocket();
    this.pair = pair;
    this.#retry = 0;
    this.chat = null;
    this.link = "connecting";
    await save();
    this.ensureSocket();
    this.announce();
  }

  async revoked(): Promise<void> {
    if (!this.pair) return;
    this.pair = null;
    this.closeSocket();
    conns.delete(this);
    await save();
    this.chat = null;
    this.link = "revoked";
    this.announce();
  }

  /** Disconnect on purpose, telling that Motwi if it can be reached. */
  async leave(): Promise<void> {
    const pair = this.pair;
    if (pair) {
      const path = this.role === "mod" ? "/mod/leave" : "/api/extension/leave";
      await fetch(`${pair.origin}${path}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${pair.token}` },
      }).catch(() => {});
    }
    this.pair = null;
    this.closeSocket();
    conns.delete(this);
    await save();
    this.chat = null;
    this.link = "unpaired";
    this.announce();
  }

  async act(m: Extract<PortRequest, { type: "act" }>): Promise<Action> {
    const action = await this.call<Action>(
      "POST",
      `/actions/${m.id}/${m.verb}`,
      m.asWarn ? { as: "warn" } : {},
    );
    this.#upsert(action);
    this.#broadcast({ type: "action", action });
    return action;
  }

  async setPaused(paused: boolean): Promise<void> {
    if (this.role === "mod") throw new Refused("Only the streamer can pause Motwi.");
    const status = await this.call<Chat["status"]>("POST", "/engine", { paused });
    if (this.chat) this.chat.status = status;
    this.announce();
  }
}

interface Answer {
  channel: string | null;
  known?: boolean;
  proof?: string;
  expected?: Promise<string>;
}

/** What's at this address, if it's a Motwi at all. With a pair, it also has to prove itself. */
async function askMotwi(origin: string, pair?: Pair): Promise<Answer | null> {
  const nonce = newNonce();
  const query = pair?.clientId ? `?client=${encodeURIComponent(pair.clientId)}&nonce=${nonce}` : "";
  try {
    const resp = await fetch(`${origin}/mod/hello${query}`);
    if (!resp.ok) return null;
    const data = await resp.json();
    if (data?.app !== "motwi") return null;
    return {
      channel: data.channel ?? null,
      known: data.known,
      proof: data.proof,
      expected: pair ? expectedProof(pair.token, origin, nonce) : undefined,
    };
  } catch {
    return null; // nothing there, or Cloudflare's error page for a tunnel that's gone
  }
}

/** Only a tunnel's address, or this machine for testing an invite locally. */
function validOrigin(text: string): string | null {
  try {
    const url = new URL(text);
    const tunnel = url.protocol === "https:" && url.hostname.endsWith(".trycloudflare.com");
    const local = url.protocol === "http:" && url.hostname === "127.0.0.1";
    return tunnel || local ? url.origin : null;
  } catch {
    return null;
  }
}

function connFor(channel: string): Conn | undefined {
  return [...conns].find((c) => c.pair?.channel === channel);
}

// ports from Twitch tabs and the toolbar popup

chrome.runtime.onConnect.addListener((port) => {
  if (!port.name.startsWith(PORT_NAME)) return;
  const channel = port.name.slice(PORT_NAME.length + 1) || null;
  void ready.then(() => {
    if (channel === null) {
      popups.add(port);
      port.onDisconnect.addListener(() => {
        popups.delete(port);
        for (const c of conns) c.idleSoon();
      });
      port.postMessage({
        type: "list",
        items: [...conns].map((c) => c.snapshot()),
      } satisfies PortEvent);
      for (const c of conns) c.ensureSocket();
      return;
    }
    const conn = connFor(channel);
    if (!conn) {
      port.postMessage({
        type: "snapshot",
        snapshot: { link: "unpaired", channel: null, chat: null, role: "own" },
      } satisfies PortEvent);
      return;
    }
    conn.tabs.add(port);
    port.onDisconnect.addListener(() => {
      conn.tabs.delete(port);
      conn.idleSoon();
    });
    port.onMessage.addListener((m: PortRequest) => void handle(conn, port, m));
    port.postMessage({ type: "snapshot", snapshot: conn.snapshot() } satisfies PortEvent);
    conn.ensureSocket();
  });
});

async function handle(conn: Conn, port: chrome.runtime.Port, m: PortRequest): Promise<void> {
  const reply = (r: Omit<Extract<PortEvent, { type: "result" }>, "type" | "req">) => {
    if (conn.tabs.has(port)) port.postMessage({ type: "result", req: m.req, ...r });
  };
  try {
    if (m.type === "act") {
      reply({ ok: true, action: await conn.act(m) });
    } else {
      await conn.setPaused(m.paused);
      reply({ ok: true });
    }
    conn.scheduleRefresh();
  } catch (e) {
    reply({ ok: false, error: e instanceof Error ? e.message : String(e) });
  }
}

// one-shot messages

function browserName(): string {
  const nav = navigator as Navigator & {
    brave?: unknown;
    userAgentData?: { brands: { brand: string }[] };
  };
  if (nav.brave) return "Brave";
  const brands = nav.userAgentData?.brands.map((b) => b.brand) ?? [];
  for (const [brand, name] of [
    ["Microsoft Edge", "Edge"],
    ["Opera", "Opera"],
    ["Google Chrome", "Chrome"],
  ]) {
    if (brands.includes(brand)) return name;
  }
  return "Chromium";
}

async function tellTabs(): Promise<void> {
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (tab.id !== undefined) void chrome.tabs.sendMessage(tab.id, PAIRED).catch(() => {});
  }
}

// the streamer's own browser

async function redeem(code: string, pageUrl: string | undefined): Promise<PairResult> {
  const url = pageUrl ? new URL(pageUrl) : null;
  // the manifest only runs the bridge here, and sender.url can't be forged by the page
  if (!url || url.hostname !== "127.0.0.1" || url.pathname !== "/pair.html") {
    return { ok: false, error: "That page can't connect this browser." };
  }
  const origin = `http://127.0.0.1:${url.port}`;
  let resp: Response;
  try {
    resp = await fetch(`${origin}/api/pair`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, label: browserName() }),
    });
  } catch {
    return { ok: false, error: "Motwi isn't running." };
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) return { ok: false, error: data.error ?? "Motwi refused the link." };

  const next: Pair = { role: "own", origin, token: data.token, channel: data.channel ?? null };
  const own = [...conns].find((c) => c.role === "own");
  if (own?.pair && own.pair.token !== next.token) {
    // this browser's old entry shouldn't linger in Motwi's list
    void fetch(`${own.pair.origin}/api/extension/leave`, {
      method: "POST",
      headers: { Authorization: `Bearer ${own.pair.token}` },
    }).catch(() => {});
  }
  // being the streamer outranks modding the same channel
  const asMod = next.channel ? connFor(next.channel) : undefined;
  if (asMod && asMod !== own) await asMod.leave();
  if (own) await own.repoint(next);
  else {
    const conn = new Conn(next);
    conns.add(conn);
    await save();
    conn.ensureSocket();
    conn.announce();
  }
  await tellTabs();
  return { ok: true, channel: next.channel };
}

// a mod's browser

/** The bridge on a join page: carry on in the extension's own page, which no site can touch. */
async function openJoinPage(
  code: string | null,
  pageUrl: string | undefined,
  tabId: number | undefined,
): Promise<void> {
  const url = pageUrl ? new URL(pageUrl) : null;
  const origin = url && url.pathname === "/motwi-join" ? validOrigin(url.origin) : null;
  if (!origin || tabId === undefined) return;
  const params = new URLSearchParams({ o: origin });
  if (code) params.set("c", code);
  await chrome.tabs.update(tabId, { url: `${chrome.runtime.getURL("join.html")}#${params}` });
}

function fromJoinPage(sender: chrome.runtime.MessageSender): boolean {
  return !!sender.url?.startsWith(chrome.runtime.getURL("join.html"));
}

async function linkInfo(text: string): Promise<LinkInfo> {
  const origin = validOrigin(text);
  if (!origin) return { state: "unreachable", channel: null };
  const here = await askMotwi(origin);
  if (!here) return { state: "unreachable", channel: null };
  const conn = here.channel ? connFor(here.channel) : undefined;
  if (!conn?.pair) return { state: "new", channel: here.channel };
  if (conn.role === "own") return { state: "own", channel: here.channel };
  // already this channel's mod: if this really is their Motwi, just move, with no new sign-in
  const pair = conn.pair;
  const proven = await askMotwi(origin, pair);
  if (proven?.proof && proven.proof === (await proven.expected)) {
    await conn.repoint({ ...pair, origin });
    await tellTabs();
    return { state: "moved", channel: here.channel };
  }
  return { state: "new", channel: here.channel };
}

async function join(text: string, code: string, twitchToken: string): Promise<JoinResult> {
  const origin = validOrigin(text);
  if (!origin) return { ok: false, error: "That isn't a Motwi link." };
  let resp: Response;
  try {
    resp = await fetch(`${origin}/mod/join`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, twitch_token: twitchToken, label: browserName() }),
    });
  } catch {
    return { ok: false, error: "Can't reach Motwi. Ask the streamer for a new link." };
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) return { ok: false, error: data.error ?? "Motwi refused the invite." };

  const next: Pair = {
    role: "mod",
    origin,
    token: data.token,
    channel: data.channel ?? null,
    clientId: data.client_id,
  };
  const existing = next.channel ? connFor(next.channel) : undefined;
  if (existing) await existing.leave();
  const conn = new Conn(next);
  conns.add(conn);
  await save();
  conn.announce();
  await tellTabs();
  return { ok: true, channel: next.channel, login: data.login };
}

chrome.runtime.onMessage.addListener((m: Request, sender, respond) => {
  if (sender.id !== chrome.runtime.id) return false;
  void ready.then(async () => {
    switch (m.type) {
      case "whoami":
        respond({
          channels: [...conns].map((c) => c.pair?.channel).filter((c): c is string => !!c),
        });
        break;
      case "pair":
        respond(await redeem(m.code, sender.url));
        break;
      case "forget": {
        const conn = [...conns].find((c) => (c.pair?.channel ?? null) === m.channel);
        await conn?.leave();
        respond({ ok: true });
        break;
      }
      case "join-link":
        await openJoinPage(m.code, sender.url, sender.tab?.id);
        respond({ ok: true });
        break;
      case "link-info":
        respond(fromJoinPage(sender) ? await linkInfo(m.origin) : { state: "unreachable", channel: null });
        break;
      case "join":
        respond(
          fromJoinPage(sender)
            ? await join(m.origin, m.code, m.twitchToken)
            : { ok: false, error: "That page can't do this." },
        );
        break;
    }
  });
  return true;
});
