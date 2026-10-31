// Runs on every Twitch page, and stays dormant unless this is the paired channel's chat.

import { mount, unmount } from "svelte";
import { channelFromUrl } from "../shared/channel";
import { PAIRED, type Request, type WhoAmI } from "../shared/protocol";
import Overlay from "./Overlay.svelte";
import { scheduleReconcile, stopLines, watchLines } from "./lines.svelte";
import { attachSheet } from "./sheet";
import { store } from "./store.svelte";

const ROOM = ".chat-room__content";
const TICK_MS = 1_000;
const RECONNECT_MS = 500;

let lastChannel: string | null | undefined;
let paired: string[] = [];
let active = false;
let activeChannel: string | null = null;
let barHost: HTMLElement | null = null;
let barComponent: object | null = null;
let timer: ReturnType<typeof setInterval>;

function alive(): boolean {
  return !!chrome.runtime?.id; // false once the extension was updated or removed
}

async function whoami(): Promise<void> {
  const reply = (await chrome.runtime.sendMessage({ type: "whoami" } satisfies Request)) as WhoAmI;
  paired = reply?.channels ?? [];
}

function mountBar(room: Element): void {
  if (!barHost) {
    barHost = document.createElement("motwi-bar");
    for (const type of ["click", "mousedown", "mouseup", "keydown", "keyup", "keypress"]) {
      barHost.addEventListener(type, (e) => e.stopPropagation());
    }
    const root = barHost.attachShadow({ mode: "open" });
    attachSheet(root);
    barComponent = mount(Overlay, { target: root });
  }
  if (barHost.parentElement !== room) room.prepend(barHost);
}

function activate(channel: string): void {
  if (active) return;
  active = true;
  activeChannel = channel;
  store.onChange = scheduleReconcile;
  store.connect(channel, () => {
    // the worker restarted, or the extension went away
    if (active && alive()) setTimeout(reconnect, RECONNECT_MS);
    else if (!alive()) shutdown();
  });
}

function reconnect(): void {
  if (!active || !alive() || !activeChannel) return;
  active = false;
  activate(activeChannel);
}

function deactivate(): void {
  if (!active) return;
  active = false;
  activeChannel = null;
  store.disconnect();
  stopLines();
  if (barComponent) void unmount(barComponent);
  barComponent = null;
  barHost?.remove();
  barHost = null;
}

function shutdown(): void {
  clearInterval(timer);
  deactivate();
}

async function tick(): Promise<void> {
  if (!alive()) {
    shutdown();
    return;
  }
  const channel = channelFromUrl(location.href);
  if (channel !== lastChannel) {
    if (!channel) lastChannel = channel;
    else {
      try {
        await whoami();
        lastChannel = channel;
      } catch {
        return; // the worker couldn't answer; the next tick asks again
      }
    }
  }
  // a connected tab follows the core, in case the streamer signed in as another channel
  const wanted = active
    ? channel === activeChannel && (store.channel === null || store.channel === channel)
    : !!channel && paired.includes(channel);
  if (!channel || !wanted) {
    deactivate();
    return;
  }
  const room = document.querySelector(ROOM);
  if (!room) return; // chat is collapsed, or hasn't rendered yet
  activate(channel);
  mountBar(room);
  watchLines();
}

chrome.runtime.onMessage.addListener((m) => {
  if (m === PAIRED) {
    lastChannel = undefined;
    void tick();
  }
});

timer = setInterval(() => void tick(), TICK_MS);
void tick();
