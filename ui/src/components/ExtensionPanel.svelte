<script lang="ts">
  import { onMount } from "svelte";
  import { del, get, message, post } from "../lib/api";
  import { onCore } from "../lib/events.svelte";
  import { ago } from "../lib/format";
  import type { ExtensionInfo } from "../lib/types";
  import Icon from "./Icon.svelte";
  import LoadError from "./LoadError.svelte";

  let {
    info = $bindable(null),
  }: {
    info?: ExtensionInfo | null;
  } = $props();

  let loadError = $state<string | null>(null);
  let pairing = $state(false);
  let pairError = $state<string | null>(null);
  let link = $state<string | null>(null);
  let copied = $state<"link" | "address" | null>(null);
  let removing = $state<string | null>(null);
  let removeError = $state<string | null>(null);

  const clients = $derived(info?.clients ?? []);

  async function load() {
    loadError = null;
    try {
      info = await get<ExtensionInfo>("/extension");
    } catch (e) {
      loadError = message(e);
    }
  }

  onMount(() => {
    void load();
    return onCore((m) => {
      if (m.type === "extension") {
        link = null;
        void load();
      }
    });
  });

  async function connect() {
    pairing = true;
    pairError = null;
    try {
      link = (await post<{ url: string }>("/extension/pair")).url;
    } catch (e) {
      pairError = message(e);
    } finally {
      pairing = false;
    }
  }

  async function copy(text: string, what: "link" | "address") {
    await navigator.clipboard.writeText(text);
    copied = what;
    setTimeout(() => (copied = null), 1600);
  }

  async function reveal() {
    pairError = null;
    try {
      await post("/extension/reveal");
    } catch (e) {
      pairError = message(e);
    }
  }

  async function remove(id: string) {
    removing = id;
    removeError = null;
    try {
      await del(`/extension/clients/${id}`);
      await load();
    } catch (e) {
      removeError = message(e);
    } finally {
      removing = null;
    }
  }
</script>

{#if loadError}
  <LoadError error={loadError} retry={load} />
{:else if !info}
  <div class="skel" style="height:120px"></div>
{:else}
  <div class="panel-body">
    {#if clients.length}
      <ul class="clients" aria-label="Connected browsers">
        {#each clients as c (c.id)}
          <li>
            <Icon name="browser" size={18} />
            <div class="grow">
              <p class="label">{c.label}</p>
              <p class="help">
                {#if c.connected}In use now{:else if c.last_seen}Last used {ago(c.last_seen)}{:else}Connected, not used yet{/if}
              </p>
            </div>
            <button class="btn sm ghost" disabled={removing === c.id} onclick={() => remove(c.id)}>
              {removing === c.id ? "Removing" : "Remove"}
            </button>
          </li>
        {/each}
      </ul>
      {#if removeError}<p class="err" role="alert">{removeError}</p>{/if}
    {:else}
      <ol class="steps">
        <li>
          Open your browser's extensions page: <span class="mono">chrome://extensions</span>
          <button class="btn sm ghost" onclick={() => copy("chrome://extensions", "address")}>
            {copied === "address" ? "Copied" : "Copy"}
          </button>
          <span class="help">Edge uses <span class="mono">edge://extensions</span>.</span>
        </li>
        <li>Turn on Developer mode.</li>
        <li>
          Choose Load unpacked and pick Motwi's extension folder.
          {#if info.folder}
            <button class="btn sm ghost" onclick={reveal}><Icon name="folder-open" size={14} />Show the folder</button>
          {:else}
            <p class="notice warn">
              <span class="ic"><Icon name="warning" size={16} /></span>The extension isn't built yet. Run
              <span class="mono">npm run build</span> in <span class="mono">extension/</span>.
            </p>
          {/if}
        </li>
        <li>Come back here and connect it.</li>
      </ol>
    {/if}

    <div class="connect">
      <button class="btn {clients.length ? '' : 'pri'}" onclick={connect} disabled={pairing}>
        {#if pairing}<Icon name="circle-notch" size={14} spin />{/if}
        {clients.length ? "Connect another browser" : "Connect this browser"}
      </button>
      {#if link}
        <p class="help" role="status">
          A page opened in your default browser and will connect it. If you watch Twitch in a different browser,
          open this link there instead:
          <button class="linkish" onclick={() => copy(link!, "link")}>
            {copied === "link" ? "Copied" : "Copy the link"}
          </button>
        </p>
      {/if}
      {#if pairError}<p class="err" role="alert">{pairError}</p>{/if}
    </div>
  </div>
{/if}

<style>
  .panel-body {
    display: grid;
    gap: 14px;
  }
  .clients {
    list-style: none;
    padding: 0;
    display: grid;
    gap: 8px;
  }
  .clients li {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .clients :global(.ic) {
    color: var(--motwi);
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  .steps {
    padding-left: 20px;
    display: grid;
    gap: 10px;
    color: var(--muted);
  }
  .steps li > :global(.btn) {
    margin-left: 6px;
  }
  .steps .help {
    display: block;
  }
  .steps .notice {
    margin-top: 8px;
  }
  .connect {
    display: grid;
    gap: 8px;
    justify-items: start;
  }
  .linkish {
    background: none;
    border: 0;
    padding: 0;
    font: inherit;
    font-weight: 600;
    color: var(--motwi);
    cursor: pointer;
  }
  .linkish:hover {
    text-decoration: underline;
  }
</style>
