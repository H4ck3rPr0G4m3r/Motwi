<script lang="ts">
  import { onMount } from "svelte";
  import Icon from "../components/Icon.svelte";
  import LoadError from "../components/LoadError.svelte";
  import RuleList from "../components/RuleList.svelte";
  import SkeletonRows from "../components/SkeletonRows.svelte";
  import Strictness from "../components/Strictness.svelte";
  import { del, get, message, post } from "../lib/api";
  import { core } from "../lib/events.svelte";
  import { CEILING_LABEL, KIND_COLOR, score, wouldDo } from "../lib/format";
  import type { BlockedWord, Ceiling, Rule, Settings, TryResult } from "../lib/types";

  let rules = $state<Rule[] | null>(null);
  let words = $state<BlockedWord[]>([]);
  let settings = $state<Settings | null>(null);
  let error = $state<string | null>(null);

  let phrase = $state("");
  let wordCeiling = $state<Ceiling>("timeout");
  let hidden = $state(false);
  let showHidden = $state(false);
  let wordError = $state<string | null>(null);

  let tryText = $state("");
  let trying = $state(false);
  let result = $state<TryResult | null>(null);
  let tryError = $state<string | null>(null);

  const stream = $derived(core.status?.stream);
  const visible = $derived(words.filter((w) => !w.hidden || showHidden));
  const hiddenCount = $derived(words.filter((w) => w.hidden).length);

  async function load() {
    error = null;
    try {
      [rules, words, settings] = await Promise.all([
        get<Rule[]>("/rules"),
        get<BlockedWord[]>("/blocked"),
        get<Settings>("/settings"),
      ]);
    } catch (e) {
      error = message(e);
    }
  }
  onMount(load);

  async function addWord(e: SubmitEvent) {
    e.preventDefault();
    wordError = null;
    try {
      const w = await post<BlockedWord>("/blocked", { phrase, ceiling: wordCeiling, hidden });
      words = [...words, w];
      phrase = "";
      hidden = false;
    } catch (err) {
      wordError = message(err);
    }
  }

  async function removeWord(w: BlockedWord) {
    const before = words;
    words = words.filter((x) => x.id !== w.id);
    try {
      await del(`/blocked/${w.id}`);
    } catch (err) {
      words = before;
      wordError = message(err);
    }
  }

  async function tryIt(e: SubmitEvent) {
    e.preventDefault();
    trying = true;
    tryError = null;
    result = null;
    try {
      result = await post<TryResult>("/try", { text: tryText });
      if (result.error) {
        tryError = result.error;
        result = null;
      }
    } catch (err) {
      tryError = message(err);
    } finally {
      trying = false;
    }
  }
</script>

<div class="page">
  <div class="pagehead">
    <div>
      <h1>Rules</h1>
      <p>Motwi reads these on every message. Write them the way you would tell a mod.</p>
    </div>
  </div>

  {#if error}
    <LoadError {error} retry={load} />
  {:else if !rules || !settings}
    <div class="layout">
      <SkeletonRows rows={5} />
      <div class="skel" style="height:220px"></div>
    </div>
  {:else}
    <div class="layout">
      <div class="stack">
        <section class="sec">
          <h2>Always <span>community rules</span></h2>
          <RuleList bind:rules scope="community" placeholder='Add a rule, like "no political arguments"' />
        </section>

        <section class="sec">
          <h2>This stream only <span>cleared when the stream ends</span></h2>
          <div class="ctx">
            <Icon name="broadcast" size={15} />
            {#if stream?.game || stream?.title}
              <span>From Twitch: <b>{stream.game ?? "No category"}</b>{stream.title ? `, title "${stream.title}"` : ""}</span>
            {:else}
              <span>Motwi reads your game and title from Twitch and gives them to the AI.</span>
            {/if}
          </div>
          <RuleList
            bind:rules
            scope="stream"
            placeholder="Add a rule for today"
            primary={false}
          />
        </section>

        <section class="sec">
          <h2>Blocked words <span>instant, no AI</span></h2>
          <div class="panel">
            <div class="words">
              {#each visible as w (w.id)}
                <span class="chip word">
                  <span class="sw" style:background={KIND_COLOR[w.ceiling]} title={CEILING_LABEL[w.ceiling]}></span>
                  {w.phrase}
                  <button class="x" aria-label="Remove blocked word {w.hidden ? '(hidden)' : w.phrase}" onclick={() => removeWord(w)}>
                    <Icon name="x" size={12} />
                  </button>
                </span>
              {/each}
              {#if hiddenCount && !showHidden}
                <button class="chip ghost" onclick={() => (showHidden = true)}>
                  <Icon name="eye-slash" size={13} />{hiddenCount} hidden
                </button>
              {:else if hiddenCount}
                <button class="chip ghost" onclick={() => (showHidden = false)}>
                  <Icon name="eye" size={13} />Hide them again
                </button>
              {/if}
              {#if !words.length}
                <span class="dim small">No blocked words. Spacing, repeated letters and leetspeak are caught too.</span>
              {/if}
            </div>
            <form class="add" onsubmit={addWord}>
              <label class="sr-only" for="word">Word or phrase to block</label>
              <input id="word" class="input" bind:value={phrase} placeholder="Word or phrase" maxlength="100" />
              <label class="sr-only" for="word-ceiling">Strongest action</label>
              <select id="word-ceiling" class="input" bind:value={wordCeiling}>
                {#each ["delete", "warn", "timeout", "ban"] as c (c)}<option value={c}>{CEILING_LABEL[c as Ceiling]}</option>{/each}
              </select>
              <label class="check">
                <input type="checkbox" bind:checked={hidden} />Hide it
              </label>
              <button class="btn"><Icon name="plus" size={14} />Add</button>
            </form>
            {#if wordError}<p class="err pad" role="alert">{wordError}</p>{/if}
          </div>
          <p class="help">Hidden words, such as slurs, only show as a count until you reveal them.</p>
        </section>
      </div>

      <div class="side">
        <Strictness bind:settings />
        <form class="card" onsubmit={tryIt}>
          <h2><label for="try">Try a message</label></h2>
          <textarea
            id="try"
            class="input"
            rows="3"
            bind:value={tryText}
            maxlength="500"
            placeholder="does the guy with the mask betray you later"
          ></textarea>
          <p class="help">Scored with your real rules and model. Nothing happens in chat.</p>
          <button class="btn" style="margin-top:10px" disabled={trying || !tryText.trim()}>
            {#if trying}<Icon name="circle-notch" size={14} spin />{:else}<Icon name="lightning" size={14} />{/if}
            Score it
          </button>
          <div aria-live="polite">
            {#if tryError}
              <p class="err" role="alert">{tryError}</p>
            {:else if result}
              <div class="result">
                {#if result.score !== null && result.score !== undefined}
                  <div class="row"><span>Score</span><span class="mono">{score(result.score)}</span></div>
                {/if}
                <div class="row">
                  <span>Would do</span>
                  <span class="would">
                    {#if result.action?.kind !== "none"}<span class="sw" style:background={KIND_COLOR[result.action?.kind ?? "delete"]}></span>{/if}
                    {wouldDo(result.action?.kind ?? "none", result.action?.duration_s ?? null)}
                  </span>
                </div>
                {#if result.rule}<div class="row"><span>Rule</span><span class="rt">{result.rule.text}</span></div>{/if}
                <p class="sm">{result.reason}</p>
                {#if result.action?.kind !== "none" && result.source === "ai"}
                  <p class="dim tiny">For a first offense. Repeat offenders step up the ladder.</p>
                {/if}
              </div>
            {/if}
          </div>
        </form>
      </div>
    </div>
  {/if}
</div>

<style>
  .layout {
    display: grid;
    grid-template-columns: 1fr 340px;
    gap: 22px;
    align-items: start;
  }
  .stack {
    display: flex;
    flex-direction: column;
    gap: 22px;
    min-width: 0;
  }
  .side {
    display: flex;
    flex-direction: column;
    gap: 22px;
    position: sticky;
    top: 0;
  }
  .ctx {
    display: flex;
    gap: 10px;
    align-items: center;
    font-size: 12px;
    color: var(--muted);
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: var(--r);
    padding: 8px 12px;
    margin-bottom: 8px;
  }
  .ctx b {
    color: var(--text);
    font-weight: 600;
  }
  .words {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    padding: 10px 12px;
    border-bottom: 1px solid var(--line);
    align-items: center;
  }
  .word {
    background: #22222a;
    padding-right: 4px;
  }
  .x {
    background: none;
    border: 0;
    color: var(--dim);
    display: inline-flex;
    padding: 2px;
    border-radius: 3px;
    cursor: pointer;
  }
  .x:hover {
    color: var(--text);
    background: var(--panel-3);
  }
  .chip.ghost {
    background: none;
    border: 1px dashed var(--line-2);
    cursor: pointer;
  }
  .chip.ghost:hover {
    color: var(--text);
  }
  .add {
    display: grid;
    grid-template-columns: 1fr 130px auto auto;
    gap: 8px;
    padding: 10px 12px;
    align-items: center;
  }
  .check {
    display: flex;
    gap: 6px;
    align-items: center;
    font-size: 12.5px;
    color: var(--muted);
    white-space: nowrap;
  }
  .check input {
    accent-color: var(--motwi);
  }
  .pad {
    padding: 0 12px 10px;
    margin: 0;
  }
  .small {
    font-size: 12.5px;
  }
  .result {
    margin-top: 12px;
    border-top: 1px solid var(--line);
    padding-top: 10px;
    font-size: 12.5px;
    display: grid;
    gap: 6px;
  }
  .row {
    display: flex;
    justify-content: space-between;
    gap: 12px;
  }
  .row > span:first-child {
    color: var(--muted);
  }
  .would {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-weight: 600;
  }
  .rt {
    text-align: right;
  }
  .sm {
    font-size: 12px;
    color: var(--muted);
  }
  .tiny {
    font-size: 11.5px;
  }
  @media (max-width: 1100px) {
    .layout {
      grid-template-columns: 1fr;
    }
    .side {
      position: static;
    }
  }
</style>
