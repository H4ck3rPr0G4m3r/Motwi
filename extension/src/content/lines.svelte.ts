// Keeps marks on Twitch's chat lines. Twitch owns those elements and may re-render or drop
// them at any time, so this re-derives everything from the DOM on each pass instead of
// remembering what it did: a line that lost its mark gets it back, and a recycled line
// loses a mark that isn't its own.

import { mount, unmount } from "svelte";
import type { Action } from "../shared/protocol";
import { isGone } from "../shared/labels";
import { matchLines, normalize, type LineInfo } from "./match";
import Mark from "./Mark.svelte";
import { attachSheet } from "./sheet";
import { store } from "./store.svelte";

const LINE = ".chat-line__message";
const BODY = '[data-a-target="chat-line-message-body"]';
const LIST = ".chat-scrollable-area__message-container";
const STATE_ATTR = "data-motwi-state";
const PIN_SLACK_PX = 40;
const THROTTLE_MS = 50;

interface Placed {
  host: HTMLElement;
  component: object;
  props: { action: Action; showText: boolean };
}

const placed = new Map<Element, Placed>();
let list: Element | null = null;
let observer: MutationObserver | null = null;
let scheduled = false;

function lineText(line: Element): string {
  const body = line.querySelector(BODY);
  if (!body) return "";
  let text = "";
  const walk = (node: Node) => {
    for (const child of node.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) text += child.textContent ?? "";
      else if (child instanceof HTMLImageElement) text += child.alt;
      else if (child.nodeType === Node.ELEMENT_NODE) walk(child);
    }
  };
  walk(body);
  return text;
}

function info(line: Element): LineInfo {
  return {
    id: line.getAttribute("data-motwi-id"),
    login: line.getAttribute("data-a-user"),
    text: lineText(line),
  };
}

function scroller(from: Element): Element | null {
  for (let el = from.parentElement; el; el = el.parentElement) {
    if (el.scrollHeight > el.clientHeight + 1) {
      const overflow = getComputedStyle(el).overflowY;
      if (overflow === "auto" || overflow === "scroll") return el;
    }
  }
  return null;
}

function place(line: Element, action: Action, text: string): void {
  const showText = isGone(action) && normalize(text) !== normalize(action.message_text);
  const existing = placed.get(line);
  if (existing) {
    existing.props.action = action;
    existing.props.showText = showText;
    return;
  }
  const host = document.createElement("motwi-mark");
  // keep clicks in the mark from reaching Twitch's handlers on the line
  for (const type of ["click", "mousedown", "mouseup", "keydown"]) {
    host.addEventListener(type, (e) => e.stopPropagation());
  }
  const root = host.attachShadow({ mode: "open" });
  attachSheet(root);
  const props = $state({ action, showText });
  const component = mount(Mark, { target: root, props });
  line.appendChild(host);
  placed.set(line, { host, component, props });
}

function remove(line: Element): void {
  const p = placed.get(line);
  if (p) {
    void unmount(p.component);
    p.host.remove();
    placed.delete(line);
  }
  line.removeAttribute(STATE_ATTR);
}

function reconcile(): void {
  if (!list) return;
  const lines = [...list.querySelectorAll(LINE)];
  const infos = lines.map(info);
  const matches = matchLines(infos, Object.values(store.actions));

  const scrollEl = matches.size > 0 ? scroller(list) : null;
  const pinned =
    !!scrollEl && scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight <= PIN_SLACK_PX;

  let added = false;
  lines.forEach((line, i) => {
    const action = matches.get(i);
    if (!action) {
      if (placed.has(line) || line.hasAttribute(STATE_ATTR)) remove(line);
      return;
    }
    if (!placed.has(line)) added = true;
    place(line, action, infos[i].text);
    const state = action.status === "suggested" ? "suggested" : isGone(action) ? "gone" : "kept";
    if (line.getAttribute(STATE_ATTR) !== state) line.setAttribute(STATE_ATTR, state);
  });

  // lines Twitch dropped: their marks went with them
  for (const line of placed.keys()) {
    if (!line.isConnected) {
      const p = placed.get(line)!;
      void unmount(p.component);
      placed.delete(line);
    }
  }

  // a mark adds height; without this, chat would stop following new messages
  if (added && pinned && scrollEl) scrollEl.scrollTop = scrollEl.scrollHeight;
}

export function scheduleReconcile(): void {
  if (scheduled) return;
  scheduled = true;
  setTimeout(() => {
    scheduled = false;
    reconcile();
  }, THROTTLE_MS);
}

/** Watches the current chat list. Safe to call repeatedly: it reattaches if Twitch replaced it. */
export function watchLines(): void {
  const current = document.querySelector(LIST);
  if (current === list) return;
  observer?.disconnect();
  list = current;
  if (!list) return;
  observer = new MutationObserver(scheduleReconcile);
  observer.observe(list, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["data-motwi-id"],
    characterData: true,
  });
  scheduleReconcile();
}

export function stopLines(): void {
  observer?.disconnect();
  observer = null;
  for (const line of [...placed.keys()]) remove(line);
  list = null;
}
