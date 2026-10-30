<script lang="ts">
  import { onMount } from "svelte";
  import ActionTag from "../components/ActionTag.svelte";
  import Empty from "../components/Empty.svelte";
  import Icon from "../components/Icon.svelte";
  import LoadError from "../components/LoadError.svelte";
  import SaveState from "../components/SaveState.svelte";
  import SkeletonRows from "../components/SkeletonRows.svelte";
  import { ApiError, del, get, message, patch, post } from "../lib/api";
  import { ago } from "../lib/format";
  import { go } from "../lib/router.svelte";
  import { createSaver } from "../lib/save.svelte";
  import type { Banned, People } from "../lib/types";

  let people = $state<People | null>(null);
  let error = $state<string | null>(null);
  let banned = $state<Banned[] | null>(null);
  let bannedError = $state<{ text: string; offline: boolean } | null>(null);
  let unbanning = $state<string | null>(null);
  let leeway = $state(1);
  const saver = createSaver();

  let newLogin = $state<Record<string, string>>({ trusted: "", vip: "" });
  let listError = $state<Record<string, string | null>>({ trusted: null, vip: null });

  async function load() {
    error = null;
    try {
      people = await get<People>("/people");
      leeway = people.vip_leeway;
    } catch (e) {
      error = message(e);
    }
  }

  async function loadBanned() {
    bannedError = null;
    banned = null;
    try {
      banned = await get<Banned[]>("/banned");
    } catch (e) {
      bannedError = { text: message(e), offline: e instanceof ApiError && e.status === 409 };
    }
  }

  onMount(() => {
    load();
    loadBanned();
  });

  async function add(kind: "trusted" | "vip", e: SubmitEvent) {
    e.preventDefault();
    listError[kind] = null;
    try {
      people = await post<People>("/people", { kind, login: newLogin[kind] });
      newLogin[kind] = "";
    } catch (err) {
      listError[kind] = message(err);
    }
  }

  async function remove(kind: "trusted" | "vip", login: string) {
    try {
      people = await del<People>(`/people/${kind}/${encodeURIComponent(login)}`);
    } catch (err) {
      listError[kind] = message(err);
    }
  }

  async function trust(login: string) {
    try {
      people = await post<People>("/people", { kind: "trusted", login });
    } catch (err) {
      listError.trusted = message(err);
    }
  }

  async function saveLeeway() {
    if (!people || leeway === people.vip_leeway) return;
    const r = await saver.save("vip_leeway", () => patch("/settings", { vip_leeway: Number(leeway) }));
    if (r && people) people.vip_leeway = Number(leeway);
  }

  async function unban(b: Banned) {
    unbanning = b.user_id;
    try {
      await post("/unban", { user_id: b.user_id });
      banned = banned?.filter((x) => x.user_id !== b.user_id) ?? null;
    } catch (e) {
      bannedError = { text: message(e), offline: false };
    } finally {
      unbanning = null;
    }
  }

  function until(b: Banned): string {
    if (!b.expires_at) return "Banned";
    const left = (Date.parse(b.expires_at) - Date.now()) / 1000;
    if (left <= 0) return "Timeout ending";
    return left < 3600 ? `Timed out, ${Math.ceil(left / 60)}m left` : `Timed out, ${Math.ceil(left / 3600)}h left`;
  }
</script>

{#snippet list(kind: "trusted" | "vip", title: string, sub: string, help: string)}
  <section class="sec">
    <h2>{title} <span>{sub}</span></h2>
    <div class="panel">
      <div class="names">
        {#each people?.[kind] ?? [] as login (login)}
          <span class="chip name">{login}
            <button class="x" aria-label="Remove {login} from {title}" onclick={() => remove(kind, login)}><Icon name="x" size={12} /></button>
          </span>
        {:else}
          <span class="dim small">{help}</span>
        {/each}
      </div>
      <form class="add" onsubmit={(e) => add(kind, e)}>
        <label class="sr-only" for="add-{kind}">Twitch username to add to {title}</label>
        <input id="add-{kind}" class="input" placeholder="Twitch username" bind:value={newLogin[kind]} maxlength="26" />
        <button class="btn"><Icon name="user-plus" size={14} />Add</button>
      </form>
      {#if listError[kind]}<p class="err pad" role="alert">{listError[kind]}</p>{/if}
    </div>
  </section>
{/snippet}

<div class="page">
  <div class="pagehead">
    <div>
      <h1>People</h1>
      <p>Who Motwi leaves alone, who gets leeway, and who it has dealt with.</p>
    </div>
  </div>

  {#if error}
    <LoadError {error} retry={load} />
  {:else if !people}
    <SkeletonRows rows={4} />
  {:else}
    <div class="cols">
      <div class="stack">
        {@render list("trusted", "Trusted", "never touched", "Nobody yet. Your mods and you are always exempt anyway.")}
        {@render list("vip", "VIP", "more leeway", "Twitch VIPs get leeway automatically. Add others here.")}
        <div class="card leeway">
          <div>
            <label class="label" for="leeway">VIP leeway</label>
            <p class="help">Added to every threshold for VIPs, so it takes a worse message to act. They aren't exempt.</p>
          </div>
          <div class="lv">
            <input
              id="leeway"
              class="input mono"
              type="number"
              min="0"
              max="5"
              step="0.5"
              bind:value={leeway}
              onblur={saveLeeway}
              onkeydown={(e) => e.key === "Enter" && saveLeeway()}
            />
            <SaveState state={saver.fields.vip_leeway} />
          </div>
        </div>
      </div>

      <div class="stack">
        <section class="sec">
          <h2>Banned and timed out <span>right now, from Twitch</span></h2>
          <div class="panel rows">
            {#if bannedError}
              {#if bannedError.offline}
                <Empty icon="twitch-logo" title="Not connected to Twitch">
                  Motwi reads this list from Twitch while it's connected.
                </Empty>
              {:else}
                <div style="padding:10px"><LoadError error={bannedError.text} retry={loadBanned} /></div>
              {/if}
            {:else if !banned}
              <div style="padding:12px"><div class="skel" style="height:18px;width:60%"></div></div>
            {:else}
              {#each banned as b (b.user_id)}
                <div class="person">
                  <div>
                    <p><b>{b.login}</b> <span class="dim small">{until(b)}</span></p>
                    {#if b.reason}<p class="muted small">{b.reason}</p>{/if}
                  </div>
                  <button class="btn sm" disabled={unbanning === b.user_id} onclick={() => unban(b)}>
                    {#if unbanning === b.user_id}<Icon name="circle-notch" size={13} spin />{/if}Unban
                  </button>
                </div>
              {:else}
                <Empty icon="check-circle" title="Nobody is banned or timed out" />
              {/each}
            {/if}
          </div>
        </section>

        <section class="sec">
          <h2>Recently actioned</h2>
          <div class="panel rows">
            {#each people.recent as p (p.user_id)}
              <div class="person">
                <div class="grow">
                  <button class="who" onclick={() => go("history", { user: p.login })}>{p.login}</button>
                  <p class="dim small">{p.actions} {p.actions === 1 ? "action" : "actions"}, last {ago(p.last_at)}</p>
                </div>
                <ActionTag kind={p.last_kind} />
                {#if !people.trusted.includes(p.login)}
                  <button class="btn sm ghost" onclick={() => trust(p.login)}>Trust</button>
                {/if}
              </div>
            {:else}
              <Empty icon="users" title="Nobody yet">Chatters Motwi acts on show up here.</Empty>
            {/each}
          </div>
        </section>
      </div>
    </div>
  {/if}
</div>

<style>
  .cols {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 22px;
    align-items: start;
  }
  .stack {
    display: flex;
    flex-direction: column;
    gap: 22px;
    min-width: 0;
  }
  .names {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    padding: 10px 12px;
    border-bottom: 1px solid var(--line);
    min-height: 44px;
    align-items: center;
  }
  .name {
    background: #22222a;
    color: var(--text);
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
  .add {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 8px;
    padding: 10px 12px;
  }
  .pad {
    padding: 0 12px 10px;
    margin: 0;
  }
  .small {
    font-size: 12.5px;
  }
  .leeway {
    display: flex;
    gap: 16px;
    justify-content: space-between;
    align-items: flex-start;
  }
  .lv {
    width: 96px;
    flex: none;
    display: grid;
    gap: 4px;
  }
  .person {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 12px;
    justify-content: space-between;
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  .who {
    background: none;
    border: 0;
    padding: 0;
    font-weight: 600;
    color: var(--text);
    cursor: pointer;
  }
  .who:hover {
    color: var(--motwi);
  }
  @media (max-width: 1100px) {
    .cols {
      grid-template-columns: 1fr;
    }
  }
</style>
