<script lang="ts">
  import { KIND_COLOR, kindLabel } from "../lib/format";
  import type { Kind } from "../lib/types";

  let { kind, duration = null }: { kind: Kind; duration?: number | null } = $props();
</script>

<span class="chip"><span class="sw" style:background={KIND_COLOR[kind]}></span>{kindLabel(kind, duration)}</span>
