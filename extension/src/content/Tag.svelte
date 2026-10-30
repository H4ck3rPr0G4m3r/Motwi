<script lang="ts">
  import { score } from "../../../ui/src/lib/format";
  import { tagLabel, tagTone } from "../shared/labels";
  import type { Action } from "../shared/protocol";

  let { action }: { action: Action } = $props();
  const tone = $derived(tagTone(action));
</script>

<span class="tag {tone}" data-kind={action.kind}>
  {#if tone === "action"}<span class="swatch" aria-hidden="true"></span>{/if}{tagLabel(action)}
  {#if action.score !== null}<span class="s">{score(action.score)}</span>{/if}
</span>
