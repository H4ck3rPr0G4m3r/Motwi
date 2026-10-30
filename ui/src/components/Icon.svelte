<script lang="ts">
  import { ICONS, type IconName } from "../lib/icons";

  let {
    name,
    size = 16,
    label,
    spin = false,
  }: { name: IconName; size?: number; label?: string; spin?: boolean } = $props();
</script>

<span
  class="ic"
  class:spin
  style:width="{size}px"
  style:height="{size}px"
  role={label ? "img" : undefined}
  aria-label={label}
  aria-hidden={label ? undefined : "true"}
>
  {@html ICONS[name]}
</span>

<style>
  .ic {
    display: inline-flex;
    flex: none;
    line-height: 0;
  }
  .ic :global(svg) {
    width: 100%;
    height: 100%;
  }
  .spin {
    animation: spin 0.9s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>
