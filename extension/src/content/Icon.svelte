<script lang="ts">
  import caretDown from "@phosphor-icons/core/assets/regular/caret-down.svg?raw";
  import warning from "@phosphor-icons/core/assets/regular/warning.svg?raw";
  import x from "@phosphor-icons/core/assets/regular/x.svg?raw";

  const ICONS = { "caret-down": caretDown, warning, x };

  let { name, size = 14 }: { name: keyof typeof ICONS; size?: number } = $props();
</script>

<span class="ic" style:width="{size}px" style:height="{size}px" aria-hidden="true">{@html ICONS[name]}</span>
