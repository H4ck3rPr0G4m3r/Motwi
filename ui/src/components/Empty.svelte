<script lang="ts">
  import type { Snippet } from "svelte";
  import type { IconName } from "../lib/icons";
  import Icon from "./Icon.svelte";

  let {
    icon,
    title,
    children,
  }: { icon: IconName; title: string; children?: Snippet } = $props();
</script>

<div class="empty">
  <Icon name={icon} size={22} />
  <p class="t">{title}</p>
  {#if children}<div class="b">{@render children()}</div>{/if}
</div>

<style>
  .empty {
    padding: 26px 16px;
    display: grid;
    justify-items: center;
    text-align: center;
    gap: 6px;
    color: var(--dim);
  }
  .t {
    color: var(--muted);
    font-weight: 600;
    font-size: 13px;
  }
  .b {
    font-size: 12.5px;
    max-width: 46ch;
  }
</style>
