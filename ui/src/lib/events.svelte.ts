// One WebSocket to the core: status snapshots and live events.

import { getToken } from "./api";
import type { Action, Role, Status } from "./types";

export type CoreMessage =
  | { type: "hello"; status: Status }
  | { type: "status"; status: Status }
  | { type: "action"; action: Action }
  | { type: "reminder"; action: Action }
  | { type: "extension" }
  | { type: "login"; role: Role; ok: boolean; login: string | null; error: string | null };

export const core = $state({
  status: null as Status | null,
  link: "connecting" as "connecting" | "open" | "lost" | "unauthorized",
});

const listeners = new Set<(m: CoreMessage) => void>();

export function onCore(fn: (m: CoreMessage) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

let retry = 0;

export function connectCore(): void {
  const ws = new WebSocket(`ws://${location.host}/api/events`);
  ws.onopen = () => ws.send(JSON.stringify({ type: "hello", token: getToken() }));
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data) as CoreMessage;
    if (m.type === "hello" || m.type === "status") {
      core.status = m.status;
      core.link = "open";
      retry = 0;
    }
    for (const fn of listeners) fn(m);
  };
  ws.onclose = (e) => {
    if (e.code === 4401) {
      core.link = "unauthorized";
      return;
    }
    core.link = core.status ? "lost" : "connecting";
    const delay = Math.min(10_000, 500 * 2 ** retry++);
    setTimeout(connectCore, delay);
  };
}

export function setStatus(s: Status): void {
  core.status = s;
}
