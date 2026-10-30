<script lang="ts">
  import { patch } from "../lib/api";
  import { duration } from "../lib/format";
  import { createSaver } from "../lib/save.svelte";
  import type { Settings } from "../lib/types";
  import SaveState from "./SaveState.svelte";
  import Segmented from "./Segmented.svelte";

  let { settings = $bindable() }: { settings: Settings } = $props();

  const PRESETS = {
    relaxed: { warn: 7, timeout: 8.5, ban: 9.5 },
    standard: { warn: 6, timeout: 7.5, ban: 9 },
    strict: { warn: 5, timeout: 6.5, ban: 8 },
  } as const;

  const saver = createSaver();
  const t = $derived(
    settings.strictness === "custom" && settings.custom_thresholds
      ? settings.custom_thresholds
      : PRESETS[settings.strictness as keyof typeof PRESETS],
  );
  let advanced = $state(false);
  let draft = $state({ warn: 6, timeout: 7.5, ban: 9 });
  let draftError = $state<string | null>(null);

  $effect(() => {
    if (advanced) draft = { ...t };
  });

  const ladder = $derived(
    settings.ladder
      .map((s) =>
        s.kind === "warn" ? "warn" : s.kind === "timeout" ? duration(s.duration_s) : "ban request",
      )
      .join(", "),
  );

  async function pick(value: Settings["strictness"]) {
    const result = await saver.save("strictness", () =>
      patch<Settings>("/settings", { strictness: value }),
    );
    if (result) settings = result;
  }

  async function saveCustom() {
    const { warn, timeout, ban } = draft;
    if (![warn, timeout, ban].every((n) => Number.isFinite(n) && n >= 0 && n <= 10)) {
      draftError = "Each threshold is a score from 0 to 10.";
      return;
    }
    if (!(warn < timeout && timeout < ban)) {
      draftError =
        "Keep them in order: warn below timeout, timeout below ban request. Motwi won't reorder them for you.";
      return;
    }
    draftError = null;
    const result = await saver.save("custom", () =>
      patch<Settings>("/settings", { strictness: "custom", custom_thresholds: draft }),
    );
    if (result) settings = result;
  }
</script>

<div class="card">
  <div class="head">
    <h2>How strict</h2>
    <SaveState state={saver.fields.strictness ?? saver.fields.custom} />
  </div>
  <Segmented
    label="Strictness"
    value={settings.strictness === "custom" ? ("" as Settings["strictness"]) : settings.strictness}
    options={[
      { value: "relaxed", label: "Relaxed" },
      { value: "standard", label: "Standard" },
      { value: "strict", label: "Strict" },
    ]}
    onchange={pick}
  />
  <div
    class="scale"
    role="img"
    aria-label="Warn from {t.warn}, timeout from {t.timeout}, ban request from {t.ban}, on a score from 0 to 10"
  >
    <div
      class="track"
      style:--w="{t.warn * 10}%"
      style:--t="{t.timeout * 10}%"
      style:--b="{t.ban * 10}%"
    ></div>
    <span class="tk mono" style:left="{t.warn * 10}%">{t.warn}</span>
    <span class="tk mono" style:left="{t.timeout * 10}%">{t.timeout}</span>
    <span class="tk mono" style:left="{t.ban * 10}%">{t.ban}</span>
  </div>
  <div class="legend"><span>Score 0 to 10</span><span>warn, timeout, ban request</span></div>
  <p class="sm">Repeat offenders step up: {ladder}.</p>
  {#if settings.strictness === "custom"}<p class="sm">Using your own thresholds.</p>{/if}

  <button class="more" aria-expanded={advanced} onclick={() => (advanced = !advanced)}>
    {advanced ? "Hide" : "Set"} exact thresholds
  </button>
  {#if advanced}
    <div class="adv">
      {#each [["warn", "Warn at"], ["timeout", "Timeout at"], ["ban", "Ban request at"]] as [key, label] (key)}
        <div>
          <label class="label" for="th-{key}">{label}</label>
          <input
            id="th-{key}"
            class="input mono"
            type="number"
            min="0"
            max="10"
            step="0.5"
            bind:value={draft[key as "warn"]}
            aria-invalid={draftError ? "true" : undefined}
          />
        </div>
      {/each}
    </div>
    {#if draftError}<p class="err" role="alert">{draftError}</p>{/if}
    <button class="btn sm" style="margin-top:10px" onclick={saveCustom}>Use these numbers</button>
  {/if}
</div>

<style>
  .head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
  }
  .scale {
    margin-top: 14px;
    position: relative;
    height: 30px;
  }
  .track {
    position: absolute;
    left: 0;
    right: 0;
    top: 10px;
    height: 4px;
    border-radius: 2px;
    background: linear-gradient(
      90deg,
      var(--line) 0 var(--w),
      var(--warn) var(--w) var(--t),
      var(--timeout) var(--t) var(--b),
      var(--ban) var(--b)
    );
  }
  .tk {
    position: absolute;
    top: 18px;
    font-size: 10.5px;
    color: var(--dim);
    transform: translateX(-50%);
  }
  .legend {
    display: flex;
    justify-content: space-between;
    font-size: 11.5px;
    color: var(--muted);
    margin-top: 6px;
  }
  .sm {
    font-size: 12px;
    color: var(--muted);
    margin-top: 10px;
  }
  .more {
    margin-top: 10px;
    background: none;
    border: 0;
    padding: 0;
    color: var(--motwi);
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
  }
  .adv {
    margin-top: 10px;
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
  }
</style>
