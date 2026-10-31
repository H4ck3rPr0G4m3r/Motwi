<script lang="ts">
  import { onMount } from "svelte";
  import DeviceLogin from "../components/DeviceLogin.svelte";
  import Empty from "../components/Empty.svelte";
  import Icon from "../components/Icon.svelte";
  import LoadError from "../components/LoadError.svelte";
  import SkeletonRows from "../components/SkeletonRows.svelte";
  import { del, get, message, post } from "../lib/api";
  import { onCore } from "../lib/events.svelte";
  import { ago } from "../lib/format";
  import type { ModsView } from "../lib/types";

  let view = $state<ModsView | null>(null);
  let loadError = $state<string | null>(null);
  let inviting = $state(false);
  let error = $state<string | null>(null);
  let copied = $state<string | null>(null);
  let busy = $state<string | null>(null);

  const tunnel = $derived(view?.tunnel.state ?? "off");
  const mods = $derived(view?.mods ?? []);
  const connected = $derived(mods.filter((m) => m.connected).length);

  async function load() {
    loadError = null;
    try {
      view = await get<ModsView>("/mods");
    } catch (e) {
      loadError = message(e);
    }
  }

  onMount(() => {
    void load();
    return onCore((m) => {
      if (m.type === "extension" || m.type === "login") void load();
    });
  });

  function left(at: number): string {
    const hours = Math.ceil((at - Date.now() / 1000) / 3600);
    return hours <= 1 ? "within the hour" : `in ${hours} hours`;
  }

  async function invite() {
    inviting = true;
    error = null;
    try {
      await post("/mods/invites");
      await load();
    } catch (e) {
      error = message(e);
    } finally {
      inviting = false;
    }
  }

  async function copy(text: string, key: string) {
    await navigator.clipboard.writeText(text);
    copied = key;
    setTimeout(() => {
      if (copied === key) copied = null;
    }, 1600);
  }

  async function copyAddress() {
    if (!view?.link) return;
    await copy(view.link, "address");
    await post("/mods/link/shared").catch(() => {});
    await load();
  }

  async function act(id: string, path: string) {
    busy = id;
    error = null;
    try {
      await del(path);
      await load();
    } catch (e) {
      error = message(e);
    } finally {
      busy = null;
    }
  }
</script>

<div class="page">
  <div class="pagehead">
    <div>
      <h1>Mods</h1>
      <p>Let your mods answer ban requests and review Motwi from their own computers.</p>
    </div>
  </div>

  {#if loadError}
    <LoadError error={loadError} retry={load} />
  {:else if !view}
    <SkeletonRows rows={3} />
  {:else}
    <div class="layout">
      <div class="stack">
        {#if !view.can_invite}
          <div class="notice warn">
            <span class="ic"><Icon name="warning" size={18} /></span>
            <div class="grow">
              <p>Reconnect Twitch once before inviting anyone.</p>
              <p class="help">Motwi now asks Twitch who your mods are, and your sign-in is from before it could.</p>
              <div class="login"><DeviceLogin role="streamer" label="Reconnect Twitch" ondone={load} /></div>
            </div>
          </div>
        {/if}

        {#if view.needs_sharing && view.link}
          <div class="notice info" role="status">
            <span class="ic"><Icon name="shield-check" size={18} /></span>
            <div class="grow">Motwi's address changed. Send your mods the new link so they can reconnect.</div>
            <button class="btn sm pri" onclick={copyAddress}>
              <Icon name={copied === "address" ? "check" : "copy"} size={13} />{copied === "address" ? "Copied" : "Copy the link"}
            </button>
          </div>
        {/if}

        {#if view.check_problem}
          <div class="notice warn" role="status">
            <span class="ic"><Icon name="warning" size={18} /></span>
            <div class="grow">{view.check_problem} Nobody loses access until Twitch says so.</div>
          </div>
        {/if}

        <section class="sec">
          <h2>Your mods{#if mods.length}<span>{connected} connected now</span>{/if}</h2>
          <div class="panel">
            {#each mods as m (m.id)}
              <div class="row">
                <span class="dot" class:on={m.connected} aria-hidden="true"></span>
                <div class="grow">
                  <p class="name">{m.login}</p>
                  <p class="help">
                    {m.label},
                    {#if m.connected}in chat now{:else if m.last_seen}last here {ago(m.last_seen)}{:else}not connected yet{/if}
                  </p>
                </div>
                <button
                  class="btn sm ghost"
                  disabled={busy === m.id}
                  aria-label="Remove {m.login}"
                  onclick={() => act(m.id, `/mods/clients/${m.id}`)}
                >
                  {busy === m.id ? "Removing" : "Remove"}
                </button>
              </div>
            {:else}
              <Empty icon="shield-check" title="No mods connected yet">
                Your mods can keep moderating through Twitch as usual. Invite one to give them Motwi's ban requests and
                undo buttons in their own chat.
              </Empty>
            {/each}
          </div>
        </section>

        <section class="sec">
          <h2>Invites<span>each works once, for a day</span></h2>
          <div class="panel">
            {#each view.invites as i (i.id)}
              <div class="row">
                <div class="grow">
                  {#if i.url}
                    <p class="url mono" title={i.url}>{i.url}</p>
                    <p class="help">Waiting for a mod to open it. Expires {left(i.expires_at)}.</p>
                  {:else}
                    <p class="help">Waiting for an address from Cloudflare.</p>
                  {/if}
                </div>
                {#if i.url}
                  <button class="btn sm" onclick={() => copy(i.url!, i.id)}>
                    <Icon name={copied === i.id ? "check" : "copy"} size={13} />{copied === i.id ? "Copied" : "Copy"}
                  </button>
                {/if}
                <button class="btn sm ghost" disabled={busy === i.id} onclick={() => act(i.id, `/mods/invites/${i.id}`)}>
                  Cancel
                </button>
              </div>
            {/each}
            <div class="foot">
              <button class="btn" class:pri={!mods.length} onclick={invite} disabled={inviting || !view.can_invite}>
                {#if inviting}<Icon name="circle-notch" size={14} spin />{:else}<Icon name="plus" size={14} />{/if}
                {inviting ? "Opening a link" : "Invite a mod"}
              </button>
              <p class="help" role="status">
                {#if inviting}
                  The first link takes about half a minute, while its address reaches the internet.
                {:else}
                  Send it to them directly. It only works for someone Twitch says mods your channel.
                {/if}
              </p>
            </div>
          </div>
          {#if error}<p class="err" role="alert">{error}</p>{/if}
          {#if tunnel === "failed"}
            <p class="err" role="alert">{view.tunnel.detail || "Motwi can't open a link right now."}</p>
          {/if}
        </section>

        {#if mods.length}
          <section class="sec">
            <h2>Link for your mods<span>changes whenever Motwi restarts</span></h2>
            <div class="panel">
              <div class="row">
                <div class="grow">
                  {#if view.link}
                    <p class="url mono" title={view.link}>{view.link}</p>
                    <p class="help">
                      After a restart, send this to your mods. They won't need to sign in again, and it does nothing for
                      anyone else.
                    </p>
                  {:else if tunnel === "failed"}
                    <p class="help">{view.tunnel.detail}</p>
                  {:else}
                    <p class="help"><Icon name="circle-notch" size={13} spin /> Getting an address from Cloudflare.</p>
                  {/if}
                </div>
                {#if view.link}
                  <button class="btn sm" onclick={copyAddress}>
                    <Icon name={copied === "address" ? "check" : "copy"} size={13} />{copied === "address" ? "Copied" : "Copy"}
                  </button>
                {/if}
              </div>
            </div>
          </section>
        {/if}
      </div>

      <aside class="card side">
        <h2>How it works</h2>
        <ol>
          <li>You invite a mod and send them the link.</li>
          <li>They install Motwi's extension. The link offers it.</li>
          <li>They sign in with Twitch, and Motwi checks they really mod your channel.</li>
        </ol>
        <h3>What mods can do</h3>
        <p>Answer ban requests, undo actions and act on suggestions, all credited to them. They can't change rules or settings.</p>
        <h3>While it's on</h3>
        <p>
          Motwi opens a link through Cloudflare only while you have a mod or an open invite, and no one gets in without
          being a verified mod. Motwi rechecks every 10 minutes and removes anyone who stops being a mod.
        </p>
      </aside>
    </div>
  {/if}
</div>

<style>
  .layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 300px;
    gap: 22px;
    align-items: start;
  }
  .stack {
    display: flex;
    flex-direction: column;
    gap: 22px;
    min-width: 0;
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  .login {
    margin-top: 10px;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 12px;
    border-bottom: 1px solid var(--line);
  }
  .row:last-child {
    border-bottom: 0;
  }
  .name {
    font-weight: 600;
    font-size: 13.5px;
  }
  .row .help {
    margin-top: 1px;
  }
  .dot {
    width: 8px;
    height: 8px;
    flex: none;
    border-radius: 50%;
    background: var(--line-2);
  }
  .dot.on {
    background: var(--motwi);
  }
  .url {
    font-size: 12px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .foot {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 12px;
  }
  .foot .help {
    margin: 0;
  }
  .foot .btn {
    flex: none;
  }
  .side {
    display: grid;
    gap: 8px;
    font-size: 12.5px;
    color: var(--muted);
    position: sticky;
    top: 0;
  }
  .side h2 {
    color: var(--text);
  }
  .side h3 {
    margin-top: 8px;
    font-size: 12.5px;
    font-weight: 600;
    color: var(--text);
  }
  .side ol {
    padding-left: 18px;
    display: grid;
    gap: 4px;
  }
  @media (max-width: 1100px) {
    .layout {
      grid-template-columns: minmax(0, 1fr);
    }
    .side {
      position: static;
    }
  }
</style>
