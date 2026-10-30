// Pairs chat lines with Motwi's actions. An exact message ID wins. A line with no ID (Twitch
// changed its internals, say) falls back to login + text, and only for recent actions no
// other line has already claimed. Anything unmatched still shows in the bar's log.

import type { Action } from "../shared/protocol";

export interface LineInfo {
  id: string | null;
  login: string | null;
  text: string;
}

const FALLBACK_WINDOW_S = 15 * 60;

export function normalize(text: string): string {
  return text.replace(/\s+/g, " ").trim().toLowerCase();
}

/** Returns, per line index, the action on that line. */
export function matchLines(
  lines: LineInfo[],
  actions: Iterable<Action>,
  now = Date.now() / 1000,
): Map<number, Action> {
  const byMessage = new Map<string, Action>();
  const all: Action[] = [];
  for (const a of actions) {
    all.push(a);
    const current = byMessage.get(a.message_id);
    if (!current || a.id > current.id) byMessage.set(a.message_id, a);
  }

  const result = new Map<number, Action>();
  const claimed = new Set<number>();
  lines.forEach((line, i) => {
    const hit = line.id ? byMessage.get(line.id) : undefined;
    if (hit) {
      result.set(i, hit);
      claimed.add(hit.id);
    }
  });

  // newest line first, so a repeated message marks the one that most likely got the action
  const recent = all
    .filter((a) => !claimed.has(a.id) && now - a.created_at <= FALLBACK_WINDOW_S)
    .sort((x, y) => y.id - x.id);
  for (let i = lines.length - 1; i >= 0 && recent.length; i--) {
    const line = lines[i];
    if (line.id || !line.login) continue;
    const text = normalize(line.text);
    const login = line.login.toLowerCase();
    const at = recent.findIndex(
      (a) => a.user_login.toLowerCase() === login && normalize(a.message_text) === text,
    );
    if (at >= 0) {
      result.set(i, recent[at]);
      recent.splice(at, 1);
    }
  }
  return result;
}
