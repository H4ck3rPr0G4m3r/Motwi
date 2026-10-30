<script lang="ts">
  import { offenseNumber, ordinal, statusNote } from "../shared/labels";
  import type { Action } from "../shared/protocol";
  import Buttons from "./Buttons.svelte";
  import { store } from "./store.svelte";
  import Tag from "./Tag.svelte";

  let { action, showText }: { action: Action; showText: boolean } = $props();
  const nth = $derived(offenseNumber(action, Object.values(store.actions)));
  const note = $derived(statusNote(action));
</script>

<div class="mark">
  {#if showText}<div class="gone">{action.message_text}</div>{/if}
  <div class="note">
    <Tag {action} />
    {#if note}<span class="why" class:bad={action.status === "failed"}>{note}</span>{/if}
    {#if action.reason}<span class="why">{action.reason}</span>{/if}
    {#if nth >= 2}<span class="why">{ordinal(nth)} offense this stream</span>{/if}
    {#if action.status !== "suggested"}<Buttons {action} />{/if}
  </div>
  {#if action.status === "suggested"}<div class="sug"><Buttons {action} /></div>{/if}
</div>
