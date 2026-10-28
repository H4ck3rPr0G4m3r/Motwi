<script lang="ts">
  import Empty from "../components/Empty.svelte";
  import Icon from "../components/Icon.svelte";
  import LoadError from "../components/LoadError.svelte";
  import Segmented from "../components/Segmented.svelte";
  import { get, message } from "../lib/api";
  import { count, KIND_COLOR, money, when } from "../lib/format";
  import { nav } from "../lib/router.svelte";
  import type { DayRow, Stats } from "../lib/types";

  type Range = "stream" | "7d" | "30d";
  // stack order validated for color-vision deficiency on dark surfaces (spec 6.4)
  const SERIES = [
    { key: "warn", label: "Warned" },
    { key: "ban_request", label: "Bans" },
    { key: "delete", label: "Deleted" },
    { key: "timeout", label: "Timeouts" },
  ] as const;

  let range = $state<Range>((nav.query.get("range") as Range) || "7d");
  let data = $state<Stats | null>(null);
  let error = $state<string | null>(null);
  let view = $state<"chart" | "table">("chart");
  let hover = $state<number | null>(null);

  async function load() {
    error = null;
    data = null;
    try {
      data = await get<Stats>(`/stats?range=${range}`);
    } catch (e) {
      error = message(e);
    }
  }

  $effect(() => {
    void range;
    load();
  });

  const days = $derived(data?.per_day ?? []);
  const total = (d: DayRow) => d.warn + d.ban_request + d.delete + d.timeout;
  const top = $derived.by(() => {
    const max = Math.max(0, ...days.map(total));
    if (max === 0) return 4;
    const mag = 10 ** Math.floor(Math.log10(max));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => max <= s * 4) ?? mag * 10;
    return step * 4;
  });
  const ticks = $derived([4, 3, 2, 1, 0].map((i) => (top / 4) * i));
  const triggerMax = $derived(Math.max(1, ...(data?.triggers ?? []).map((t) => t.count)));
  const actionsTotal = $derived((data?.triggers ?? []).reduce((s, t) => s + t.count, 0));
  const labelEvery = $derived(days.length > 14 ? 5 : 1);

  function dayLabel(day: string, long = false): string {
    const d = new Date(`${day}T12:00:00`);
    if (long) return d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
    return days.length > 8
      ? d.toLocaleDateString([], { month: "numeric", day: "numeric" })
      : d.toLocaleDateString([], { weekday: "short" });
  }

  function describe(d: DayRow): string {
    if (!total(d)) return `${dayLabel(d.day, true)}: no actions`;
    return `${dayLabel(d.day, true)}: ${SERIES.map((s) => `${d[s.key]} ${s.label.toLowerCase()}`).join(", ")}`;
  }

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
      <h1>Stats</h1>
      <p>
        {#if range === "stream" && data?.stream}
          Last stream, {when(data.stream.started_at)}{data.stream.game ? `, ${data.stream.game}` : ""}.
        {:else}
          What Motwi did and why.
        {/if}
      </p>
    </div>
    <div style="width:300px">
      <Segmented
        label="Time range"
        value={range}
        options={[
          { value: "stream", label: "Last stream" },
          { value: "7d", label: "7 days" },
          { value: "30d", label: "30 days" },
        ]}
        onchange={(v) => (range = v)}
      />
    </div>
  </div>

  {#if error}
    <LoadError {error} retry={load} />
  {:else if !data}
    <div class="skel" style="height:92px"></div>
    <div class="charts">
      <div class="skel" style="height:320px"></div>
      <div class="skel" style="height:320px"></div>
    </div>
  {:else if data.empty}
    <div class="panel">
      <Empty icon="chart-bar" title={range === "stream" ? "No streams recorded yet" : "Nothing to show for this range"}>
        {range === "stream"
          ? "Stats for a stream appear once Motwi has watched one from start to end."
          : "Motwi hasn't checked or acted on any messages in this range."}
      </Empty>
    </div>
  {:else if data.totals}
    <section class="kpis panel" aria-label="Totals">
      <div class="kpi">
        <p class="l">Messages checked</p>
        <p class="v mono">{count(data.checked)}</p>
        <p class="d">{range === "stream" ? "this stream" : `across ${data.streams} ${data.streams === 1 ? "stream" : "streams"}`}</p>
      </div>
      {#each KPIS as [kind, label] (kind)}
        {@const t = data.totals[kind]}
        <div class="kpi">
          <p class="l"><span class="sw" style:background={KIND_COLOR[kind]}></span>{label}</p>
          <p class="v mono">{count(t.count)}</p>
          <p class="d">{kind === "ban_request" ? `${t.denied} denied` : `${t.undone} undone`}{t.failed ? `, ${t.failed} failed` : ""}</p>
        </div>
      {/each}
    </section>

    <div class="charts">
      <section class="chart panel">
        <div class="chead">
          <h2>Actions per day</h2>
          <div style="width:150px">
            <Segmented
              label="Show as"
              value={view}
              options={[
                { value: "chart", label: "Chart" },
                { value: "table", label: "Table" },
              ]}
              onchange={(v) => (view = v)}
            />
          </div>
        </div>
        <div class="legend" aria-hidden={view === "table"}>
          {#each SERIES as s (s.key)}<span><i style:background={KIND_COLOR[s.key]}></i>{s.label}</span>{/each}
        </div>

        {#if view === "chart"}
          <div class="plot" role="group" aria-label="Actions per day, stacked by action. Use the table view for exact numbers.">
            <div class="yaxis mono" aria-hidden="true">
              {#each ticks as t (t)}<span>{t}</span>{/each}
            </div>
            <div class="area">
              <div class="grid" aria-hidden="true">{#each ticks as t (t)}<div class:base={t === 0}></div>{/each}</div>
              <div class="cols" style:--n={days.length}>
                {#each days as d, i (d.day)}
                  {@const sum = total(d)}
                  <button
                    class="col"
                    aria-label={describe(d)}
                    onmouseenter={() => (hover = i)}
                    onmouseleave={() => (hover = null)}
                    onfocus={() => (hover = i)}
                    onblur={() => (hover = null)}
                  >
                    <span class="stack" style:height="{(sum / top) * 100}%">
                      {#each SERIES as s (s.key)}
                        {#if d[s.key]}<span class="seg" style:flex-grow={d[s.key]} style:background={KIND_COLOR[s.key]}></span>{/if}
                      {/each}
                    </span>
                    {#if hover === i}
                      <span class="tip" class:left={i > days.length * 0.7} role="tooltip">
                        <b>{dayLabel(d.day, true)}</b>
                        {#if sum}
                          {#each [...SERIES].reverse() as s (s.key)}
                            <span class="tr"><i style:background={KIND_COLOR[s.key]}></i>{s.label}<span class="mono">{d[s.key]}</span></span>
                          {/each}
                        {:else}
                          <span class="dim">No actions</span>
                        {/if}
                      </span>
                    {/if}
                  </button>
                {/each}
              </div>
              <div class="xaxis mono" style:--n={days.length} aria-hidden="true">
                {#each days as d, i (d.day)}<span>{i % labelEvery === 0 || i === days.length - 1 ? dayLabel(d.day) : ""}</span>{/each}
              </div>
            </div>
          </div>
        {:else}
          <div class="tablewrap">
            <table>
              <thead>
                <tr><th scope="col">Day</th>{#each SERIES as s (s.key)}<th scope="col" class="num">{s.label}</th>{/each}<th scope="col" class="num">Total</th></tr>
              </thead>
              <tbody>
                {#each days as d (d.day)}
                  <tr>
                    <th scope="row">{dayLabel(d.day, true)}</th>
                    {#each SERIES as s (s.key)}<td class="num mono">{d[s.key]}</td>{/each}
                    <td class="num mono">{total(d)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      </section>

      <section class="chart panel">
        <h2>What triggered them</h2>
        <p class="sub">{actionsTotal ? `All ${count(actionsTotal)} actions` : "No actions in this range"}</p>
        <div class="hb">
          {#each (data.triggers ?? []).slice(0, 8) as t (t.label)}
            <div class="hrow">
              <span class="hl">{t.label}</span><span class="n mono">{count(t.count)}</span>
              <div class="bar" style:width="{(t.count / triggerMax) * 100}%"></div>
            </div>
          {:else}
            <p class="dim small">Rules that trigger actions are ranked here.</p>
          {/each}
        </div>
        <div class="foot">
          <span><Icon name="coins" size={14} />AI cost, {range === "stream" ? "this stream" : range === "7d" ? "7 days" : "30 days"}</span>
          <span class="mono">{money(data.cost)}</span>
        </div>
      </section>
    </div>
  {/if}
</div>

<style>
  .kpis {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
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
    font-size: 24px;
    font-weight: 600;
    margin-top: 4px;
    letter-spacing: -0.02em;
  }
  .d {
    font-size: 11.5px;
    color: var(--dim);
  }
  .charts {
    display: grid;
    grid-template-columns: 1.6fr 1fr;
    gap: 22px;
    min-height: 360px;
  }
  .chart {
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .chart h2 {
    font-size: 13px;
    font-weight: 600;
  }
  .chead {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
  }
  .sub {
    font-size: 12px;
    color: var(--dim);
  }
  .legend {
    display: flex;
    gap: 14px;
    margin-top: 10px;
    font-size: 12px;
    color: var(--muted);
  }
  .legend span,
  .tr {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .legend i,
  .tr i {
    width: 9px;
    height: 9px;
    border-radius: 2px;
    display: inline-block;
  }
  .plot {
    flex: 1;
    display: grid;
    grid-template-columns: 32px 1fr;
    gap: 10px;
    margin-top: 14px;
    min-height: 240px;
  }
  .yaxis {
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    font-size: 10.5px;
    color: var(--dim);
    text-align: right;
    padding-bottom: 22px;
    line-height: 0;
  }
  .area {
    position: relative;
    display: grid;
    grid-template-rows: 1fr 22px;
  }
  .grid {
    position: absolute;
    inset: 0 0 22px 0;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    pointer-events: none;
  }
  .grid div {
    border-top: 1px solid #232328;
  }
  .grid div.base {
    border-color: #3a3a42;
  }
  .cols {
    position: relative;
    display: grid;
    grid-template-columns: repeat(var(--n), 1fr);
    gap: 0 clamp(3px, 1.2vw, 14px);
    align-items: end;
  }
  .col {
    position: relative;
    height: 100%;
    background: none;
    border: 0;
    padding: 0;
    display: flex;
    align-items: flex-end;
    cursor: default;
    border-radius: 4px 4px 0 0;
  }
  .col:hover .stack,
  .col:focus-visible .stack {
    filter: brightness(1.12);
  }
  .stack {
    width: 100%;
    display: flex;
    flex-direction: column-reverse;
    gap: 2px;
    min-height: 0;
  }
  .seg {
    flex-basis: 0;
    min-height: 2px;
  }
  .seg:last-child {
    border-radius: 4px 4px 0 0;
  }
  .tip {
    position: absolute;
    bottom: calc(100% - 20px);
    left: 50%;
    transform: translate(-50%, -100%);
    z-index: 5;
    background: #26262c;
    border: 1px solid var(--line-2);
    border-radius: var(--r);
    padding: 8px 10px;
    font-size: 12px;
    display: grid;
    gap: 4px;
    min-width: 150px;
    text-align: left;
    pointer-events: none;
    box-shadow: 0 8px 24px rgb(0 0 0 / 0.4);
  }
  .tip.left {
    left: auto;
    right: 0;
    transform: translateY(-100%);
  }
  .tr .mono {
    margin-left: auto;
    padding-left: 12px;
  }
  .xaxis {
    display: grid;
    grid-template-columns: repeat(var(--n), 1fr);
    gap: 0 clamp(3px, 1.2vw, 14px);
    font-size: 10.5px;
    color: var(--dim);
    text-align: center;
    padding-top: 6px;
    white-space: nowrap;
  }
  .tablewrap {
    margin-top: 12px;
    overflow: auto;
    max-height: 320px;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12.5px;
  }
  th,
  td {
    padding: 6px 8px;
    text-align: left;
    border-bottom: 1px solid var(--line);
  }
  thead th {
    color: var(--muted);
    font-weight: 600;
    position: sticky;
    top: 0;
    background: var(--panel);
  }
  tbody th {
    font-weight: 500;
  }
  .num {
    text-align: right;
  }
  .hb {
    display: grid;
    gap: 12px;
    margin-top: 14px;
  }
  .hrow {
    display: grid;
    grid-template-columns: 1fr 44px;
    gap: 4px 10px;
    align-items: center;
    font-size: 12.5px;
  }
  .hl {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .hrow .bar {
    grid-column: 1 / -1;
    height: 8px;
    border-radius: 0 4px 4px 0;
    background: var(--muted);
    opacity: 0.75;
  }
  .n {
    text-align: right;
    font-weight: 600;
    font-size: 12px;
    color: var(--muted);
  }
  .small {
    font-size: 12.5px;
  }
  .foot {
    margin-top: auto;
    padding-top: 12px;
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    color: var(--muted);
    border-top: 1px solid var(--line);
  }
  .hb {
    margin-bottom: 16px;
  }
  .foot span:first-child {
    display: flex;
    gap: 6px;
    align-items: center;
  }
  @media (max-width: 1150px) {
    .charts {
      grid-template-columns: 1fr;
    }
    .kpis {
      grid-template-columns: repeat(3, 1fr);
    }
  }
</style>
