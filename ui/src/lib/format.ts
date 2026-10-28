import type { Action, Ceiling, Kind } from "./types";

export const KIND_COLOR: Record<string, string> = {
  delete: "var(--del)",
  warn: "var(--warn)",
  timeout: "var(--timeout)",
  ban_request: "var(--ban)",
  ban: "var(--ban)",
};

export const CEILING_LABEL: Record<Ceiling, string> = {
  delete: "Delete",
  warn: "Warn",
  timeout: "Up to timeout",
  ban: "Up to ban",
};

export const CEILING_HELP: Record<Ceiling, string> = {
  delete: "Only deletes the message",
  warn: "Deletes and warns, never more",
  timeout: "Can escalate to timeouts",
  ban: "Can escalate to a ban request",
};

export function duration(s: number | null): string {
  if (!s) return "";
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

export function kindLabel(kind: Kind, durationS: number | null = null): string {
  switch (kind) {
    case "delete":
      return "Deleted";
    case "warn":
      return "Warned";
    case "timeout":
      return `Timeout ${duration(durationS)}`.trim();
    case "ban_request":
      return "Ban";
    default:
      return "Nothing";
  }
}

export function wouldDo(kind: Kind, durationS: number | null): string {
  switch (kind) {
    case "delete":
      return "Delete the message";
    case "warn":
      return "Delete and warn";
    case "timeout":
      return `Delete and time out for ${duration(durationS)}`;
    case "ban_request":
      return "Delete and ask you to ban";
    default:
      return "Nothing";
  }
}

export function statusLabel(a: Action): string {
  switch (a.status) {
    case "suggested":
      return "Suggested";
    case "pending":
      return "Waiting for an answer";
    case "expired":
      return "Hold ended, still open";
    case "done":
      return a.kind === "ban_request" ? "Banned" : "Done";
    case "failed":
      return "Failed";
    case "undone":
      return a.kind === "delete" || a.kind === "warn" ? "Marked as mistake" : "Undone";
    case "denied":
      return "Denied";
    case "ignored":
      return "Dismissed";
  }
}

export function sourceLabel(a: Action): string {
  if (a.source === "spam") return "Spam filter";
  if (a.source === "blocked_word") return "Blocked word";
  return "AI";
}

export function ago(ts: number, now = Date.now() / 1000): string {
  const d = Math.max(0, now - ts);
  if (d < 45) return "just now";
  if (d < 3600) return `${Math.round(d / 60)}m ago`;
  if (d < 86400) return `${Math.round(d / 3600)}h ago`;
  if (d < 86400 * 7) return `${Math.round(d / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

export function clock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function when(ts: number): string {
  const d = new Date(ts * 1000);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  return sameDay
    ? clock(ts)
    : d.toLocaleDateString([], { month: "short", day: "numeric" }) + ", " + clock(ts);
}

export function money(usd: number | undefined | null): string {
  const v = usd ?? 0;
  if (v > 0 && v < 0.01) return "<$0.01";
  return `$${v.toFixed(2)}`;
}

export function count(n: number | undefined | null): string {
  return (n ?? 0).toLocaleString("en-US");
}

export function score(s: number | null): string {
  return s === null ? "" : s.toFixed(1);
}

export const PROVIDER_LABEL: Record<string, string> = {
  gemini: "Gemini",
  openrouter: "OpenRouter",
  custom: "Custom",
};

export function modelLabel(model: string): string {
  // "google/gemini-2.5-flash-lite" -> "Gemini 2.5 Flash Lite"
  const name = model.includes("/") ? model.split("/").pop()! : model;
  return name
    .split(/[-_]/)
    .map((w) => (/^[a-z]/.test(w) ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}
