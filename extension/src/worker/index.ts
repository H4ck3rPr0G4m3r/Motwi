// The service worker: the only code holding the token or talking to the core.
//
// It keeps one WebSocket open while any Twitch tab on the paired channel is connected. A
// keepalive every 20 s stops Chrome from suspending the worker meanwhile. If Chrome stops it
// anyway, the tabs' ports drop, they reconnect, and the new worker starts over from storage.

import {
  PAIRED,
  PORT_NAME,
  type Action,
  type Chat,
  type Link,
  type PairResult,
  type PortEvent,
  type PortRequest,
  type Request,
  type Snapshot,
} from "../shared/protocol";

interface Pair {
  port: number;
  token: string;
  channel: string | null;
}

const KEY = "pair";
const KEEPALIVE_MS = 20_000;
const IDLE_CLOSE_MS = 30_000;
const REFRESH_DELAY_MS = 250;
const RETRY_MS = [1_000, 2_000, 5_000, 10_000, 15_000];

let pair: Pair | null = null;
let link: Link = "unpaired";
let chat: Chat | null = null;
const ports = new Set<chrome.runtime.Port>();

let socket: WebSocket | null = null;
let retry = 0;
let retryTimer: ReturnType<typeof setTimeout> | undefined;
let idleTimer: ReturnType<typeof setTimeout> | undefined;
let refreshTimer: ReturnType<typeof setTimeout> | undefined;

chrome.storage.local.setAccessLevel?.({ accessLevel: "TRUSTED_CONTEXTS" });

const ready: Promise<void> = chrome.storage.local.get(KEY).then((stored) => {
  pair = (stored[KEY] as Pair | undefined) ?? null;
  link = pair ? "connecting" : "unpaired";
});

function base(): string {
  return `http://127.0.0.1:${pair!.port}`;
}

function snapshot(): Snapshot {
  return { link, channel: pair?.channel ?? null, chat };
}

function broadcast(event: PortEvent): void {
  for (const port of ports) port.postMessage(event);
}

function setLink(next: Link): void {
  if (next !== link) {
    link = next;
    broadcast({ type: "snapshot", snapshot: snapshot() });
  }
}

async function savePair(next: Pair | null): Promise<void> {
  pair = next;
  if (next) await chrome.storage.local.set({ [KEY]: next });
  else await chrome.storage.local.remove(KEY);
}

// HTTP

class Refused extends Error {}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (!pair) throw new Refused("This browser isn't connected to Motwi.");
  let resp: Response;
  try {
    resp = await fetch(`${base()}/api${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${pair.token}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error("Motwi isn't running.");
  }
  const data = await resp.json().catch(() => ({}));
  if (resp.status === 401) {
    await revoked();
    throw new Refused("This browser was disconnected from Motwi.");
  }
  if (!resp.ok) throw new Error(data.error ?? `Motwi refused that (${resp.status}).`);
  return data as T;
}

async function refresh(): Promise<void> {
  try {
    const next = await call<Chat>("GET", "/chat");
    chat = next;
    const login = next.status.accounts.streamer?.login ?? null;
    if (login && pair && login !== pair.channel) await savePair({ ...pair, channel: login });
    broadcast({ type: "snapshot", snapshot: snapshot() });
  } catch {
    // the socket decides whether the core is gone; a failed read just waits for the next one
  }
}

function scheduleRefresh(): void {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refresh, REFRESH_DELAY_MS);
}

function upsert(action: Action): void {
  if (!chat) return;
  const at = chat.recent.findIndex((a) => a.id === action.id);
  if (at >= 0) chat.recent[at] = action;
  else chat.recent.unshift(action);
}

// the socket

function ensureSocket(): void {
  clearTimeout(idleTimer);
  if (!pair || socket || ports.size === 0) return;
  clearTimeout(retryTimer);
  const ws = new WebSocket(`ws://127.0.0.1:${pair.port}/api/events`);
  socket = ws;
  let keepalive: ReturnType<typeof setInterval> | undefined;
  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "hello", token: pair?.token }));
    keepalive = setInterval(() => ws.send('{"type":"ping"}'), KEEPALIVE_MS);
  };
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data as string);
    if (m.type === "hello") {
      retry = 0;
      if (chat) chat.status = m.status;
      setLink("open");
      void refresh();
    } else if (m.type === "status") {
      if (chat) chat.status = m.status;
      broadcast({ type: "snapshot", snapshot: snapshot() });
      scheduleRefresh();
    } else if (m.type === "action") {
      upsert(m.action);
      broadcast({ type: "action", action: m.action });
      scheduleRefresh();
    } else if (m.type === "reminder") {
      scheduleRefresh();
    }
  };
  ws.onclose = (e) => {
    clearInterval(keepalive);
    if (socket !== ws) return; // closed on purpose; a newer state already took over
    socket = null;
    if (e.code === 4401) {
      void revoked();
      return;
    }
    if (!pair) return;
    setLink("offline");
    if (ports.size > 0) {
      const delay = RETRY_MS[Math.min(retry++, RETRY_MS.length - 1)];
      retryTimer = setTimeout(ensureSocket, delay);
    }
  };
}

function closeSocket(): void {
  clearTimeout(retryTimer);
  const ws = socket;
  socket = null;
  ws?.close();
}

async function revoked(): Promise<void> {
  if (!pair) return;
  await savePair(null);
  closeSocket();
  chat = null;
  link = "revoked";
  broadcast({ type: "snapshot", snapshot: snapshot() });
}

// ports from Twitch tabs and the toolbar popup

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== PORT_NAME) return;
  ports.add(port);
  port.onDisconnect.addListener(() => {
    ports.delete(port);
    if (ports.size === 0) {
      idleTimer = setTimeout(() => {
        closeSocket();
        if (pair) link = "connecting";
      }, IDLE_CLOSE_MS);
    }
  });
  port.onMessage.addListener((m: PortRequest) => void handle(port, m));
  void ready.then(() => {
    port.postMessage({ type: "snapshot", snapshot: snapshot() } satisfies PortEvent);
    ensureSocket();
  });
});

async function handle(port: chrome.runtime.Port, m: PortRequest): Promise<void> {
  await ready;
  const reply = (r: Omit<Extract<PortEvent, { type: "result" }>, "type" | "req">) => {
    if (ports.has(port)) port.postMessage({ type: "result", req: m.req, ...r });
  };
  try {
    if (m.type === "act") {
      const action = await call<Action>(
        "POST",
        `/actions/${m.id}/${m.verb}`,
        m.asWarn ? { as: "warn" } : {},
      );
      upsert(action);
      broadcast({ type: "action", action });
      reply({ ok: true, action });
    } else {
      const status = await call<Chat["status"]>("POST", "/engine", { paused: m.paused });
      if (chat) chat.status = status;
      broadcast({ type: "snapshot", snapshot: snapshot() });
      reply({ ok: true });
    }
    scheduleRefresh();
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

  const previous = pair;
  closeSocket();
  retry = 0;
  await savePair({ port: Number(url.port), token: data.token, channel: data.channel ?? null });
  chat = null;
  link = "connecting";
  if (previous && previous.token !== data.token) {
    // this browser's old entry shouldn't linger in Motwi's list
    void fetch(`http://127.0.0.1:${previous.port}/api/extension/leave`, {
      method: "POST",
      headers: { Authorization: `Bearer ${previous.token}` },
    }).catch(() => {});
  }
  ensureSocket();
  broadcast({ type: "snapshot", snapshot: snapshot() });
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (tab.id !== undefined) void chrome.tabs.sendMessage(tab.id, PAIRED).catch(() => {});
  }
  return { ok: true, channel: pair?.channel ?? null };
}

async function forget(): Promise<void> {
  if (pair) {
    await fetch(`${base()}/api/extension/leave`, {
      method: "POST",
      headers: { Authorization: `Bearer ${pair.token}` },
    }).catch(() => {});
  }
  await savePair(null);
  closeSocket();
  chat = null;
  link = "unpaired";
  broadcast({ type: "snapshot", snapshot: snapshot() });
}

chrome.runtime.onMessage.addListener((m: Request, sender, respond) => {
  if (sender.id !== chrome.runtime.id) return false;
  void ready.then(async () => {
    if (m.type === "whoami") respond({ channel: pair?.channel ?? null });
    else if (m.type === "pair") respond(await redeem(m.code, sender.url));
    else if (m.type === "forget") respond(await forget());
  });
  return true;
});
