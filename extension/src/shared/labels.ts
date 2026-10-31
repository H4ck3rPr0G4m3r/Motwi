// What the bar, the marks and the log say about Motwi's state and actions. Pure, so tests
// can pin the wording that promises honesty: nothing reads as done before the core says so.

import { duration, modelLabel } from "../../../ui/src/lib/format";
import type { Action, Counts, Link, Role, Status, Verb } from "./protocol";

export interface BarView {
  label: string;
  sub: string;
  tone: "on" | "off" | "bad";
  /** The switch can only be flipped when the engine is actually running. */
  canSwitch: boolean;
  suggesting: boolean;
}

export function modelName(s: Status): string {
  const ai = s.ai.state === "degraded" && s.ai.backup ? s.ai.backup : (s.ai.primary ?? s.ai.backup);
  return ai ? modelLabel(ai.model) : "No AI set up";
}

export function barView(link: Link, status: Status | null, role: Role = "own"): BarView {
  if (role === "mod") {
    const view = streamerView(link, status, true);
    return { ...view, canSwitch: false };
  }
  return streamerView(link, status, false);
}

function streamerView(link: Link, status: Status | null, mod: boolean): BarView {
  const off = (label: string, sub: string, tone: BarView["tone"] = "off"): BarView => ({
    label,
    sub,
    tone,
    canSwitch: false,
    suggesting: false,
  });
  if (link === "moved") return off("Motwi moved", "Ask the streamer for the new link", "bad");
  if (link === "offline") {
    return mod
      ? off("Can't reach Motwi", "Trying again", "bad")
      : off("Motwi isn't running", "Start Motwi to moderate", "bad");
  }
  if (link === "revoked") {
    return mod
      ? off("Disconnected", "Ask the streamer for a new invite", "bad")
      : off("Disconnected", "Connect again in Motwi's Settings", "bad");
  }
  if (link === "unpaired") return off("Not connected", "Connect from Motwi's Settings", "bad");
  if (link === "connecting" || !status) return off("Connecting", "Reaching Motwi");

  const model = modelName(status);
  switch (status.engine) {
    case "reconnect":
      return mod
        ? off("Twitch disconnected", "The streamer needs to reconnect it", "bad")
        : off("Reconnect Twitch", status.twitch.detail || "Open Motwi to sign in again", "bad");
    case "connecting":
      return off("Connecting to Twitch", status.twitch.detail || model);
    case "stopped":
      return mod
        ? off("Not watching", "The streamer hasn't started Motwi", "bad")
        : off("Not watching", status.twitch.detail || "Open Motwi to start", "bad");
    case "paused":
      return { label: "Paused", sub: model, tone: "off", canSwitch: true, suggesting: false };
    case "suggesting":
    case "watching": {
      const suggesting = status.engine === "suggesting";
      const down = status.ai.state === "down" || status.ai.state === "off";
      return {
        label: down ? "AI down" : suggesting ? "Suggesting" : "Watching",
        sub: model,
        tone: down ? "bad" : "on",
        canSwitch: true,
        suggesting,
      };
    }
  }
}

export interface CountPart {
  key: string;
  text: string;
  kind: "delete" | "warn" | "timeout" | "ban" | "suggested";
}

export function countParts(c: Counts | null): CountPart[] {
  if (!c) return [];
  const parts: CountPart[] = [];
  if (c.delete) parts.push({ key: "del", text: `${c.delete} del`, kind: "delete" });
  if (c.warn) parts.push({ key: "warn", text: `${c.warn} warn`, kind: "warn" });
  if (c.timeout) parts.push({ key: "to", text: `${c.timeout} to`, kind: "timeout" });
  if (c.ban) parts.push({ key: "ban", text: `${c.ban} ban`, kind: "ban" });
  if (c.suggested) parts.push({ key: "sugg", text: `${c.suggested} sugg`, kind: "suggested" });
  return parts;
}

function past(a: Action): string {
  switch (a.kind) {
    case "delete":
      return "Deleted";
    case "warn":
      return "Warned";
    case "timeout":
      return `Timed out ${duration(a.duration_s)}`.trim();
    case "ban_request":
      return "Banned";
    default:
      return "Nothing";
  }
}

function verbName(a: Action): string {
  switch (a.kind) {
    case "delete":
      return "delete";
    case "warn":
      return "warn";
    case "timeout":
      return `timeout ${duration(a.duration_s)}`.trim();
    case "ban_request":
      return "ban";
    default:
      return "nothing";
  }
}

/** The tag on a chat line. Spec 5.6: an undone delete can't come back, so it never says so. */
export function tagLabel(a: Action): string {
  switch (a.status) {
    case "suggested":
      return `Suggest ${verbName(a)}`;
    case "pending":
      return "Ban requested";
    case "expired":
      return "Ban request open";
    case "done":
      return past(a);
    case "failed":
      return `${verbName(a)[0].toUpperCase()}${verbName(a).slice(1)} failed`;
    case "undone":
      if (a.kind === "timeout") return "Timeout removed";
      if (a.kind === "ban_request") return "Unbanned";
      return "Marked as mistake";
    case "denied":
      return "Ban denied";
    case "ignored":
      return "Dismissed";
  }
}

export function tagTone(a: Action): "action" | "neutral" | "error" {
  if (a.status === "failed") return "error";
  if (a.status === "undone" || a.status === "ignored" || a.status === "denied") return "neutral";
  return "action";
}

/** Whether Twitch really removed the message, so the line can be struck through. */
export function isGone(a: Action): boolean {
  if (!a.delete) return false;
  return a.status === "done" || a.status === "pending" || a.status === "expired" || a.status === "denied";
}

export interface ActionButton {
  label: string;
  verb: Verb;
  asWarn?: boolean;
  primary?: boolean;
  title?: string;
}

/** Buttons on a line or a log row. Open ban requests are answered in the pop-up instead. */
export function buttonsFor(a: Action): ActionButton[] {
  switch (a.status) {
    case "suggested": {
      const primary: Record<string, string> = {
        delete: "Delete",
        warn: "Warn",
        timeout: "Timeout",
        ban_request: "Ban",
      };
      const buttons: ActionButton[] = [
        { label: primary[a.kind] ?? "Do it", verb: "approve", primary: true },
      ];
      if (a.kind === "timeout" || a.kind === "ban_request") {
        buttons.push({ label: "Warn instead", verb: "approve", asWarn: true });
      }
      buttons.push({ label: "Ignore", verb: "deny" });
      return buttons;
    }
    case "done":
      return [
        {
          label: "Undo",
          verb: "undo",
          title:
            a.kind === "delete" || a.kind === "warn"
              ? "Twitch can't take this back. Undo marks it as Motwi's mistake."
              : undefined,
        },
      ];
    case "failed":
      return [{ label: "Retry", verb: "retry", primary: true }];
    default:
      return [];
  }
}

export function busyLabel(verb: Verb, a: Action): string {
  switch (verb) {
    case "approve":
      return a.kind === "ban_request" ? "Banning" : "Working";
    case "deny":
      return a.status === "suggested" ? "Ignoring" : "Denying";
    case "undo":
      return "Undoing";
    case "retry":
      return "Retrying";
  }
}

/** Note beside the tag: who answered, or why it can't be undone on Twitch. */
export function statusNote(a: Action): string {
  if (a.status === "failed") return a.error ?? "Twitch refused it";
  if (a.status === "undone" && (a.kind === "delete" || a.kind === "warn")) {
    return "Twitch can't restore it";
  }
  if (a.actor && (a.status === "done" || a.status === "undone" || a.status === "denied")) {
    return `by ${a.actor}`;
  }
  return "";
}

const COUNTED = new Set(["done", "pending", "expired"]);

/** Which offense this is for its chatter, among the actions in view. 1 for the first. */
export function offenseNumber(a: Action, actions: Iterable<Action>): number {
  if (!COUNTED.has(a.status)) return 0;
  let n = 0;
  for (const other of actions) {
    if (
      other.user_id === a.user_id &&
      other.stream_id === a.stream_id &&
      other.id <= a.id &&
      COUNTED.has(other.status)
    ) {
      n++;
    }
  }
  return n;
}

export function ordinal(n: number): string {
  const rem = n % 100;
  if (rem >= 11 && rem <= 13) return `${n}th`;
  return `${n}${["th", "st", "nd", "rd"][n % 10] ?? "th"}`;
}

export function receiptText(a: Action): string {
  const by = a.actor ? ` by ${a.actor}` : "";
  if (a.status === "done") return `${a.user_login} banned${by}`;
  if (a.status === "denied") return `${a.user_login}'s ban denied${by}`;
  return `${a.user_login}: ${tagLabel(a)}`;
}

/** Who else is looking at a request, for the pop-up. */
export function alsoHere(names: string[] | undefined): string {
  if (!names?.length) return "";
  if (names.length === 1) return `${names[0]} is here too`;
  if (names.length === 2) return `${names[0]} and ${names[1]} are here too`;
  return `${names[0]} and ${names.length - 1} others are here too`;
}

export function requestFoot(a: Action): string {
  if (a.status === "expired") return "Hold ended. Still waiting for your answer";
  if (a.hold_applied) return "Held in timeout while you decide";
  return "Not held. They can keep chatting";
}
