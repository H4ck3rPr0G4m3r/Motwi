<script lang="ts">
  import { onMount } from "svelte";
  import Icon from "./components/Icon.svelte";
  import Rail from "./components/Rail.svelte";
  import { connectCore, core } from "./lib/events.svelte";
  import { nav } from "./lib/router.svelte";
  import History from "./views/History.svelte";
  import Mods from "./views/Mods.svelte";
  import Overview from "./views/Overview.svelte";
  import People from "./views/People.svelte";
  import Rules from "./views/Rules.svelte";
  import Settings from "./views/Settings.svelte";
  import Setup from "./views/Setup.svelte";
  import Stats from "./views/Stats.svelte";

  onMount(connectCore);
</script>

{#if core.link === "unauthorized"}
  <main class="center">
    <Icon name="key" size={28} />
    <h1>This window lost its key to Motwi</h1>
    <p class="muted">Open Motwi again from the tray icon or the Start menu.</p>
  </main>
{:else if !core.status}
  <main class="center" aria-busy="true">
    <span class="logo" aria-hidden="true">M</span>
    <p class="muted">Starting Motwi</p>
  </main>
{:else if !core.status.setup_complete}
  <Setup />
{:else}
  <div class="shell">
    <Rail />
    <main class="main">
      {#if core.link === "lost"}
        <div class="lost" role="status">
          <Icon name="circle-notch" size={14} spin />Lost contact with Motwi's core. Reconnecting.
        </div>
      {/if}
      {#key nav.route}
        {#if nav.route === "overview"}<Overview />
        {:else if nav.route === "rules"}<Rules />
        {:else if nav.route === "history"}<History />
        {:else if nav.route === "people"}<People />
        {:else if nav.route === "stats"}<Stats />
        {:else if nav.route === "mods"}<Mods />
        {:else if nav.route === "settings"}<Settings />
        {/if}
      {/key}
    </main>
  </div>
{/if}

<style>
  .shell {
    display: grid;
    grid-template-columns: 216px minmax(0, 1fr);
    grid-template-rows: minmax(0, 1fr);
    height: 100%;
    overflow: hidden;
  }
  .main {
    min-width: 0;
    min-height: 0;
    overflow: hidden;
    position: relative;
    display: flex;
    flex-direction: column;
  }
  .main > :global(.page) {
    flex: 1;
    min-height: 0;
  }
  .lost {
    display: flex;
    gap: 8px;
    align-items: center;
    justify-content: center;
    padding: 7px;
    font-size: 12.5px;
    background: var(--caution-soft);
    color: var(--caution);
    border-bottom: 1px solid #5a4318;
  }
  .center {
    height: 100%;
    display: grid;
    place-content: center;
    justify-items: center;
    gap: 10px;
    text-align: center;
  }
  .center h1 {
    font-size: 18px;
    font-weight: 600;
  }
  .logo {
    width: 40px;
    height: 40px;
    border-radius: 10px;
    background: var(--motwi);
    color: var(--motwi-ink);
    font-weight: 700;
    font-size: 20px;
    display: grid;
    place-items: center;
  }
</style>
