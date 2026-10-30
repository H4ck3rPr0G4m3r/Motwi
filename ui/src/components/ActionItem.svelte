<script lang="ts">
  import { message, post } from "../lib/api";
  import { score, statusLabel, when } from "../lib/format";
  import type { Action, Rule } from "../lib/types";
  import ActionTag from "./ActionTag.svelte";
  import Icon from "./Icon.svelte";

  let {
    action,
    rules = {},
    onchange,
    onuser,
  }: {
    action: Action;
    rules?: Record<string, Rule>;
    onchange?: (a: Action) => void;
    onuser?: (login: string) => void;
  } = $props();

  let busy = $state<string | null>(null);
  let error = $state<string | null>(null);

  async function act(verb: "approve" | "deny" | "undo" | "retry") {
    busy = verb;
    error = null;
    try {
      onchange?.(await post<Action>(`/actions/${action.id}/${verb}`));
    } catch (e) {
      error = message(e);
    } finally {
      busy = null;
    }
  }

  const open = $derived(action.status === "pending" || action.status === "expired");
  const gone = $derived(
    action.delete && ["done", "pending", "expired", "undone"].includes(action.status),
  );
  const rule = $derived(action.rule_id ? rules[action.rule_id] : undefined);
  const undoLabel = $derived(
    action.kind === "delete" || action.kind === "warn" ? "Mark as mistake" : "Undo",
  );
  const undoHelp = $derived(
    action.kind === "delete"
      ? "Twitch can't bring a deleted message back. This records it as Motwi's mistake."
      : action.kind === "warn"
        ? "Twitch warnings can't be taken back. This records it as Motwi's mistake."
        : "Lifts the timeout or ban on Twitch.",
  );
</script>

<div class="item" class:quiet={["undone", "denied", "ignored"].includes(action.status)}>
  <div class="tag"><ActionTag kind={action.kind} duration={action.duration_s} /></div>
  <div class="body">
    <p class="msg">
      {#if onuser}
        <button class="who" onclick={() => onuser(action.user_login)}>{action.user_login}</button>
      {:else}
        <b class="who">{action.user_login}</b>
      {/if}
      <span class:gone>{action.message_text}</span>
    </p>
    <p class="why">
      {#if action.score !== null}<span class="mono score">{score(action.score)}</span>{/if}
      <span>{action.reason}</span>
      {#if rule}<span class="rule">{rule.text}</span>{/if}
    </p>
    <p class="meta">
      <span class:bad={action.status === "failed"} class:open>{statusLabel(action)}</span>
      {#if action.actor}<span>by {action.actor}</span>{/if}
      <span>{when(action.created_at)}</span>
    </p>
    {#if action.error && action.status === "failed"}
      <p class="err">{action.error}</p>
    {/if}
    {#if error}<p class="err" role="alert">{error}</p>{/if}
  </div>
  <div class="acts">
    {#if open || (action.status === "suggested" && action.kind === "ban_request")}
      <button class="btn sm danger" disabled={!!busy} onclick={() => act("approve")}>
        {#if busy === "approve"}<Icon name="circle-notch" size={13} spin />{/if}Ban
      </button>
      <button class="btn sm" disabled={!!busy} onclick={() => act("deny")}>Deny</button>
    {:else if action.status === "suggested"}
      <button class="btn sm pri" disabled={!!busy} onclick={() => act("approve")}>
        {#if busy === "approve"}<Icon name="circle-notch" size={13} spin />{/if}Do it
      </button>
      <button class="btn sm" disabled={!!busy} onclick={() => act("deny")}>Dismiss</button>
    {:else if action.status === "done"}
      <button class="btn sm ghost" title={undoHelp} disabled={!!busy} onclick={() => act("undo")}>
        <Icon name="arrow-counter-clockwise" size={13} />{undoLabel}
      </button>
    {:else if action.status === "failed"}
      <button class="btn sm" disabled={!!busy} onclick={() => act("retry")}>
        <Icon name="arrow-clockwise" size={13} spin={busy === "retry"} />Retry
      </button>
    {/if}
  </div>
</div>

<style>
  .item {
    display: grid;
    grid-template-columns: 104px minmax(0, 1fr) auto;
    gap: 12px;
    padding: 11px 12px;
    align-items: start;
  }
  .quiet .msg,
  .quiet .why {
    opacity: 0.6;
  }
  .tag {
    padding-top: 1px;
  }
  .msg {
    font-size: 13.5px;
    overflow-wrap: anywhere;
  }
  .who {
    font-weight: 600;
    margin-right: 6px;
    color: var(--text);
    background: none;
    border: 0;
    padding: 0;
    cursor: pointer;
  }
  button.who:hover {
    color: var(--motwi);
  }
  .gone {
    text-decoration: line-through;
    text-decoration-color: #6a6a74;
    color: var(--muted);
  }
  .why {
    font-size: 12.5px;
    color: var(--muted);
    margin-top: 3px;
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }
  .rule {
    color: var(--dim);
  }
  .rule::before {
    content: "Rule: ";
  }
  .score {
    color: var(--text);
    font-weight: 600;
  }
  .meta {
    font-size: 11.5px;
    color: var(--dim);
    margin-top: 4px;
    display: flex;
    gap: 10px;
  }
  .bad {
    color: var(--error);
    font-weight: 600;
  }
  .open {
    color: var(--caution);
    font-weight: 600;
  }
  .acts {
    display: flex;
    gap: 6px;
  }
</style>
