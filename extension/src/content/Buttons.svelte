<script lang="ts">
  import { busyLabel, buttonsFor } from "../shared/labels";
  import type { Action } from "../shared/protocol";
  import { store } from "./store.svelte";

  let { action }: { action: Action } = $props();
  const buttons = $derived(buttonsFor(action));
  const busy = $derived(store.busy[action.id]);
  const error = $derived(store.errors[action.id]);
</script>

{#if buttons.length}
  <span class="acts">
    {#each buttons as b (b.label)}
      <button
        class="act"
        class:go={b.primary}
        disabled={!store.online || !!busy}
        title={b.title}
        onclick={() => store.act(action.id, b.verb, b.asWarn)}
      >
        {busy === b.verb ? busyLabel(b.verb, action) : b.label}
      </button>
    {/each}
  </span>
{/if}
{#if error}<span class="err" role="alert">{error}</span>{/if}
