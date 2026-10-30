<script lang="ts">
  import { onMount } from "svelte";
  import { barView } from "../shared/labels";
  import { PORT_NAME, type PortEvent, type Request, type Snapshot } from "../shared/protocol";

  let snap = $state<Snapshot | null>(null);
  let forgetting = $state(false);

  onMount(() => {
    const port = chrome.runtime.connect({ name: PORT_NAME });
    port.onMessage.addListener((m: PortEvent) => {
      if (m.type === "snapshot") snap = m.snapshot;
    });
    return () => port.disconnect();
  });

  const view = $derived(snap ? barView(snap.link, snap.chat?.status ?? null) : null);
  const paired = $derived(!!snap && snap.link !== "unpaired" && snap.link !== "revoked");

  async function forget() {
    forgetting = true;
    await chrome.runtime.sendMessage({ type: "forget" } satisfies Request);
    forgetting = false;
  }
</script>

<header><span class="logo" aria-hidden="true">M</span><b>Motwi</b></header>

{#if !snap}
  <p class="muted">Checking this browser's connection.</p>
{:else if paired && view}
  <dl>
    <div><dt>Channel</dt><dd>{snap.channel ?? "Unknown"}</dd></div>
    <div><dt>State</dt><dd class:bad={view.tone === "bad"} role="status">{view.label}</dd></div>
    <div><dt>Detail</dt><dd class="muted">{view.sub}</dd></div>
  </dl>
  {#if snap.channel}
    <a class="link" href="https://www.twitch.tv/popout/{snap.channel}/chat" target="_blank" rel="noreferrer">
      Open your chat
    </a>
  {/if}
  <button onclick={forget} disabled={forgetting}>{forgetting ? "Disconnecting" : "Disconnect this browser"}</button>
{:else}
  <p>
    {snap.link === "revoked"
      ? "This browser was disconnected from Motwi."
      : "This browser isn't connected to Motwi yet."}
  </p>
  <p class="muted">In the Motwi app, go to Settings, Browser extension, and press Connect this browser.</p>
{/if}
