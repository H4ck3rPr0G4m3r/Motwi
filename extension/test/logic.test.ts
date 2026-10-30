import { describe, expect, it } from "vitest";
import { matchLines, normalize, type LineInfo } from "../src/content/match";
import { channelFromUrl } from "../src/shared/channel";
import {
  barView,
  buttonsFor,
  countParts,
  isGone,
  offenseNumber,
  ordinal,
  receiptText,
  requestFoot,
  statusNote,
  tagLabel,
} from "../src/shared/labels";
import type { Action, Status } from "../src/shared/protocol";

const NOW = 2_000_000;

function action(over: Partial<Action> = {}): Action {
  return {
    id: 1,
    created_at: NOW - 5,
    updated_at: NOW - 5,
    stream_id: "s",
    user_id: "u1",
    user_login: "grizzlecat",
    message_id: "m1",
    message_text: "ur trash at this",
    context: [],
    source: "ai",
    rule_id: null,
    score: 7.8,
    reason: "Insults the streamer",
    kind: "timeout",
    delete: true,
    duration_s: 60,
    ladder_index: 1,
    status: "done",
    actor: null,
    hold_applied: false,
    error: null,
    level: 1,
    ...over,
  };
}

function status(over: Partial<Status> = {}): Status {
  return {
    version: "0",
    setup_complete: true,
    accounts: { streamer: { login: "me", user_id: "1" }, bot: null },
    moderator: "me",
    twitch: { state: "connected", detail: "" },
    stream: { live: true, title: null, game: null, stream_id: "s" },
    engine: "watching",
    suggest_only: false,
    paused: false,
    ai: {
      state: "ok",
      detail: "",
      primary: {
        provider: "gemini",
        model: "gemini-2.5-flash-lite",
        base_url: null,
        has_key: true,
        key_from_env: false,
      },
      backup: {
        provider: "openrouter",
        model: "google/gemini-2.0-flash",
        base_url: null,
        has_key: true,
        key_from_env: false,
      },
    },
    month_cost: 0,
    open_requests: 0,
    ...over,
  };
}

describe("channelFromUrl", () => {
  it.each([
    ["https://www.twitch.tv/SomeOne", "someone"],
    ["https://www.twitch.tv/someone/videos", "someone"],
    ["https://www.twitch.tv/popout/someone/chat?popout=", "someone"],
    ["https://www.twitch.tv/moderator/someone", "someone"],
    ["https://www.twitch.tv/embed/someone/chat?parent=x", "someone"],
    ["https://dashboard.twitch.tv/u/someone/stream-manager", "someone"],
    ["https://dashboard.twitch.tv/stream-manager", null],
    ["https://www.twitch.tv/", null],
    ["https://www.twitch.tv/popout", null],
    ["https://evil.example/someone", null],
    ["nonsense", null],
  ])("%s", (url, expected) => {
    expect(channelFromUrl(url)).toBe(expected);
  });
});

describe("matchLines", () => {
  const line = (id: string | null, login: string, text: string): LineInfo => ({ id, login, text });

  it("matches exactly by message id", () => {
    const a = action({ message_id: "abc" });
    const out = matchLines([line("other", "x", "hi"), line("abc", "grizzlecat", "changed")], [a], NOW);
    expect(out.get(1)).toBe(a);
    expect(out.has(0)).toBe(false);
  });

  it("never falls back for a line that has an id", () => {
    const a = action({ message_id: "abc" });
    expect(matchLines([line("different", "grizzlecat", a.message_text)], [a], NOW).size).toBe(0);
  });

  it("falls back to login and text, ignoring case and spacing", () => {
    const a = action();
    const out = matchLines([line(null, "GrizzleCat", "  UR  trash at   this ")], [a], NOW);
    expect(out.get(0)).toBe(a);
  });

  it("marks the newest line when a repeated message has one action", () => {
    const a = action();
    const out = matchLines(
      [line(null, "grizzlecat", a.message_text), line(null, "grizzlecat", a.message_text)],
      [a],
      NOW,
    );
    expect([...out.keys()]).toEqual([1]);
  });

  it("won't reuse an action a line already claimed by id", () => {
    const a = action({ message_id: "abc" });
    const out = matchLines(
      [line("abc", "grizzlecat", a.message_text), line(null, "grizzlecat", a.message_text)],
      [a],
      NOW,
    );
    expect([...out.keys()]).toEqual([0]);
  });

  it("doesn't fall back to old actions", () => {
    const a = action({ created_at: NOW - 3600 });
    expect(matchLines([line(null, "grizzlecat", a.message_text)], [a], NOW).size).toBe(0);
  });

  it("uses the latest action when one message has several", () => {
    const first = action({ id: 1 });
    const second = action({ id: 2 });
    expect(matchLines([line("m1", "x", "y")], [second, first], NOW).get(0)).toBe(second);
  });

  it("normalizes", () => {
    expect(normalize(" A\n b  ")).toBe("a b");
  });
});

describe("barView never overstates", () => {
  it("drops every running state when the core is gone", () => {
    const v = barView("offline", status());
    expect(v.label).toBe("Motwi isn't running");
    expect(v.canSwitch).toBe(false);
  });

  it("says when it was disconnected, or is still connecting", () => {
    expect(barView("revoked", status()).label).toBe("Disconnected");
    expect(barView("connecting", null).label).toBe("Connecting");
    expect(barView("open", null).label).toBe("Connecting");
  });

  it("shows watching, suggesting and paused with the active model", () => {
    expect(barView("open", status())).toMatchObject({
      label: "Watching",
      sub: "Gemini 2.5 Flash Lite",
      canSwitch: true,
      suggesting: false,
    });
    expect(barView("open", status({ engine: "suggesting" }))).toMatchObject({
      label: "Suggesting",
      suggesting: true,
    });
    expect(barView("open", status({ engine: "paused", paused: true }))).toMatchObject({
      label: "Paused",
      canSwitch: true,
    });
  });

  it("says AI down, and names the backup when it took over", () => {
    const base = status();
    expect(barView("open", status({ ai: { ...base.ai, state: "down" } })).label).toBe("AI down");
    expect(barView("open", status({ ai: { ...base.ai, state: "degraded" } })).sub).toBe(
      "Gemini 2.0 Flash",
    );
  });

  it("can't be switched while Twitch is down", () => {
    const v = barView(
      "open",
      status({ engine: "reconnect", twitch: { state: "reconnect", detail: "Sign in again" } }),
    );
    expect(v).toMatchObject({ label: "Reconnect Twitch", sub: "Sign in again", canSwitch: false });
    expect(barView("open", status({ engine: "stopped" })).canSwitch).toBe(false);
  });
});

describe("labels", () => {
  it("counts only what happened", () => {
    expect(countParts({ delete: 2, warn: 0, timeout: 1, ban: 0, suggested: 0 }).map((p) => p.text)).toEqual([
      "2 del",
      "1 to",
    ]);
    expect(countParts(null)).toEqual([]);
  });

  it("never says an undone delete was restored (spec 5.6)", () => {
    const undone = action({ kind: "delete", duration_s: null, status: "undone" });
    expect(tagLabel(undone)).toBe("Marked as mistake");
    expect(statusNote(undone)).toBe("Twitch can't restore it");
    expect(tagLabel(action({ status: "undone" }))).toBe("Timeout removed");
    expect(tagLabel(action({ kind: "ban_request", status: "undone" }))).toBe("Unbanned");
  });

  it("names each state of a ban request", () => {
    const req = { kind: "ban_request", duration_s: null } as const;
    expect(tagLabel(action({ ...req, status: "pending" }))).toBe("Ban requested");
    expect(tagLabel(action({ ...req, status: "expired" }))).toBe("Ban request open");
    expect(tagLabel(action({ ...req, status: "done" }))).toBe("Banned");
    expect(tagLabel(action({ ...req, status: "denied" }))).toBe("Ban denied");
    expect(tagLabel(action({ ...req, status: "failed" }))).toBe("Ban failed");
    expect(tagLabel(action({ status: "suggested" }))).toBe("Suggest timeout 1m");
    expect(tagLabel(action())).toBe("Timed out 1m");
  });

  it("strikes a line only when Twitch really removed it", () => {
    expect(isGone(action())).toBe(true);
    expect(isGone(action({ status: "failed" }))).toBe(false);
    expect(isGone(action({ status: "suggested" }))).toBe(false);
    expect(isGone(action({ status: "undone" }))).toBe(false);
    expect(isGone(action({ kind: "warn", delete: false }))).toBe(false);
    expect(isGone(action({ kind: "ban_request", status: "denied" }))).toBe(true);
  });

  it("offers the right buttons", () => {
    const verbs = (a: Action) => buttonsFor(a).map((b) => b.label);
    expect(verbs(action({ status: "suggested" }))).toEqual(["Timeout", "Warn instead", "Ignore"]);
    expect(verbs(action({ status: "suggested", kind: "ban_request" }))).toEqual([
      "Ban",
      "Warn instead",
      "Ignore",
    ]);
    expect(verbs(action({ status: "suggested", kind: "delete" }))).toEqual(["Delete", "Ignore"]);
    expect(verbs(action())).toEqual(["Undo"]);
    expect(verbs(action({ status: "failed" }))).toEqual(["Retry"]);
    expect(verbs(action({ kind: "ban_request", status: "pending" }))).toEqual([]);
    expect(verbs(action({ status: "undone" }))).toEqual([]);
    expect(buttonsFor(action({ kind: "delete" }))[0].title).toContain("can't take this back");
  });

  it("counts offenses, skipping undone ones", () => {
    const first = action({ id: 1 });
    const undone = action({ id: 2, status: "undone" });
    const second = action({ id: 3 });
    const other = action({ id: 4, user_id: "u2" });
    const all = [first, undone, second, other];
    expect(offenseNumber(first, all)).toBe(1);
    expect(offenseNumber(second, all)).toBe(2);
    expect(offenseNumber(undone, all)).toBe(0);
    expect(offenseNumber(other, all)).toBe(1);
    const nextStream = action({ id: 5, stream_id: "s2" });
    expect(offenseNumber(nextStream, [...all, nextStream])).toBe(1);
  });

  it("writes ordinals", () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22].map(ordinal)).toEqual([
      "1st",
      "2nd",
      "3rd",
      "4th",
      "11th",
      "12th",
      "13th",
      "21st",
      "22nd",
    ]);
  });

  it("writes receipts and the hold note", () => {
    const req = { kind: "ban_request", actor: "brakkenhoff" } as const;
    expect(receiptText(action({ ...req, status: "done" }))).toBe("grizzlecat banned by brakkenhoff");
    expect(receiptText(action({ ...req, status: "denied" }))).toBe(
      "grizzlecat's ban denied by brakkenhoff",
    );
    expect(requestFoot(action({ status: "pending", hold_applied: true }))).toContain("Held");
    expect(requestFoot(action({ status: "pending", hold_applied: false }))).toContain("Not held");
    expect(requestFoot(action({ status: "expired", hold_applied: true }))).toContain("Hold ended");
  });
});
