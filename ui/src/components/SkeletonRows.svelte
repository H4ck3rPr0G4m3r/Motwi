<script lang="ts">
  let { rows = 4, height = 18 }: { rows?: number; height?: number } = $props();
</script>

<div class="panel rows" aria-busy="true" aria-label="Loading">
  {#each { length: rows } as _, i (i)}
    <div class="row">
      <div class="skel" style:height="{height}px" style:width="{60 + ((i * 17) % 30)}%"></div>
    </div>
  {/each}
</div>

<style>
  .row {
    padding: 12px;
  }
</style>
