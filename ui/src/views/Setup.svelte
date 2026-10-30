<script lang="ts">
  import { onMount } from "svelte";
  import DeviceLogin from "../components/DeviceLogin.svelte";
  import ExtensionPanel from "../components/ExtensionPanel.svelte";
  import Icon from "../components/Icon.svelte";
  import LoadError from "../components/LoadError.svelte";
  import ProviderForm from "../components/ProviderForm.svelte";
  import RuleList from "../components/RuleList.svelte";
  import Strictness from "../components/Strictness.svelte";
  import Toggle from "../components/Toggle.svelte";
  import { get, message, patch, post } from "../lib/api";
  import { core, setStatus } from "../lib/events.svelte";
  import { modelLabel, PROVIDER_LABEL } from "../lib/format";
  import type { AIConfig, ExtensionInfo, Rule, Settings, Status } from "../lib/types";

  const STEPS = ["Welcome", "Twitch", "AI", "Rules", "Extension", "Done"];
  let step = $state(0);
  let config = $state<AIConfig | null>(null);
  let settings = $state<Settings | null>(null);
  let rules = $state<Rule[]>([]);
  let loadError = $state<string | null>(null);
  let botWarning = $state<string | null>(null);
  let showBot = $state(false);
  let showBackup = $state(false);
  let finishing = $state(false);
  let finishError = $state<string | null>(null);
  let extension = $state<ExtensionInfo | null>(null);

  const status = $derived(core.status!);
  const streamer = $derived(status.accounts.streamer);
  const bot = $derived(status.accounts.bot);
  const aiReady = $derived(!!config?.primary?.has_key);

  async function load() {
    loadError = null;
    try {
      [config, settings, rules] = await Promise.all([
        get<AIConfig>("/ai"),
        get<Settings>("/settings"),
        get<Rule[]>("/rules"),
      ]);
    } catch (e) {
      loadError = message(e);
    }
  }

  onMount(load);

  async function refreshStatus() {
    setStatus(await get<Status>("/status"));
  }

  async function setSuggest(value: boolean) {
    settings = await patch<Settings>("/settings", { suggest_only: value });
  }

  async function signOutBot() {
    await post("/twitch/logout", { role: "bot" });
    await refreshStatus();
  }

  async function finish() {
    finishing = true;
    finishError = null;
    try {
      await patch("/settings", { setup_complete: true });
      await refreshStatus();
      location.hash = "#/overview";
    } catch (e) {
      finishError = message(e);
    } finally {
      finishing = false;
    }
  }

  const canNext = $derived(
    step === 0 || (step === 1 && !!streamer) || (step === 2 && aiReady) || step === 3 || step === 4,
  );
  const blocker = $derived(
    step === 1 && !streamer
      ? "Connect your Twitch account to continue."
      : step === 2 && !aiReady
        ? "Test and save a provider to continue."
        : null,
  );
</script>

<div class="setup">
  <aside class="steps" aria-label="Setup steps">
    <div class="brand"><span class="logo" aria-hidden="true">M</span><b>Motwi setup</b></div>
    <ol>
      {#each STEPS as name, i (name)}
        <li aria-current={i === step ? "step" : undefined} class:done={i < step}>
          <span class="n mono">{#if i < step}<Icon name="check" size={12} />{:else}{i + 1}{/if}</span>{name}
        </li>
      {/each}
    </ol>
  </aside>

  <main class="content">
    {#if loadError}
      <LoadError error={loadError} retry={load} />
    {:else if !config || !settings}
      <div class="skel" style="height:28px;width:50%"></div>
      <div class="skel" style="height:120px;margin-top:16px"></div>
    {:else}
      <div class="body">
        {#if step === 0}
          <h1>Motwi watches your chat so you don't have to.</h1>
          <p class="lead">
            It reads every message against your rules, acts on clear cases right away, and asks you before
            anything serious like a ban. Setup takes about five minutes.
          </p>
          <ul class="facts">
            <li><Icon name="twitch-logo" size={18} /><span>Connect your Twitch account. No developer app or tokens to copy.</span></li>
            <li><Icon name="cpu" size={18} /><span>Pick an AI provider and paste its key. A few cents covers a typical stream.</span></li>
            <li><Icon name="scroll" size={18} /><span>Start from a set of common rules and make them yours.</span></li>
          </ul>
        {:else if step === 1}
          <h1>Connect Twitch</h1>
          <p class="lead">
            Motwi reads chat and moderates as your account. Twitch asks you to approve it with a short code.
          </p>
          {#if streamer}
            <div class="connected">
              <Icon name="check-circle" size={18} />
              <span>Connected as <b>{streamer.login}</b></span>
            </div>
          {:else}
            <DeviceLogin role="streamer" label="Connect Twitch" ondone={refreshStatus} />
          {/if}

          {#if streamer}
            <div class="optional">
              <button class="more" aria-expanded={showBot || !!bot} onclick={() => (showBot = !showBot)}>
                <Icon name={showBot || bot ? "caret-down" : "caret-right"} size={12} />Use a separate bot account (optional)
              </button>
              {#if showBot || bot}
                <p class="help">
                  Actions then show as the bot instead of you. Make it a mod first: type
                  <span class="mono">/mod botname</span> in your chat.
                </p>
                {#if bot}
                  <div class="connected">
                    <Icon name="robot" size={18} /><span>Bot: <b>{bot.login}</b></span>
                    <button class="btn sm ghost" onclick={signOutBot}>Remove</button>
                  </div>
                  {#if botWarning}<div class="notice warn"><span class="ic"><Icon name="warning" size={16} /></span>{botWarning}</div>{/if}
                {:else}
                  <DeviceLogin
                    role="bot"
                    label="Connect the bot account"
                    ondone={(_, warning) => {
                      botWarning = warning;
                      refreshStatus();
                    }}
                  />
                  <p class="help">Sign in to Twitch as the bot when the activation page opens.</p>
                {/if}
              {/if}
            </div>
          {/if}
        {:else if step === 2}
          <h1>Pick an AI</h1>
          <p class="lead">
            The AI scores each message from 0 to 10 against your rules. Motwi tests the key before moving on.
          </p>
          <ProviderForm slot="primary" bind:config />
          <div class="optional">
            <button class="more" aria-expanded={showBackup} onclick={() => (showBackup = !showBackup)}>
              <Icon name={showBackup ? "caret-down" : "caret-right"} size={12} />Add a backup provider (optional)
            </button>
            {#if showBackup}
              <p class="help">If the main AI stops answering, Motwi switches to this one automatically.</p>
              <ProviderForm slot="backup" bind:config />
            {/if}
          </div>
        {:else if step === 3}
          <h1>Your rules</h1>
          <p class="lead">
            These are a starting point. Write rules the way you would tell a mod. The chip is the strongest thing
            each rule may do.
          </p>
          <div class="grid2">
            <RuleList bind:rules scope="community" placeholder='Add a rule, like "no political arguments"' />
            <div class="side">
              <Strictness bind:settings />
              <div class="card">
                <div class="toggle-row">
                  <div>
                    <p class="label" id="suggest-label">Start in suggest-only mode</p>
                    <p class="help">
                      Motwi shows what it would do and waits for you. Good for the first stream. You can switch any time.
                    </p>
                  </div>
                  <Toggle checked={settings.suggest_only} label="Suggest-only mode" onchange={setSuggest} />
                </div>
              </div>
            </div>
          </div>
        {:else if step === 4}
          <h1>Motwi inside Twitch chat</h1>
          <p class="lead">
            The browser extension puts Motwi's decisions, undo buttons and ban requests right in your Twitch chat.
          </p>
          <ExtensionPanel bind:info={extension} />
          <p class="help">You can skip this and connect it later in Settings. Everything also works from this window.</p>
        {:else}
          <h1>Ready</h1>
          <p class="lead">Here's what Motwi will do once you start.</p>
          <dl class="summary">
            <div><dt>Channel</dt><dd>{streamer?.login}{bot ? `, acting as ${bot.login}` : ""}</dd></div>
            <div>
              <dt>AI</dt>
              <dd>
                {config.primary ? `${PROVIDER_LABEL[config.primary.provider]} ${modelLabel(config.primary.model)}` : "None"}
                {config.backup ? `, backup ${PROVIDER_LABEL[config.backup.provider]}` : ""}
              </dd>
            </div>
            <div><dt>Rules</dt><dd>{rules.filter((r) => r.scope === "community").length} community rules, {settings.strictness}</dd></div>
            <div><dt>Mode</dt><dd>{settings.suggest_only ? "Suggest only: nothing happens without you" : "Acts on its own, asks before bans"}</dd></div>
          </dl>
          {#if finishError}<p class="err" role="alert">{finishError}</p>{/if}
        {/if}
      </div>

      <footer class="foot">
        {#if step > 0}<button class="btn ghost" onclick={() => step--}>Back</button>{/if}
        <span class="dim blocker" aria-live="polite">{blocker ?? ""}</span>
        {#if step === 4}
          {#if extension?.clients.length}
            <button class="btn pri" onclick={() => step++}>Continue<Icon name="arrow-right" size={14} /></button>
          {:else}
            <button class="btn" onclick={() => step++}>Skip for now</button>
          {/if}
        {:else if step === STEPS.length - 1}
          <button class="btn pri" onclick={finish} disabled={finishing}>
            {#if finishing}<Icon name="circle-notch" size={14} spin />{/if}Start watching
          </button>
        {:else}
          <button class="btn pri" onclick={() => step++} disabled={!canNext}>
            {step === 0 ? "Get started" : "Continue"}<Icon name="arrow-right" size={14} />
          </button>
        {/if}
      </footer>
    {/if}
  </main>
</div>

<style>
  .setup {
    display: grid;
    grid-template-columns: 240px 1fr;
    height: 100%;
  }
  .steps {
    background: var(--rail);
    border-right: 1px solid var(--line);
    padding: 18px 14px;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 0 6px 22px;
  }
  .logo {
    width: 26px;
    height: 26px;
    border-radius: 7px;
    background: var(--motwi);
    color: var(--motwi-ink);
    font-weight: 700;
    display: grid;
    place-items: center;
  }
  ol {
    list-style: none;
    padding: 0;
    display: grid;
    gap: 2px;
  }
  li {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 8px;
    border-radius: var(--r);
    color: var(--dim);
    font-size: 13.5px;
    font-weight: 500;
  }
  li[aria-current="step"] {
    background: var(--panel-2);
    color: var(--text);
  }
  li.done {
    color: var(--muted);
  }
  .n {
    width: 20px;
    height: 20px;
    border-radius: 50%;
    border: 1px solid var(--line-2);
    display: grid;
    place-items: center;
    font-size: 11px;
  }
  li[aria-current="step"] .n {
    border-color: var(--motwi);
    color: var(--motwi);
  }
  li.done .n {
    background: var(--motwi-soft);
    border-color: var(--motwi-soft);
    color: var(--motwi);
  }
  .content {
    display: flex;
    flex-direction: column;
    min-width: 0;
    height: 100%;
    padding: 40px 48px 0;
  }
  .body {
    flex: 1;
    overflow-y: auto;
    max-width: 860px;
    display: flex;
    flex-direction: column;
    gap: 18px;
    padding-bottom: 24px;
  }
  h1 {
    font-size: 24px;
    font-weight: 600;
    letter-spacing: -0.015em;
    max-width: 30ch;
  }
  .lead {
    color: var(--muted);
    max-width: 62ch;
  }
  .facts {
    list-style: none;
    padding: 0;
    display: grid;
    gap: 12px;
    margin-top: 6px;
  }
  .facts li {
    padding: 0;
    color: var(--text);
    font-size: 14px;
    align-items: flex-start;
  }
  .facts :global(.ic) {
    color: var(--motwi);
    margin-top: 1px;
  }
  .connected {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    border-radius: var(--r);
    background: var(--motwi-soft);
    color: var(--text);
    width: fit-content;
  }
  .connected :global(.ic) {
    color: var(--motwi);
  }
  .optional {
    display: grid;
    gap: 10px;
    border-top: 1px solid var(--line);
    padding-top: 16px;
  }
  .more {
    display: inline-flex;
    gap: 6px;
    align-items: center;
    background: none;
    border: 0;
    padding: 0;
    color: var(--muted);
    font-weight: 600;
    font-size: 13px;
    cursor: pointer;
    width: fit-content;
  }
  .more:hover {
    color: var(--text);
  }
  .grid2 {
    display: grid;
    grid-template-columns: 1fr 320px;
    gap: 18px;
    align-items: start;
  }
  .side {
    display: grid;
    gap: 14px;
  }
  .toggle-row {
    display: flex;
    gap: 14px;
    align-items: flex-start;
    justify-content: space-between;
  }
  .summary {
    display: grid;
    gap: 0;
    border: 1px solid var(--line);
    border-radius: var(--r);
    background: var(--panel);
    max-width: 560px;
  }
  .summary div {
    display: grid;
    grid-template-columns: 110px 1fr;
    padding: 10px 14px;
  }
  .summary div + div {
    border-top: 1px solid var(--line);
  }
  dt {
    color: var(--dim);
    font-size: 13px;
  }
  dd {
    margin: 0;
    font-size: 13.5px;
  }
  .foot {
    display: flex;
    gap: 10px;
    align-items: center;
    padding: 14px 0 18px;
    border-top: 1px solid var(--line);
    max-width: 860px;
  }
  .blocker {
    margin-left: auto;
    font-size: 12.5px;
  }
  @media (max-width: 1100px) {
    .grid2 {
      grid-template-columns: 1fr;
    }
  }
</style>
