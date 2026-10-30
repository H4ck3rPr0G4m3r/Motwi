<script lang="ts">
  import Icon from "./Icon.svelte";

  let { error, retry }: { error: string; retry?: () => void } = $props();
</script>

<div class="notice error" role="alert">
  <span class="ic"><Icon name="warning-circle" size={18} /></span>
  <div style="flex:1">{error}</div>
  {#if retry}<button class="btn sm" onclick={retry}>Try again</button>{/if}
</div>
