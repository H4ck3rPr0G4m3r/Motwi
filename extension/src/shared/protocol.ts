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
  /** Who else has Motwi open in this chat right now. */
  present?: string[];
}

/**
 * - unpaired: no token here.
 * - connecting: first attempt at the core.
 * - open: connected and authenticated.
 * - offline: the core can't be reached, usually because Motwi isn't running.
 * - revoked: the core refused this token; it was removed in Motwi's Settings.
 * - moved: a mod's Motwi isn't at the address they knew. It changes whenever it restarts.
 */
export type Link = "unpaired" | "connecting" | "open" | "offline" | "revoked" | "moved";

/** own: this browser belongs to the streamer. mod: it helps moderate someone else's channel. */
export type Role = "own" | "mod";

export interface Snapshot {
  link: Link;
  channel: string | null;
  chat: Chat | null;
  role: Role;
}

// long-lived port: PORT_NAME:channel from a Twitch tab, or plain PORT_NAME from the popup
export type PortRequest =
  | { type: "act"; req: number; id: number; verb: Verb; asWarn?: boolean }
  | { type: "pause"; req: number; paused: boolean };

export type PortEvent =
  | { type: "snapshot"; snapshot: Snapshot }
  | { type: "list"; items: Snapshot[] }
  | { type: "action"; action: Action }
  | { type: "result"; req: number; ok: boolean; error?: string; action?: Action };

// one-shot messages
export type Request =
  | { type: "whoami" }
  | { type: "pair"; code: string }
  | { type: "forget"; channel: string | null }
  | { type: "join-link"; code: string | null }
  | { type: "link-info"; origin: string }
  | { type: "join"; origin: string; code: string; twitchToken: string };

export interface WhoAmI {
  channels: string[];
}

export type PairResult = { ok: true; channel: string | null } | { ok: false; error: string };

export interface LinkInfo {
  /** new: nothing here yet. moved: this browser just followed its Motwi to this address. */
  state: "unreachable" | "new" | "moved" | "own";
  channel: string | null;
}

export type JoinResult =
  | { ok: true; channel: string | null; login: string }
  | { ok: false; error: string };

/** Sent to tabs after pairing, so an open Twitch tab activates without a reload. */
export const PAIRED = "motwi-paired";

export const PORT_NAME = "motwi";
