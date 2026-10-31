<script lang="ts">
  import { barView, countParts, receiptText, statusNote } from "../shared/labels";
  import Buttons from "./Buttons.svelte";
  import Icon from "./Icon.svelte";
  import Request from "./Request.svelte";
  import { store } from "./store.svelte";
  import Tag from "./Tag.svelte";

  const LOG_ROWS = 25;

  let logOpen = $state(false);
  let logEl = $state<HTMLElement>();
  let countsEl = $state<HTMLElement>();

  const view = $derived(barView(store.link, store.status, store.role));
  const parts = $derived(countParts(store.chat?.counts ?? null));
  const paused = $derived(store.status?.paused ?? false);
  const warnings = $derived(store.online ? (store.chat?.warnings ?? []) : []);
  const requests = $derived(store.requests);
  const log = $derived(store.log.slice(0, LOG_ROWS));

  function onKey(e: KeyboardEvent) {
    if (e.key === "Escape" && logOpen) {
      logOpen = false;
      countsEl?.focus();
    }
  }

  function onPointer(e: MouseEvent) {
    const path = e.composedPath();
    if (logOpen && logEl && countsEl && !path.includes(logEl) && !path.includes(countsEl)) {
      logOpen = false;
    }
  }
</script>

<svelte:document onmousedown={onPointer} onkeydown={onKey} />

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="overlay" onkeydown={onKey}>
  <div class="bar" data-tone={view.tone}>
    <span class="logo" aria-hidden="true">M</span>
    <div class="st" role="status" aria-live="polite">
      <b>{view.label}</b><small title={view.sub}>{view.sub}</small>
    </div>
    {#if view.suggesting}<span class="mode">Suggest only</span>{/if}
    <button
      bind:this={countsEl}
      class="counts"
      aria-expanded={logOpen}
      aria-label="Recent actions: {parts.map((p) => p.text).join(', ') || 'none yet'}"
      onclick={() => (logOpen = !logOpen)}
    >
      {#each parts as p (p.key)}<span data-count={p.kind}>{p.text}</span>{:else}<span>0</span>{/each}
      <Icon name="caret-down" size={10} />
    </button>
    {#if store.role === "own"}
      <button
        class="sw"
        role="switch"
        aria-checked={!paused && view.canSwitch}
        aria-label="Motwi moderation"
        aria-busy={store.switching}
        title={view.canSwitch ? (paused ? "Resume moderation" : "Pause moderation") : view.label}
        disabled={!view.canSwitch || store.switching}
        onclick={() => store.setPaused(!paused)}
      ></button>
    {/if}
  </div>

  {#if store.switchError}
    <div class="band bad" role="alert"><Icon name="warning" />{store.switchError}</div>
  {/if}
  {#each warnings as w (w.kind)}
    <div class="band" class:bad={w.level === "error"} role="status"><Icon name="warning" />{w.text}</div>
  {/each}

  <div class="floats">
    {#if logOpen}
      <div class="log" bind:this={logEl} role="region" aria-label="Recent actions">
        {#each log as a (a.id)}
          {@const note = statusNote(a)}
          <div class="row" data-status={a.status}>
            <div class="row-top"><Tag action={a} /><b>{a.user_login}</b></div>
            <div class="row-text">{a.message_text}</div>
            {#if a.reason || note}
              <div class="why">{[note, a.reason].filter(Boolean).join(". ")}</div>
            {/if}
            <Buttons action={a} />
          </div>
        {:else}
          <p class="empty">Nothing yet. Motwi's actions will list here.</p>
        {/each}
      </div>
    {/if}

    {#if requests.length}
      <div aria-live="assertive">
        <Request action={requests[0]} more={requests.length - 1} />
      </div>
    {/if}

    {#each store.receipts as r (r.id)}
      <div class="resolved" role="status">
        <span>{receiptText(store.actions[r.id] ?? r.action)}</span>
        {#if (store.actions[r.id] ?? r.action).status === "done"}
          <Buttons action={store.actions[r.id] ?? r.action} />
        {/if}
      </div>
    {/each}
  </div>
</div>
