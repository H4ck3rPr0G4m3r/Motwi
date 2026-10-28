<script lang="ts">
  import { untrack } from "svelte";
  import { ApiError, del, message, post, put } from "../lib/api";
  import type { AIConfig, ProviderView, Slot } from "../lib/types";
  import Icon from "./Icon.svelte";

  let {
    slot,
    config = $bindable(),
    onsaved,
  }: { slot: Slot; config: AIConfig; onsaved?: (c: AIConfig) => void } = $props();

  type Name = ProviderView["provider"];
  const current = $derived(config[slot]);

  // the form starts from what is saved, then belongs to the user until saved again
  const initial = untrack(() => config[slot]);
  let provider = $state<Name>(initial?.provider ?? "gemini");
  let model = $state(initial?.model ?? untrack(() => config.providers.gemini?.default_model) ?? "");
  let baseUrl = $state(initial?.base_url ?? "");
  let key = $state("");
  let showKey = $state(false);
  let busy = $state(false);
  let result = $state<{ ok: boolean; text: string } | null>(null);
  let fieldError = $state<Record<string, string>>({});

  const KEY_LINKS: Record<string, { href: string; text: string }> = {
    gemini: { href: "https://aistudio.google.com/apikey", text: "Get a Gemini key" },
    openrouter: { href: "https://openrouter.ai/keys", text: "Get an OpenRouter key" },
  };
  const id = (f: string) => `${slot}-${f}`;

  function choose(p: Name) {
    const wasDefault = !model || Object.values(config.providers).some((v) => v.default_model === model);
    provider = p;
    if (wasDefault) model = config.providers[p]?.default_model ?? "";
    result = null;
  }

  async function testAndSave() {
    busy = true;
    result = null;
    fieldError = {};
    const body = { provider, model, base_url: baseUrl, key: key || undefined };
    try {
      const test = await post<{ ok: boolean; detail?: string; latency_ms?: number }>("/ai/test", {
        slot,
        ...body,
      });
      if (!test.ok) {
        result = { ok: false, text: test.detail ?? "The provider didn't answer." };
        return;
      }
      config = await put<AIConfig>(`/ai/${slot}`, body);
      key = "";
      result = { ok: true, text: `Works. It answered in ${((test.latency_ms ?? 0) / 1000).toFixed(1)}s.` };
      onsaved?.(config);
    } catch (e) {
      if (e instanceof ApiError && e.field) fieldError[e.field] = e.message;
      else result = { ok: false, text: message(e) };
    } finally {
      busy = false;
    }
  }

  async function removeBackup() {
    busy = true;
    try {
      config = await del<AIConfig>("/ai/backup");
      onsaved?.(config);
    } catch (e) {
      result = { ok: false, text: message(e) };
    } finally {
      busy = false;
    }
  }
</script>

<div class="form">
  <div role="radiogroup" aria-label="AI provider" class="providers">
    {#each [["gemini", "Gemini", "Google. Cheap and fast."], ["openrouter", "OpenRouter", "Many models, one key."], ["custom", "Custom", "Any OpenAI-compatible URL."]] as [value, label, help] (value)}
      <button
        type="button"
        role="radio"
        aria-checked={provider === value}
        class="prov"
        onclick={() => choose(value as Name)}
      >
        <b>{label}</b><span>{help}</span>
      </button>
    {/each}
  </div>

  {#if provider === "custom"}
    <div>
      <label class="label" for={id("url")}>Base URL</label>
      <input
        id={id("url")}
        class="input"
        bind:value={baseUrl}
        placeholder="https://example.com/v1"
        aria-invalid={fieldError.base_url ? "true" : undefined}
      />
      {#if fieldError.base_url}<p class="err">{fieldError.base_url}</p>{/if}
    </div>
  {/if}

  <div>
    <label class="label" for={id("model")}>Model</label>
    <input
      id={id("model")}
      class="input mono"
      bind:value={model}
      aria-invalid={fieldError.model ? "true" : undefined}
    />
    {#if fieldError.model}<p class="err">{fieldError.model}</p>{:else}<p class="help">
        The default is a fast, cheap model that's good at this.
      </p>{/if}
  </div>

  <div>
    <label class="label" for={id("key")}>API key</label>
    <div class="keyrow">
      <input
        id={id("key")}
        class="input mono"
        type={showKey ? "text" : "password"}
        autocomplete="off"
        spellcheck="false"
        bind:value={key}
        placeholder={current?.has_key && current.provider === provider ? "Saved. Paste a new one to replace it." : ""}
        aria-invalid={fieldError.key ? "true" : undefined}
      />
      <button
        type="button"
        class="btn icon"
        aria-label={showKey ? "Hide key" : "Show key"}
        onclick={() => (showKey = !showKey)}
      >
        <Icon name={showKey ? "eye-slash" : "eye"} size={16} />
      </button>
    </div>
    {#if fieldError.key}<p class="err">{fieldError.key}</p>{:else}<p class="help">
        Stored in Windows Credential Manager, never in a file.
        {#if KEY_LINKS[provider]}<a href={KEY_LINKS[provider].href} target="_blank" rel="noreferrer"
            >{KEY_LINKS[provider].text}</a
          >{/if}
      </p>{/if}
    {#if current?.key_from_env}<p class="help">The MOTWI_AI_KEY environment variable overrides this key.</p>{/if}
  </div>

  <div class="actions">
    <button class="btn pri" onclick={testAndSave} disabled={busy}>
      {#if busy}<Icon name="circle-notch" size={14} spin />{:else}<Icon name="lightning" size={14} />{/if}
      Test and save
    </button>
    {#if slot === "backup" && current}
      <button class="btn ghost" onclick={removeBackup} disabled={busy}>Remove backup</button>
    {/if}
    {#if result}
      <span class="result" class:bad={!result.ok} role={result.ok ? "status" : "alert"}>
        <Icon name={result.ok ? "check-circle" : "warning-circle"} size={15} />{result.text}
      </span>
    {/if}
  </div>
</div>

<style>
  .form {
    display: grid;
    gap: 14px;
  }
  .providers {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
  }
  .prov {
    text-align: left;
    background: var(--panel-2);
    border: 1px solid var(--line);
    border-radius: var(--r);
    padding: 10px 12px;
    display: grid;
    gap: 2px;
    cursor: pointer;
  }
  .prov b {
    font-size: 13px;
  }
  .prov span {
    font-size: 12px;
    color: var(--dim);
  }
  .prov:hover {
    border-color: #3a3a42;
  }
  .prov[aria-checked="true"] {
    border-color: var(--motwi);
    background: var(--motwi-soft);
  }
  .keyrow {
    display: flex;
    gap: 6px;
    align-items: center;
  }
  .keyrow .btn.icon {
    width: 32px;
    height: 32px;
  }
  .actions {
    display: flex;
    gap: 10px;
    align-items: center;
    flex-wrap: wrap;
  }
  .result {
    display: inline-flex;
    gap: 6px;
    align-items: center;
    font-size: 12.5px;
    color: var(--motwi);
  }
  .result.bad {
    color: var(--error);
  }
</style>
