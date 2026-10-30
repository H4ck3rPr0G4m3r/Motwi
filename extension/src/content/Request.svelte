<script lang="ts">
  import { score } from "../../../ui/src/lib/format";
  import { requestFoot } from "../shared/labels";
  import type { Action } from "../shared/protocol";
  import Icon from "./Icon.svelte";
  import { store } from "./store.svelte";

  let { action, more }: { action: Action; more: number } = $props();
  const busy = $derived(store.busy[action.id]);
  const error = $derived(store.errors[action.id]);
  const failed = $derived(action.status === "failed");
</script>

<div class="req" role="alertdialog" aria-label="Ban request for {action.user_login}">
  <div class="req-top">
    <span>{failed ? "Ban failed" : "Ban request"}</span>
    {#if failed}
      <button class="close" aria-label="Close" onclick={() => store.dismissFailed(action.id)}><Icon name="x" /></button>
    {:else if action.score !== null}
      <span class="mono">{score(action.score)}</span>
    {/if}
  </div>
  <div class="req-msg"><b>{action.user_login}</b>: {action.message_text}</div>
  {#if action.reason}<div class="req-why">{action.reason}</div>{/if}
  {#if failed}<div class="req-err" role="alert">{action.error ?? "Twitch refused the ban."}</div>{/if}
  {#if error}<div class="req-err" role="alert">{error}</div>{/if}
  <div class="req-act">
    {#if failed}
      <button class="btn btn-ban" disabled={!store.online || !!busy} onclick={() => store.act(action.id, "retry")}>
        {busy ? "Retrying" : "Retry"}
      </button>
    {:else}
      <button class="btn btn-ban" disabled={!store.online || !!busy} onclick={() => store.act(action.id, "approve")}>
        {busy === "approve" ? "Banning" : "Ban"}
      </button>
      <button class="btn btn-deny" disabled={!store.online || !!busy} onclick={() => store.act(action.id, "deny")}>
        {busy === "deny" ? "Denying" : "Deny"}
      </button>
    {/if}
  </div>
  <div class="req-foot">
    <span>{failed ? "Nothing changed on Twitch" : requestFoot(action)}</span>
    {#if more > 0}<span>{more} more waiting</span>{/if}
  </div>
</div>
