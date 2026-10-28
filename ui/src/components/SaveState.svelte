<script lang="ts">
  import type { FieldState } from "../lib/save.svelte";
  import Icon from "./Icon.svelte";

  let { state }: { state: FieldState | undefined } = $props();
</script>

<span class="save" aria-live="polite">
  {#if state?.state === "saving"}
    <span class="dim">Saving</span>
  {:else if state?.state === "saved"}
    <span class="ok"><Icon name="check" size={13} />Saved</span>
  {/if}
</span>
{#if state?.state === "error"}
  <p class="err" role="alert">{state.message}</p>
{/if}

<style>
  .save {
    font-size: 12px;
    min-height: 16px;
    display: inline-flex;
    align-items: center;
  }
  .ok {
    color: var(--motwi);
    display: inline-flex;
    gap: 4px;
    align-items: center;
  }
</style>
