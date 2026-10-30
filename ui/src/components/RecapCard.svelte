<script lang="ts">
  import { ago, count, duration, KIND_COLOR, money } from "../lib/format";
  import type { Recap } from "../lib/types";

  let { recap }: { recap: Recap } = $props();

  const ROWS = [
    ["delete", "Deleted"],
    ["warn", "Warned"],
    ["timeout", "Timeouts"],
    ["ban_request", "Bans"],
  ] as const;

  function minutes(seconds: number): string {
    const m = Math.round(seconds / 60);
    if (m < 1) return "under a minute";
    if (m < 60) return `${m}m`;
    return `${Math.floor(m / 60)}h ${m % 60}m`;
  }

  function furthest(kind: string, durationS: number | null): string {
    if (kind === "ban_request") return "ban request";
    if (kind === "timeout") return `timeout ${duration(durationS)}`.trim();
    if (kind === "warn") return "warning";
    return "delete";
  }

  const length = $derived(minutes(recap.ended_at - recap.started_at));
  const down = $derived(recap.outages.filter((o) => o.state === "down"));
  const backup = $derived(recap.outages.filter((o) => o.state === "degraded"));
  const total = (spans: { started_at: number; ended_at: number }[]) =>
    spans.reduce((sum, o) => sum + o.ended_at - o.started_at, 0);
</script>

<div class="recap">
  <div>
    <p class="t">{recap.game ?? "Stream"}</p>
    {#if recap.title}<p class="muted small title" title={recap.title}>{recap.title}</p>{/if}
    <p class="dim small">{recap.seen_ending ? "Ended" : "Last activity"} {ago(recap.ended_at)}, {length} long</p>
  </div>

  {#if !recap.seen_ending}
    <p class="note">Motwi wasn't running when this stream ended, so it may have missed the end.</p>
  {/if}

  <dl class="nums">
    <div><dt>Messages checked</dt><dd class="mono">{count(recap.checked)}</dd></div>
    {#each ROWS as [kind, label] (kind)}
      {@const t = recap.actions[kind]}
      <div>
        <dt><span class="sw" style:background={KIND_COLOR[kind]}></span>{label}</dt>
        <dd class="mono">
          {count(t.count)}
          {#if t.undone || t.denied || t.failed}
            <small>
              {[t.undone && `${t.undone} undone`, t.denied && `${t.denied} denied`, t.failed && `${t.failed} failed`]
                .filter(Boolean)
                .join(", ")}
            </small>
          {/if}
        </dd>
      </div>
    {/each}
    <div><dt>AI cost</dt><dd class="mono">{money(recap.cost)}</dd></div>
  </dl>

  {#if down.length || backup.length || recap.unchecked}
    <div class="group">
      <h3>AI trouble</h3>
      <ul>
        {#if down.length}
          <li>Down {down.length === 1 ? "once" : `${down.length} times`}, {minutes(total(down))} in all</li>
        {/if}
        {#if backup.length}<li>On the backup for {minutes(total(backup))}</li>{/if}
        {#if recap.unchecked}<li>{count(recap.unchecked)} messages couldn't be checked</li>{/if}
      </ul>
    </div>
  {/if}

  {#if recap.triggers.length}
    <div class="group">
      <h3>Top triggers</h3>
      <ul>
        {#each recap.triggers as t (t.label)}
          <li class="row"><span class="grow">{t.label}</span><span class="mono">{count(t.count)}</span></li>
        {/each}
      </ul>
    </div>
  {/if}

  {#if recap.escalated.length}
    <div class="group">
      <h3>Escalated furthest</h3>
      <ul>
        {#each recap.escalated as c (c.login)}
          <li class="row">
            <span class="grow"><b>{c.login}</b> reached {furthest(c.kind, c.duration_s)}</span>
            <span class="mono">{c.actions}x</span>
          </li>
        {/each}
      </ul>
    </div>
  {/if}
</div>

<style>
  .recap {
    display: grid;
    gap: 12px;
  }
  .t {
    font-weight: 600;
  }
  .small {
    font-size: 12.5px;
  }
  .title {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .note {
    font-size: 12.5px;
    color: var(--caution);
  }
  .nums {
    display: grid;
    gap: 5px;
    margin: 0;
    padding: 10px 0;
    border-block: 1px solid var(--line);
  }
  .nums div {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    font-size: 13px;
  }
  dt {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--muted);
  }
  dd {
    margin: 0;
    text-align: right;
  }
  small {
    display: block;
    font-family: var(--sans);
    font-size: 11.5px;
    color: var(--dim);
  }
  h3 {
    font-size: 12px;
    font-weight: 600;
    color: var(--muted);
    margin-bottom: 5px;
  }
  ul {
    list-style: none;
    padding: 0;
    display: grid;
    gap: 4px;
    font-size: 12.5px;
  }
  .row {
    display: flex;
    gap: 10px;
    justify-content: space-between;
  }
  .grow {
    min-width: 0;
    overflow-wrap: anywhere;
  }
  b {
    font-weight: 600;
  }
</style>
