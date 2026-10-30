// What this tab knows, fed by the worker. Actions change only when the core says so: a
// button goes busy and stays busy until the result arrives, so the UI never claims early.

import {
  PORT_NAME,
  type Action,
  type Chat,
  type Link,
  type PortEvent,
  type PortRequest,
  type Verb,
} from "../shared/protocol";

const RECEIPT_MS = 8_000;
const OPEN = new Set(["pending", "expired"]);
/** How long a snapshot that lacks a new request is assumed to predate it. */
const SNAPSHOT_RACE_S = 5;

export interface Receipt {
  id: number;
  action: Action;
}

class ChatStore {
  link = $state<Link>("connecting");
  channel = $state<string | null>(null);
  chat = $state<Omit<Chat, "recent" | "open_requests"> | null>(null);
  actions = $state<Record<number, Action>>({});
  /** Requests whose ban failed: the pop-up stays until retried or closed. */
  failed = $state<number[]>([]);
  receipts = $state<Receipt[]>([]);
  busy = $state<Record<number, Verb>>({});
  errors = $state<Record<number, string>>({});
  switching = $state(false);
  switchError = $state<string | null>(null);
  onChange: () => void = () => {};

  #port: chrome.runtime.Port | null = null;
  #req = 0;
  #pending = new Map<number, (ok: boolean, error?: string) => void>();
  #timers = new Set<ReturnType<typeof setTimeout>>();

  get status() {
    return this.chat?.status ?? null;
  }

  get online(): boolean {
    return this.link === "open";
  }

  /** Open ban requests, oldest first, then any whose ban failed. */
  get requests(): Action[] {
    return Object.values(this.actions)
      .filter(
        (a) =>
          a.kind === "ban_request" && (OPEN.has(a.status) || this.failed.includes(a.id)),
      )
      .sort((x, y) => x.id - y.id);
  }

  get log(): Action[] {
    return Object.values(this.actions).sort((x, y) => y.id - x.id);
  }

  connect(onLost: () => void): void {
    const port = chrome.runtime.connect({ name: PORT_NAME });
    this.#port = port;
    port.onMessage.addListener((m: PortEvent) => this.#receive(m));
    port.onDisconnect.addListener(() => {
      this.#port = null;
      for (const resolve of this.#pending.values()) resolve(false, "Lost contact with Motwi.");
      this.#pending.clear();
      onLost();
    });
  }

  disconnect(): void {
    const port = this.#port;
    this.#port = null;
    try {
      port?.disconnect();
    } catch {
      // the extension was updated or removed; the port is already gone
    }
    for (const t of this.#timers) clearTimeout(t);
    this.#timers.clear();
    this.receipts = [];
  }

  #receive(m: PortEvent): void {
    if (m.type === "snapshot") {
      this.link = m.snapshot.link;
      this.channel = m.snapshot.channel;
      if (m.snapshot.chat) {
        const { recent, open_requests, ...rest } = m.snapshot.chat;
        this.chat = rest;
        for (const a of [...recent, ...open_requests]) this.#upsert(a);
        this.#dropAnsweredElsewhere(open_requests);
      } else if (m.snapshot.link !== "offline") {
        this.chat = null;
      }
      this.onChange();
    } else if (m.type === "action") {
      this.#upsert(m.action);
      this.onChange();
    } else {
      const resolve = this.#pending.get(m.req);
      this.#pending.delete(m.req);
      resolve?.(m.ok, m.error);
    }
  }

  /** A request answered while this tab was out of touch, too old to be in `recent`. */
  #dropAnsweredElsewhere(open: Action[]): void {
    const still = new Set(open.map((a) => a.id));
    const now = Date.now() / 1000;
    for (const a of Object.values(this.actions)) {
      if (OPEN.has(a.status) && !still.has(a.id) && now - a.updated_at > SNAPSHOT_RACE_S) {
        delete this.actions[a.id];
      }
    }
  }

  #upsert(next: Action): void {
    const prev = this.actions[next.id];
    if (prev && prev.updated_at > next.updated_at) return; // a slower snapshot; keep the newer
    this.actions[next.id] = next;
    if (next.kind !== "ban_request" || !prev) return;
    const wasOpen = OPEN.has(prev.status) || this.failed.includes(next.id);
    if (next.status === "failed" && wasOpen) {
      if (!this.failed.includes(next.id)) this.failed.push(next.id);
    } else if (wasOpen && (next.status === "done" || next.status === "denied")) {
      this.failed = this.failed.filter((id) => id !== next.id);
      this.#addReceipt(next);
    }
  }

  #addReceipt(action: Action): void {
    this.receipts = [...this.receipts.filter((r) => r.id !== action.id), { id: action.id, action }];
    const timer = setTimeout(() => {
      this.#timers.delete(timer);
      this.receipts = this.receipts.filter((r) => r.id !== action.id);
    }, RECEIPT_MS);
    this.#timers.add(timer);
  }

  dismissFailed(id: number): void {
    this.failed = this.failed.filter((x) => x !== id);
    delete this.errors[id];
  }

  #send(m: Omit<PortRequest, "req"> & Record<string, unknown>): Promise<{ ok: boolean; error?: string }> {
    return new Promise((resolve) => {
      if (!this.#port) {
        resolve({ ok: false, error: "Lost contact with Motwi." });
        return;
      }
      const req = ++this.#req;
      this.#pending.set(req, (ok, error) => resolve({ ok, error }));
      this.#port.postMessage({ ...m, req } as PortRequest);
    });
  }

  async act(id: number, verb: Verb, asWarn = false): Promise<void> {
    if (this.busy[id]) return;
    this.busy[id] = verb;
    delete this.errors[id];
    const { ok, error } = await this.#send({ type: "act", id, verb, asWarn });
    delete this.busy[id];
    if (!ok) this.errors[id] = error ?? "That didn't work.";
    else if (verb === "undo") this.receipts = this.receipts.filter((r) => r.id !== id);
    this.onChange();
  }

  async setPaused(paused: boolean): Promise<void> {
    if (this.switching) return;
    this.switching = true;
    this.switchError = null;
    const { ok, error } = await this.#send({ type: "pause", paused });
    this.switching = false;
    if (!ok) this.switchError = error ?? "That didn't work.";
  }
}

export const store = new ChatStore();
