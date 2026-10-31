<script lang="ts">
  import { del, message, patch, post } from "../lib/api";
  import { CEILING_GROUPS, CEILING_HELP, CEILING_LABEL, KIND_COLOR } from "../lib/format";
  import type { Ceiling, Rule } from "../lib/types";
  import Empty from "./Empty.svelte";
  import Icon from "./Icon.svelte";

  let {
    rules = $bindable(),
    scope,
    placeholder,
    addLabel = "Add",
    primary = true,
  }: {
    rules: Rule[];
    scope: "community" | "stream";
    placeholder: string;
    addLabel?: string;
    primary?: boolean;
  } = $props();

  const mine = $derived(rules.filter((r) => r.scope === scope));

  let text = $state("");
  let ceiling = $state<Ceiling>("timeout");
  let adding = $state(false);
  let error = $state<string | null>(null);
  let rowError = $state<Record<string, string>>({});
  let editing = $state<string | null>(null);
  let draft = $state("");

  const inputId = $derived(`rule-${scope}`);

  async function add(e: SubmitEvent) {
    e.preventDefault();
    if (!text.trim()) {
      error = "Write the rule first.";
      return;
    }
    adding = true;
    error = null;
    try {
      const rule = await post<Rule>("/rules", { text, ceiling, scope });
      rules = [...rules, rule];
      text = "";
    } catch (err) {
      error = message(err);
    } finally {
      adding = false;
    }
  }

  async function update(rule: Rule, changes: Partial<Rule>) {
    delete rowError[rule.id];
    const before = rules;
    rules = rules.map((r) => (r.id === rule.id ? { ...r, ...changes } : r));
    try {
      const saved = await patch<Rule>(`/rules/${rule.id}`, changes);
      rules = rules.map((r) => (r.id === rule.id ? saved : r));
    } catch (err) {
      rules = before;
      rowError[rule.id] = message(err);
    }
  }

  async function remove(rule: Rule) {
    const before = rules;
    rules = rules.filter((r) => r.id !== rule.id);
    try {
      await del(`/rules/${rule.id}`);
    } catch (err) {
      rules = before;
      rowError[rule.id] = message(err);
    }
  }

  function startEdit(rule: Rule) {
    editing = rule.id;
    draft = rule.text;
  }

  async function commitEdit(rule: Rule) {
    editing = null;
    if (draft.trim() && draft.trim() !== rule.text) await update(rule, { text: draft.trim() });
  }
</script>

<div class="panel">
  {#if mine.length === 0}
    <Empty icon="scroll" title={scope === "stream" ? "No rules for this stream" : "No rules yet"}>
      {scope === "stream"
        ? "Add one for today, like no spoilers. They clear when the stream ends."
        : "Add the rules you would tell a mod. Motwi judges every message against them."}
    </Empty>
  {/if}
  {#each mine as rule (rule.id)}
    <div class="rule">
      {#if editing === rule.id}
        <input
          class="input"
          aria-label="Rule text"
          bind:value={draft}
          onblur={() => commitEdit(rule)}
          onkeydown={(e) => {
            if (e.key === "Enter") commitEdit(rule);
            if (e.key === "Escape") editing = null;
          }}
          {@attach (el) => el.focus()}
        />
      {:else}
        <button class="txt" onclick={() => startEdit(rule)} title="Edit this rule">{rule.text}</button>
      {/if}
      <label class="ceil">
        <span class="sr-only">Action for "{rule.text}"</span>
        <span class="sw" style:background={KIND_COLOR[rule.ceiling]}></span>
        <select
          value={rule.ceiling}
          title={CEILING_HELP[rule.ceiling]}
          onchange={(e) => update(rule, { ceiling: e.currentTarget.value as Ceiling })}
        >
          {#each CEILING_GROUPS as group (group.label)}
            <optgroup label={group.label}>
              {#each group.options as c (c)}<option value={c}>{CEILING_LABEL[c]}</option>{/each}
            </optgroup>
          {/each}
        </select>
        <Icon name="caret-down" size={11} />
      </label>
      <button class="btn icon" aria-label="Remove rule: {rule.text}" onclick={() => remove(rule)}>
        <Icon name="x" size={15} />
      </button>
      {#if rowError[rule.id]}<p class="err full" role="alert">{rowError[rule.id]}</p>{/if}
    </div>
  {/each}
  <form class="add" onsubmit={add}>
    <label class="sr-only" for={inputId}>New {scope === "stream" ? "stream" : "community"} rule</label>
    <input
      id={inputId}
      class="input"
      {placeholder}
      bind:value={text}
      maxlength="300"
      aria-invalid={error ? "true" : undefined}
    />
    <label class="sr-only" for="{inputId}-ceiling">Action</label>
    <select id="{inputId}-ceiling" class="input pick" bind:value={ceiling}>
      {#each CEILING_GROUPS as group (group.label)}
        <optgroup label={group.label}>
          {#each group.options as c (c)}<option value={c}>{CEILING_LABEL[c]}</option>{/each}
        </optgroup>
      {/each}
    </select>
    <button class="btn" class:pri={primary} disabled={adding}>
      <Icon name="plus" size={14} />{addLabel}
    </button>
    <p class="help hint">{CEILING_HELP[ceiling]}</p>
  </form>
  {#if error}<p class="err pad" role="alert">{error}</p>{/if}
</div>

<style>
  .rule {
    display: grid;
    grid-template-columns: 1fr auto auto;
    align-items: center;
    gap: 10px;
    padding: 8px 8px 8px 12px;
    border-bottom: 1px solid var(--line);
  }
  .txt {
    font-size: 13.5px;
    text-align: left;
    background: none;
    border: 0;
    padding: 4px 0;
    cursor: text;
    color: var(--text);
  }
  .ceil {
    position: relative;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: var(--panel-3);
    border-radius: var(--r-sm);
    padding: 0 8px;
    height: 24px;
    color: var(--muted);
  }
  .ceil select {
    appearance: none;
    background: none;
    border: 0;
    font-size: 11.5px;
    font-weight: 600;
    color: var(--muted);
    padding-right: 2px;
    cursor: pointer;
  }
  .ceil select:focus {
    outline: none;
  }
  .ceil:focus-within {
    outline: 2px solid var(--motwi);
    outline-offset: 1px;
  }
  .ceil select option,
  .ceil select optgroup {
    background: var(--panel-2);
    color: var(--text);
  }
  .add {
    display: grid;
    grid-template-columns: 1fr 140px auto;
    gap: 8px;
    padding: 10px 12px;
  }
  .hint {
    grid-column: 1 / -1;
    margin: 0;
  }
  .full {
    grid-column: 1 / -1;
    margin-top: 0;
  }
  .pad {
    padding: 0 12px 10px;
    margin: 0;
  }
</style>
