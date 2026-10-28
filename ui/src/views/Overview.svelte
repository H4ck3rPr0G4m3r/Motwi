<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import ActionItem from "../components/ActionItem.svelte";
  import Empty from "../components/Empty.svelte";
  import Icon from "../components/Icon.svelte";
  import LoadError from "../components/LoadError.svelte";
  import SkeletonRows from "../components/SkeletonRows.svelte";
  import Toggle from "../components/Toggle.svelte";
  import { get, message, patch, post } from "../lib/api";
  import { core, onCore, setStatus } from "../lib/events.svelte";
  import { ago, count, KIND_COLOR, money } from "../lib/format";
  import { go } from "../lib/router.svelte";
  import type { Action, Overview, Rule, Status } from "../lib/types";

  let data = $state<Overview | null>(null);
  let rules = $state<Record<string, Rule>>({});
  let error = $state<string | null>(null);
  let switchError = $state<string | null>(null);
  let reconnecting = $state(false);
  let timer: ReturnType<typeof setTimeout> | undefined;

  const s = $derived(core.status!);

  async function load() {
    try {
      const [o, r] = await Promise.all([get<Overview>("/overview"), get<Rule[]>("/rules")]);
      data = o;
      rules = Object.fromEntries(r.map((x) => [x.id, x]));
      error = null;
    } catch (e) {
      error = message(e);
    }
  }

  function soon() {
    clearTimeout(timer);
    timer = setTimeout(load, 300);
  }

  onMount(load);
  const stop = onCore((m) => {
    if (m.type === "action" || m.type === "reminder" || m.type === "status") soon();
  });
  onDestroy(() => {
    stop();
    clearTimeout(timer);
  });

  async function setPaused(paused: boolean) {
    switchError = null;
    try {
      setStatus(await post<Status>("/engine", { paused }));
    } catch (e) {
      switchError = message(e);
    }
  }

  async function setSuggest(value: boolean) {
    switchError = null;
    try {
      await patch("/settings", { suggest_only: value });
    } catch (e) {
      switchError = message(e);
    }
  }

  async function reconnect() {
    reconnecting = true;
    try {
      setStatus(await post<Status>("/twitch/reconnect"));
    } finally {
      reconnecting = false;
    }
  }

  function changed(a: Action) {
    if (!data) return;
    data.open_requests = data.open_requests.filter((x) => x.id !== a.id || ["pending", "expired", "suggested"].includes(a.status));
    data.recent = data.recent.map((x) => (x.id === a.id ? a : x));
  }

  const headline = $derived.by(() => {
    const login = s.accounts.streamer?.login;
    switch (s.engine) {
      case "watching":
        return { icon: "eye" as const, text: `Watching ${login}`, tone: "on" };
      case "suggesting":
        return { icon: "chat-text" as const, text: `Suggesting in ${login}`, tone: "on" };
      case "paused":
        return { icon: "pause" as const, text: "Paused. Chat isn't being checked.", tone: "warn" };
      case "connecting":
        return { icon: "circle-notch" as const, text: "Connecting to Twitch", tone: "warn" };
      case "reconnect":
        return { icon: "warning-circle" as const, text: "Twitch needs you to sign in again", tone: "bad" };
      default:
        return { icon: "prohibit" as const, text: "Not watching", tone: "off" };
    }
  });

  const KPIS = [
    ["delete", "Deleted"],
    ["warn", "Warned"],
    ["timeout", "Timeouts"],
    ["ban_request", "Bans"],
  ] as const;
</script>

<div class="page">
  <div class="pagehead">
    <div>
      <h1>Overview</h1>
      <p>Today on your channel. Everything live also shows up in Twitch chat once the extension is in.</p>
    </div>
  </div>

  {#if data}
    {#each data.warnings as w (w.kind + w.text)}
      <div class="notice {w.level}" role="alert">
        <span class="ic"><Icon name={w.level === "error" ? "warning-circle" : "warning"} size={18} /></span>
        <div style="flex:1">{w.text}</div>
        {#if w.kind === "twitch" && s.twitch.state === "reconnect"}
          <button class="btn sm" onclick={() => go("settings")}>Reconnect Twitch</button>
        {:else if w.kind === "twitch"}
          <button class="btn sm" onclick={reconnect} disabled={reconnecting}>Try again</button>
        {:else if w.kind === "ai"}
          <button class="btn sm" onclick={() => go("settings")}>AI settings</button>
        {/if}
      </div>
    {/each}
  {/if}

  <section class="state panel" aria-live="polite">
    <div class="who">
      <span class="badge {headline.tone}"><Icon name={headline.icon} size={20} spin={s.engine === "connecting"} /></span>
      <div>
        <p class="big">{headline.text}</p>
        <p class="muted sub">
          {#if s.stream.live}
            <span class="live">Live</span>
            {s.stream.game ?? "No category"}{s.stream.title ? `, "${s.stream.title}"` : ""}
          {:else if s.twitch.state === "connected"}
            Offline. {s.stream.game ? `Last category ${s.stream.game}.` : ""} Motwi still checks chat.
          {:else}
            {s.twitch.detail || "Motwi connects to Twitch when you start."}
          {/if}
        </p>
      </div>
    </div>
    <div class="switches">
      <div class="sw-row">
        <span id="pause-label">Moderating</span>
        <Toggle
          checked={!s.paused}
          label="Moderating"
          disabled={s.engine === "stopped" || s.engine === "reconnect"}
          onchange={(on) => setPaused(!on)}
        />
      </div>
      <div class="sw-row">
        <span>Suggest only</span>
        <Toggle checked={s.suggest_only} label="Suggest only" onchange={setSuggest} />
      </div>
      {#if switchError}<p class="err" role="alert">{switchError}</p>{/if}
    </div>
  </section>

  {#if error}
    <LoadError {error} retry={load} />
  {:else if !data}
    <div class="skel" style="height:86px"></div>
    <SkeletonRows rows={3} />
  {:else}
    <section class="kpis panel" aria-label="Today">
      <div class="kpi">
        <p class="l">Checked today</p>
        <p class="v mono">{count(data.today.checked)}</p>
        <p class="d">{data.today.unchecked ? `${count(data.today.unchecked)} couldn't be checked` : "messages"}</p>
      </div>
      {#each KPIS as [kind, label] (kind)}
        {@const t = data.today.actions[kind]}
        <div class="kpi">
          <p class="l"><span class="sw" style:background={KIND_COLOR[kind]}></span>{label}</p>
          <p class="v mono">{count(t.count)}</p>
          <p class="d">
            {kind === "ban_request"
              ? `${t.denied} denied`
              : t.suggested
                ? `${t.suggested} suggested`
                : `${t.undone} undone`}
          </p>
        </div>
      {/each}
      <div class="kpi">
        <p class="l">AI cost today</p>
        <p class="v mono">{money(data.today.cost)}</p>
        <p class="d">{money(s.month_cost)} this month</p>
      </div>
    </section>

    <section class="sec" aria-live="polite">
      <h2>Ban requests <span>{data.open_requests.length ? "waiting for you" : ""}</span></h2>
      <div class="panel rows">
        {#each data.open_requests as a (a.id)}
          <ActionItem action={a} {rules} onchange={changed} />
        {:else}
          <Empty icon="gavel" title="No open ban requests">
            When a message is serious enough for a ban, Motwi holds the chatter and asks here and in chat.
          </Empty>
        {/each}
      </div>
    </section>

    <div class="cols">
      <section class="sec">
        <h2>Recent actions</h2>
        <div class="panel rows">
          {#each data.recent.filter((a) => !data!.open_requests.some((o) => o.id === a.id)) as a (a.id)}
            <ActionItem action={a} {rules} onchange={changed} />
          {:else}
            <Empty icon="clock-counter-clockwise" title="Nothing yet">
              Actions show up here as Motwi takes them. Try a message on the Rules page to see what it would do.
            </Empty>
          {/each}
        </div>
        {#if data.recent.length}
          <button class="link" onclick={() => go("history")}>See everything in History<Icon name="arrow-right" size={13} /></button>
        {/if}
      </section>

      <section class="sec">
        <h2>Last stream</h2>
        <div class="card">
          {#if data.last_stream}
            <p class="t">{data.last_stream.game ?? "Stream"}</p>
            <p class="muted small">{data.last_stream.title ?? ""}</p>
            <p class="dim small" style="margin-top:6px">
              {data.last_stream.ended_at ? `Ended ${ago(data.last_stream.ended_at)}` : `Live since ${ago(data.last_stream.started_at)}`}
            </p>
            <button class="link" onclick={() => go("stats", { range: "stream" })}>Stats for this stream<Icon name="arrow-right" size={13} /></button>
          {:else}
            <Empty icon="broadcast" title="No streams yet">A recap of each stream shows up here after it ends.</Empty>
          {/if}
        </div>
      </section>
    </div>
  {/if}
</div>

<style>
  .state {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 20px;
    padding: 16px 18px;
  }
  .who {
    display: flex;
    gap: 14px;
    align-items: center;
    min-width: 0;
  }
  .badge {
    width: 40px;
    height: 40px;
    border-radius: var(--r);
    display: grid;
    place-items: center;
    flex: none;
    background: var(--panel-3);
    color: var(--muted);
  }
  .badge.on {
    background: var(--motwi-soft);
    color: var(--motwi);
  }
  .badge.warn {
    background: var(--caution-soft);
    color: var(--caution);
  }
  .badge.bad {
    background: var(--error-soft);
    color: var(--error);
  }
  .big {
    font-size: 16px;
    font-weight: 600;
  }
  .sub {
    font-size: 12.5px;
    margin-top: 2px;
  }
  .live {
    font-size: 11px;
    font-weight: 700;
    background: #e5484d;
    color: #fff;
    padding: 1px 5px;
    border-radius: 3px;
    margin-right: 4px;
  }
  .switches {
    display: grid;
    gap: 8px;
    flex: none;
  }
  .sw-row {
    display: flex;
    gap: 12px;
    align-items: center;
    justify-content: space-between;
    font-size: 13px;
    color: var(--muted);
  }
  .kpis {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
  }
  .kpi {
    padding: 14px 16px;
  }
  .kpi + .kpi {
    border-left: 1px solid var(--line);
  }
  .l {
    font-size: 12px;
    color: var(--muted);
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .v {
    font-size: 22px;
    font-weight: 600;
    margin-top: 4px;
    letter-spacing: -0.02em;
  }
  .d {
    font-size: 11.5px;
    color: var(--dim);
  }
  .cols {
    display: grid;
    grid-template-columns: 1fr 300px;
    gap: 22px;
    align-items: start;
  }
  .t {
    font-weight: 600;
  }
  .small {
    font-size: 12.5px;
  }
  .link {
    margin-top: 10px;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: none;
    border: 0;
    padding: 0;
    color: var(--motwi);
    font-weight: 600;
    font-size: 12.5px;
    cursor: pointer;
  }
  @media (max-width: 1150px) {
    .kpis {
      grid-template-columns: repeat(3, 1fr);
    }
    .kpi:nth-child(4) {
      border-left: 0;
    }
    .kpi:nth-child(n + 4) {
      border-top: 1px solid var(--line);
    }
    .cols {
      grid-template-columns: 1fr;
    }
  }
</style>
