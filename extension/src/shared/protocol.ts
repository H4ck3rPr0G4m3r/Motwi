// Messages between the worker, which alone holds the token, and the pages it serves.

import type { Action, Status, Warning } from "../../../ui/src/lib/types";

export type { Action, Status, Warning };

export type Verb = "approve" | "deny" | "undo" | "retry";

export interface Counts {
  delete: number;
  warn: number;
  timeout: number;
  ban: number;
  suggested: number;
}

export interface Chat {
  status: Status;
  counts: Counts;
  recent: Action[];
  open_requests: Action[];
  warnings: Warning[];
}

/**
 * - unpaired: no token here.
 * - connecting: first attempt at the core.
 * - open: connected and authenticated.
 * - offline: the core can't be reached, usually because Motwi isn't running.
 * - revoked: the core refused this token; it was removed in Motwi's Settings.
 */
export type Link = "unpaired" | "connecting" | "open" | "offline" | "revoked";

export interface Snapshot {
  link: Link;
  channel: string | null;
  chat: Chat | null;
}

// long-lived port from a Twitch tab or the toolbar popup
export type PortRequest =
  | { type: "act"; req: number; id: number; verb: Verb; asWarn?: boolean }
  | { type: "pause"; req: number; paused: boolean };

export type PortEvent =
  | { type: "snapshot"; snapshot: Snapshot }
  | { type: "action"; action: Action }
  | { type: "result"; req: number; ok: boolean; error?: string; action?: Action };

// one-shot messages
export type Request =
  | { type: "whoami" }
  | { type: "pair"; code: string }
  | { type: "forget" };

export interface WhoAmI {
  channel: string | null;
}

export type PairResult = { ok: true; channel: string | null } | { ok: false; error: string };

/** Sent to tabs after pairing, so an open Twitch tab activates without a reload. */
export const PAIRED = "motwi-paired";

export const PORT_NAME = "motwi";
