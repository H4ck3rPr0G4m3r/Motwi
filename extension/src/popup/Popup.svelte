<script lang="ts">
  import { onMount } from "svelte";
  import { barView } from "../shared/labels";
  import { PORT_NAME, type PortEvent, type Request, type Snapshot } from "../shared/protocol";

  let list = $state<Snapshot[] | null>(null);
  let forgetting = $state<string | null | undefined>(undefined);

  onMount(() => {
    const port = chrome.runtime.connect({ name: PORT_NAME });
    port.onMessage.addListener((m: PortEvent) => {
      if (m.type === "list") list = m.items;
    });
    return () => port.disconnect();
  });

  async function forget(channel: string | null) {
    forgetting = channel;
    await chrome.runtime.sendMessage({ type: "forget", channel } satisfies Request);
    forgetting = undefined;
  }
</script>

<header><span class="logo" aria-hidden="true">M</span><b>Motwi</b></header>

{#if !list}
  <p class="muted">Checking this browser's connection.</p>
{:else if list.length === 0}
  <p>This browser isn't connected to Motwi yet.</p>
  <p class="muted">In the Motwi app, go to Settings, Browser extension, and press Connect this browser.</p>
  <p class="muted">Helping moderate someone's channel? Open the invite link they sent you.</p>
{:else}
  {#each list as snap (snap.channel)}
    {@const view = barView(snap.link, snap.chat?.status ?? null, snap.role)}
    <section>
      <dl>
        <div>
          <dt>Channel</dt>
          <dd>{snap.channel ?? "Unknown"}{#if snap.role === "mod"}<span class="muted"> (you mod here)</span>{/if}</dd>
        </div>
        <div><dt>State</dt><dd class:bad={view.tone === "bad"} role="status">{view.label}</dd></div>
        <div><dt>Detail</dt><dd class="muted">{view.sub}</dd></div>
      </dl>
      {#if snap.channel}
        <a class="link" href="https://www.twitch.tv/popout/{snap.channel}/chat" target="_blank" rel="noreferrer">
          Open {snap.role === "mod" ? "the" : "your"} chat
        </a>
      {/if}
      <button onclick={() => forget(snap.channel)} disabled={forgetting === snap.channel}>
        {#if forgetting === snap.channel}Disconnecting
        {:else if snap.role === "mod"}Stop moderating here
        {:else}Disconnect this browser{/if}
      </button>
    </section>
  {/each}
{/if}
