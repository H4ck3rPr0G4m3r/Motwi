// Runs in the page's own world, because that's the only place React's internals are visible.
// It copies each chat line's message ID to an attribute and does nothing else: no extension
// APIs, no token, no messages. The isolated content script reads the attribute.

const LINE = ".chat-line__message";
const ATTR = "data-motwi-id";
const MAX_DEPTH = 12;

interface Fiber {
  memoizedProps?: { message?: { id?: unknown } } | null;
  return?: Fiber | null;
}

function messageId(el: Element): string | null {
  const key = Object.keys(el).find((k) => k.startsWith("__reactFiber$"));
  if (!key) return null;
  let fiber = (el as unknown as Record<string, Fiber | undefined>)[key];
  for (let depth = 0; fiber && depth < MAX_DEPTH; depth++) {
    const id = fiber.memoizedProps?.message?.id;
    if (typeof id === "string") return id;
    fiber = fiber.return ?? undefined;
  }
  return null;
}

function tag(el: Element): void {
  if (el.hasAttribute(ATTR)) return;
  const id = messageId(el);
  if (id) el.setAttribute(ATTR, id);
}

new MutationObserver((records) => {
  for (const record of records) {
    for (const node of record.addedNodes) {
      if (node.nodeType !== Node.ELEMENT_NODE) continue;
      const el = node as Element;
      if (el.matches(LINE)) tag(el);
      else if (el.firstElementChild) el.querySelectorAll(LINE).forEach(tag);
    }
  }
}).observe(document.documentElement, { childList: true, subtree: true });

document.querySelectorAll(LINE).forEach(tag);
