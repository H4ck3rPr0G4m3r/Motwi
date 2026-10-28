<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import ActionItem from "../components/ActionItem.svelte";
  import Empty from "../components/Empty.svelte";
  import Icon from "../components/Icon.svelte";
  import LoadError from "../components/LoadError.svelte";
  import SkeletonRows from "../components/SkeletonRows.svelte";
  import { get, message } from "../lib/api";
  import { core, onCore } from "../lib/events.svelte";
  import { when } from "../lib/format";
  import { nav } from "../lib/router.svelte";
  import type { Action, Rule, StreamInfo } from "../lib/types";

  const PAGE = 50;

  let items = $state<Action[] | null>(null);
  let rules = $state<Rule[]>([]);
  let streams = $state<StreamInfo[]>([]);
  let error = $state<string | null>(null);
  let more = $state(false);
  let loadingMore = $state(false);

  let q = $state("");
  let kind = $state("");
  let rule = $state("");
  let actor = $state("");
  let status = $state("");
  let stream = $state("");
  let user = $state(nav.query.get("user") ?? "");

  const ruleMap = $derived(Object.fromEntries(rules.map((r) => [r.id, r])));
  const streamer = $derived(core.status?.accounts.streamer?.login ?? "");
  const filtered = $derived(!!(q || kind || rule || actor || status || stream || user));

  function params(before?: number) {
    const p = new URLSearchParams({ limit: String(PAGE) });
    if (q.trim()) p.set("q", q.trim());
    if (kind) p.set("kind", kind);
    if (rule.startsWith("source:")) p.set("source", rule.slice(7));
    else if (rule) p.set("rule", rule);
    if (actor) p.set("actor", actor);
    if (status) p.set("status", status);
    if (stream) p.set("stream", stream);
    if (user) p.set("user", user);
    if (before) p.set("before", String(before));
    return p;
  }

  async function load() {
    error = null;
    try {
      const page = await get<Action[]>(`/actions?${params()}`);
      items = page;
      more = page.length === PAGE;
    } catch (e) {
      error = message(e);
    }
  }

  async function loadMore() {
    if (!items?.length) return;
    loadingMore = true;
    try {
      const page = await get<Action[]>(`/actions?${params(items[items.length - 1].id)}`);
      items = [...items, ...page];
      more = page.length === PAGE;
    } catch (e) {
      error = message(e);
    } finally {
      loadingMore = false;
    }
  }

  let debounce: ReturnType<typeof setTimeout> | undefined;
  $effect(() => {
    // re-run whenever a filter changes
    void [q, kind, rule, actor, status, stream, user];
    clearTimeout(debounce);
    debounce = setTimeout(load, 220);
  });

  onMount(async () => {
    try {
      [rules, streams] = await Promise.all([get<Rule[]>("/rules"), get<StreamInfo[]>("/streams")]);
    } catch {
      /* filters still work without names */
    }
  });

  const stop = onCore((m) => {
    if (m.type !== "action" || !items) return;
    const i = items.findIndex((a) => a.id === m.action.id);
    if (i >= 0) items[i] = m.action;
    else if (!filtered) items = [m.action, ...items];
  });
  onDestroy(() => {
    stop();
    clearTimeout(debounce);
  });

  function changed(a: Action) {
    if (items) items = items.map((x) => (x.id === a.id ? a : x));
  }

  function clear() {
    q = kind = rule = actor = status = stream = user = "";
  }
</script>

<div class="page">
  <div class="pagehead">
    <div>
      <h1>History</h1>
      <p>Everything Motwi did or suggested. Undo from here, or click a chatter to see their history.</p>
    </div>
  </div>

  <div class="filters" role="search">
    <div class="search">
      <Icon name="magnifying-glass" size={15} />
      <label class="sr-only" for="hq">Search messages, chatters and reasons</label>
      <input id="hq" class="input" type="search" placeholder="Search messages, chatters, reasons" bind:value={q} />
    </div>
    <label class="sr-only" for="hk">Action</label>
    <select id="hk" class="input" bind:value={kind}>
      <option value="">Any action</option>
      <option value="delete">Deleted</option>
      <option value="warn">Warned</option>
      <option value="timeout">Timeouts</option>
      <option value="ban_request">Bans</option>
    </select>
    <label class="sr-only" for="hr">Rule</label>
    <select id="hr" class="input" bind:value={rule}>
      <option value="">Any reason</option>
      <option value="source:spam">Spam filter</option>
      <option value="source:blocked_word">Blocked words</option>
      {#each rules as r (r.id)}<option value={r.id}>{r.text}</option>{/each}
    </select>
    <label class="sr-only" for="ha">Who acted</label>
    <select id="ha" class="input" bind:value={actor}>
      <option value="">Anyone</option>
      <option value="motwi">Motwi on its own</option>
      {#if streamer}<option value={streamer}>You ({streamer})</option>{/if}
    </select>
    <label class="sr-only" for="hs">Status</label>
    <select id="hs" class="input" bind:value={status}>
      <option value="">Any status</option>
      <option value="pending,expired">Open requests</option>
      <option value="suggested">Suggestions</option>
      <option value="done">Done</option>
      <option value="undone">Undone</option>
      <option value="denied,ignored">Denied or dismissed</option>
      <option value="failed">Failed</option>
    </select>
    <label class="sr-only" for="hst">Stream</label>
    <select id="hst" class="input" bind:value={stream}>
      <option value="">Any stream</option>
      {#each streams as st (st.id)}<option value={st.id}>{when(st.started_at)}{st.game ? `, ${st.game}` : ""}</option>{/each}
    </select>
  </div>

  {#if user}
    <div class="chatter">
      <Icon name="user-check" size={16} />
      <span>Showing <b>{user}</b>{items ? `: ${items.length}${more ? "+" : ""} actions` : ""}</span>
      <button class="btn sm ghost" onclick={() => (user = "")}><Icon name="x" size={13} />All chatters</button>
    </div>
  {/if}

  {#if error}
    <LoadError {error} retry={load} />
  {:else if !items}
    <SkeletonRows rows={6} height={32} />
  {:else if !items.length}
    <div class="panel">
      {#if filtered}
        <Empty icon="funnel" title="Nothing matches these filters">
          <button class="btn sm" onclick={clear}>Clear filters</button>
        </Empty>
      {:else}
        <Empty icon="clock-counter-clockwise" title="No actions yet">
          Everything Motwi deletes, warns, times out or suggests lands here.
        </Empty>
      {/if}
    </div>
  {:else}
    <div class="panel rows">
      {#each items as a (a.id)}
        <ActionItem action={a} rules={ruleMap} onchange={changed} onuser={(login) => (user = login)} />
      {/each}
    </div>
    {#if more}
      <button class="btn" style="align-self:center" onclick={loadMore} disabled={loadingMore}>
        {#if loadingMore}<Icon name="circle-notch" size={14} spin />{/if}Load more
      </button>
    {/if}
  {/if}
</div>

<style>
  .filters {
    display: grid;
    grid-template-columns: minmax(200px, 1.6fr) repeat(5, minmax(0, 1fr));
    gap: 8px;
  }
  .search {
    position: relative;
    display: flex;
    align-items: center;
  }
  .search :global(.ic) {
    position: absolute;
    left: 10px;
    color: var(--dim);
    pointer-events: none;
  }
  .search .input {
    padding-left: 32px;
  }
  .chatter {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 13px;
    color: var(--muted);
  }
  .chatter b {
    color: var(--text);
  }
  @media (max-width: 1150px) {
    .filters {
      grid-template-columns: repeat(3, 1fr);
    }
    .search {
      grid-column: 1 / -1;
    }
  }
</style>
