<script lang="ts">
  import { onMount } from "svelte";
  import DeviceLogin from "../components/DeviceLogin.svelte";
  import ExtensionPanel from "../components/ExtensionPanel.svelte";
  import Icon from "../components/Icon.svelte";
  import LoadError from "../components/LoadError.svelte";
  import ProviderForm from "../components/ProviderForm.svelte";
  import SaveState from "../components/SaveState.svelte";
  import Segmented from "../components/Segmented.svelte";
  import Toggle from "../components/Toggle.svelte";
  import { get, message, patch, post } from "../lib/api";
  import { core, setStatus } from "../lib/events.svelte";
  import { duration } from "../lib/format";
  import { createSaver } from "../lib/save.svelte";
  import type { AIConfig, LadderStep, Settings, Status } from "../lib/types";

  let settings = $state<Settings | null>(null);
  let config = $state<AIConfig | null>(null);
  let error = $state<string | null>(null);
  let showBackup = $state(false);
  let accountError = $state<string | null>(null);
  let botWarning = $state<string | null>(null);
  let retention = $state(30);
  let ladder = $state<LadderStep[]>([]);
  const saver = createSaver();

  const s = $derived(core.status!);

  async function load() {
    error = null;
    try {
      [settings, config] = await Promise.all([get<Settings>("/settings"), get<AIConfig>("/ai")]);
      retention = settings.retention_days;
      ladder = settings.ladder.map((x) => ({ ...x }));
      showBackup = !!config.backup;
    } catch (e) {
      error = message(e);
    }
  }
  onMount(load);

  async function set(field: keyof Settings, value: unknown) {
    const result = await saver.save(String(field), () => patch<Settings>("/settings", { [field]: value }));
    if (result) settings = result;
  }

  async function refreshStatus() {
    setStatus(await get<Status>("/status"));
  }

  async function signOut(role: "streamer" | "bot") {
    accountError = null;
    try {
      setStatus(await post<Status>("/twitch/logout", { role }));
    } catch (e) {
      accountError = message(e);
    }
  }

  async function reconnect() {
    accountError = null;
    try {
      setStatus(await post<Status>("/twitch/reconnect"));
    } catch (e) {
      accountError = message(e);
    }
  }

  function saveLadder() {
    set("ladder", ladder);
  }

  function addStep() {
    const last = ladder[ladder.length - 1];
    if (last?.kind === "ban_request") {
      // a new timeout goes before the ban request, longer than the last timeout
      const longest = Math.max(60, ...ladder.map((x) => (x.kind === "timeout" ? (x.duration_s ?? 0) : 0)));
      const next: LadderStep = { kind: "timeout", duration_s: Math.min(longest * 6, 604800) };
      ladder = [...ladder.slice(0, -1), next, last];
    } else {
      ladder = [...ladder, { kind: "ban_request", duration_s: null }];
    }
    saveLadder();
  }

  function removeStep(i: number) {
    ladder = ladder.filter((_, j) => j !== i);
    saveLadder();
  }

  const DURATIONS = [60, 300, 600, 1800, 3600, 21600, 86400, 604800];
  const HOLDS = [300, 600, 1800, 3600, 7200];
  const twitchLabel = $derived(
    {
      signed_out: "Not connected",
      idle: "Connected, not running",
      connecting: "Connecting",
      connected: "Connected",
      reconnect: "Sign-in expired",
      error: "Problem",
    }[s.twitch.state],
  );
</script>

<div class="page">
  <div class="pagehead">
    <div>
      <h1>Settings</h1>
      <p>Changes save as soon as you make them.</p>
    </div>
  </div>

  {#if error}
    <LoadError {error} retry={load} />
  {:else if !settings || !config}
    <div class="skel" style="height:140px;max-width:760px"></div>
    <div class="skel" style="height:240px;max-width:760px"></div>
  {:else}
    <div class="col">
      <section class="sec" id="twitch">
        <h2>Twitch</h2>
        <div class="panel rows">
          <div class="row">
            <div class="grow">
              <p class="label">Your account</p>
              {#if s.accounts.streamer}
                <p class="muted">
                  <b class="text">{s.accounts.streamer.login}</b>
                  <span class:bad={s.twitch.state === "reconnect" || s.twitch.state === "error"}>{twitchLabel}</span>
                </p>
                {#if s.twitch.detail}<p class="help">{s.twitch.detail}</p>{/if}
              {:else}
                <p class="help">Motwi reads chat and moderates as this account.</p>
              {/if}
            </div>
            <div class="acts">
              {#if s.accounts.streamer && s.twitch.state !== "reconnect"}
                <button class="btn sm" onclick={reconnect}><Icon name="arrow-clockwise" size={13} />Reconnect</button>
                <button class="btn sm ghost" onclick={() => signOut("streamer")}><Icon name="sign-out" size={13} />Sign out</button>
              {/if}
            </div>
          </div>
          {#if !s.accounts.streamer || s.twitch.state === "reconnect"}
            <div class="row">
              <DeviceLogin
                role="streamer"
                label={s.accounts.streamer ? "Reconnect Twitch" : "Connect Twitch"}
                ondone={refreshStatus}
              />
            </div>
          {/if}
          {#if s.accounts.streamer}
            <div class="row">
              <div class="grow">
                <p class="label">Bot account <span class="dim">optional</span></p>
                {#if s.accounts.bot}
                  <p class="muted"><b class="text">{s.accounts.bot.login}</b> reads chat and takes actions.</p>
                {:else}
                  <p class="help">Actions show as a separate account. It must be a mod: type <span class="mono">/mod botname</span> in your chat first.</p>
                {/if}
                {#if botWarning}<p class="err">{botWarning}</p>{/if}
              </div>
              <div class="acts">
                {#if s.accounts.bot}
                  <button class="btn sm ghost" onclick={() => signOut("bot")}>Remove bot</button>
                {:else}
                  <DeviceLogin
                    role="bot"
                    label="Connect a bot"
                    ondone={(_, w) => {
                      botWarning = w;
                      refreshStatus();
                    }}
                  />
                {/if}
              </div>
            </div>
          {/if}
        </div>
        {#if accountError}<p class="err" role="alert">{accountError}</p>{/if}
      </section>

      <section class="sec" id="ai">
        <h2>AI provider</h2>
        <div class="card"><ProviderForm slot="primary" bind:config /></div>
        <h2 style="margin-top:16px">Backup <span>used automatically when the main one fails</span></h2>
        {#if showBackup}
          <div class="card"><ProviderForm slot="backup" bind:config onsaved={(c) => (showBackup = !!c.backup || showBackup)} /></div>
        {:else}
          <button class="btn" onclick={() => (showBackup = true)}><Icon name="plus" size={14} />Add a backup</button>
        {/if}
      </section>

      <section class="sec" id="extension">
        <h2>Browser extension <span>Motwi's decisions, undo and ban requests inside Twitch chat</span></h2>
        <div class="card"><ExtensionPanel /></div>
      </section>

      <section class="sec" id="actions">
        <h2>What Motwi does</h2>
        <div class="panel rows">
          <div class="row">
            <div class="grow">
              <p class="label" id="suggest-l">Suggest only</p>
              <p class="help">Motwi shows what it would do, and nothing happens until you or a mod clicks.</p>
            </div>
            <SaveState state={saver.fields.suggest_only} />
            <Toggle checked={settings.suggest_only} label="Suggest only" onchange={(v) => set("suggest_only", v)} />
          </div>
          <div class="row">
            <div class="grow">
              <p class="label">How to warn</p>
              <p class="help">A Twitch warning must be acknowledged before the chatter can talk again.</p>
            </div>
            <SaveState state={saver.fields.warn_style} />
            <div style="width:260px">
              <Segmented
                label="How to warn"
                value={settings.warn_style}
                options={[
                  { value: "twitch", label: "Twitch warning" },
                  { value: "chat", label: "@mention in chat" },
                ]}
                onchange={(v) => set("warn_style", v)}
              />
            </div>
          </div>
          <div class="row">
            <div class="grow">
              <p class="label">Delete warned messages</p>
              <p class="help">Off keeps the message in chat and only warns.</p>
            </div>
            <SaveState state={saver.fields.delete_on_warn} />
            <Toggle checked={settings.delete_on_warn} label="Delete warned messages" onchange={(v) => set("delete_on_warn", v)} />
          </div>
          <div class="row">
            <div class="grow">
              <p class="label">Hold chatters during a ban request</p>
              <p class="help">Times them out while you decide, so they can't keep posting. Deny lifts it.</p>
            </div>
            <SaveState state={saver.fields.hold_timeout_enabled ?? saver.fields.hold_timeout_s} />
            {#if settings.hold_timeout_enabled}
              <label class="sr-only" for="hold">Hold length</label>
              <select id="hold" class="input short" value={settings.hold_timeout_s} onchange={(e) => set("hold_timeout_s", Number(e.currentTarget.value))}>
                {#each HOLDS as h (h)}<option value={h}>{duration(h)}</option>{/each}
                {#if !HOLDS.includes(settings.hold_timeout_s)}<option value={settings.hold_timeout_s}>{duration(settings.hold_timeout_s)}</option>{/if}
              </select>
            {/if}
            <Toggle checked={settings.hold_timeout_enabled} label="Hold chatters during a ban request" onchange={(v) => set("hold_timeout_enabled", v)} />
          </div>
          <div class="row ladder">
            <div class="grow">
              <p class="label">Escalation for repeat offenders</p>
              <p class="help">Each offense in a stream moves the chatter one step. A rule's chip caps how far it goes.</p>
              <ol class="steps">
                {#each ladder as step, i (i)}
                  <li>
                    <span class="mono dim">{i + 1}</span>
                    <label class="sr-only" for="step-{i}">Step {i + 1}</label>
                    <select
                      id="step-{i}"
                      class="input"
                      value={step.kind === "timeout" ? String(step.duration_s) : step.kind}
                      onchange={(e) => {
                        const v = e.currentTarget.value;
                        ladder[i] = v === "warn" || v === "ban_request" ? { kind: v, duration_s: null } : { kind: "timeout", duration_s: Number(v) };
                        saveLadder();
                      }}
                    >
                      <option value="warn">Warn</option>
                      {#each DURATIONS as d (d)}<option value={String(d)}>Timeout {duration(d)}</option>{/each}
                      {#if step.kind === "timeout" && !DURATIONS.includes(step.duration_s ?? 0)}<option value={String(step.duration_s)}>Timeout {duration(step.duration_s)}</option>{/if}
                      <option value="ban_request">Ban request</option>
                    </select>
                    <button class="btn icon" aria-label="Remove step {i + 1}" disabled={ladder.length <= 1} onclick={() => removeStep(i)}><Icon name="x" size={14} /></button>
                  </li>
                {/each}
              </ol>
              {#if ladder.length < 8}<button class="btn sm" onclick={addStep}><Icon name="plus" size={13} />Add a step</button>{/if}
            </div>
            <SaveState state={saver.fields.ladder} />
          </div>
        </div>
      </section>

      <section class="sec" id="data">
        <h2>Data</h2>
        <div class="panel rows">
          <div class="row">
            <div class="grow">
              <label class="label" for="retention">Keep history for</label>
              <p class="help">Motwi stores only messages it acted on, never all of chat. Older records are deleted.</p>
            </div>
            <SaveState state={saver.fields.retention_days} />
            <div class="days">
              <input
                id="retention"
                class="input mono short"
                type="number"
                min="1"
                max="365"
                bind:value={retention}
                onblur={() => retention !== settings?.retention_days && set("retention_days", Number(retention))}
                onkeydown={(e) => e.key === "Enter" && set("retention_days", Number(retention))}
              />
              <span class="muted">days</span>
            </div>
          </div>
        </div>
      </section>

      <section class="sec" id="app">
        <h2>App</h2>
        <div class="panel rows">
          <div class="row">
            <div class="grow">
              <p class="label">Start with Windows</p>
              <p class="help">Arrives with the installer.</p>
            </div>
            <Toggle checked={false} label="Start with Windows" disabled onchange={() => {}} />
          </div>
          <div class="row">
            <div class="grow">
              <p class="label">Version</p>
              <p class="help">Motwi {s.version}. Update checks arrive with the installer.</p>
            </div>
          </div>
        </div>
      </section>
    </div>
  {/if}
</div>

<style>
  .col {
    max-width: 780px;
    display: flex;
    flex-direction: column;
    gap: 26px;
  }
  .row {
    display: flex;
    gap: 14px;
    align-items: center;
    padding: 12px 14px;
  }
  .row.ladder {
    align-items: flex-start;
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  .row .label {
    margin-bottom: 2px;
  }
  .row .help {
    margin-top: 2px;
  }
  .text {
    color: var(--text);
    margin-right: 8px;
  }
  .bad {
    color: var(--error);
    font-weight: 600;
  }
  .acts {
    display: flex;
    gap: 6px;
    align-items: center;
  }
  .short {
    width: 96px;
  }
  .days {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .steps {
    list-style: none;
    padding: 0;
    display: grid;
    gap: 6px;
    margin: 10px 0;
    max-width: 280px;
  }
  .steps li {
    display: grid;
    grid-template-columns: 18px 1fr auto;
    gap: 8px;
    align-items: center;
  }
</style>
