<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import type { JoinResult, LinkInfo, Request } from "../shared/protocol";
  import { revoke, startSignIn, waitForSignIn, type DeviceCode } from "./twitch";

  type Phase = "checking" | "unreachable" | "moved" | "own" | "ask" | "invite" | "code" | "joining" | "done";

  const params = new URLSearchParams(location.hash.slice(1));
  const origin = params.get("o") ?? "";
  const code = params.get("c");

  let phase = $state<Phase>("checking");
  let channel = $state<string | null>(null);
  let login = $state<string | null>(null);
  let device = $state<DeviceCode | null>(null);
  let error = $state<string | null>(null);
  const cancel = new AbortController();

  const chatUrl = $derived(
    channel ? `https://www.twitch.tv/popout/${encodeURIComponent(channel)}/chat` : null,
  );

  function send<T>(m: Request): Promise<T> {
    return chrome.runtime.sendMessage(m);
  }

  onMount(async () => {
    const info = await send<LinkInfo>({ type: "link-info", origin });
    channel = info.channel;
    if (info.state === "unreachable") phase = "unreachable";
    else if (info.state === "moved") phase = "moved";
    else if (info.state === "own") phase = "own";
    else phase = code ? "invite" : "ask";
  });

  onDestroy(() => cancel.abort());

  async function signIn() {
    error = null;
    let token: string | null = null;
    try {
      device = await startSignIn();
      phase = "code";
      if (device.verification_uri.startsWith("https://www.twitch.tv/")) {
        await chrome.tabs.create({ url: device.verification_uri });
      }
      token = await waitForSignIn(device, cancel.signal);
      phase = "joining";
      const result = await send<JoinResult>({
        type: "join",
        origin,
        code: code!,
        twitchToken: token,
      });
      if (result.ok) {
        channel = result.channel;
        login = result.login;
        phase = "done";
        history.replaceState(null, "", location.pathname); // the code is used up
      } else {
        error = result.error;
        phase = "invite";
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      error = e instanceof Error ? e.message : "That didn't work. Try again.";
      phase = "invite";
    } finally {
      device = null;
      // it has done its one job, whichever way that went
      if (token) await revoke(token);
    }
  }
</script>

<header><span class="logo" aria-hidden="true">M</span>Motwi</header>

<div aria-live="polite">
  {#if phase === "checking"}
    <h1>Checking the link</h1>
    <p>This takes a moment.</p>
  {:else if phase === "unreachable"}
    <h1>Motwi isn't at this address</h1>
    <p>The address changes whenever the streamer restarts Motwi. Ask them for the new link.</p>
  {:else if phase === "moved"}
    <h1>You're back on {channel}</h1>
    <p>Motwi moved, and this browser followed it. You didn't need to sign in again. You can close this tab.</p>
    {#if chatUrl}<a class="action" href={chatUrl}>Open the chat</a>{/if}
  {:else if phase === "own"}
    <h1>This is your own Motwi</h1>
    <p>This browser is already connected to {channel} as the streamer, so there's nothing to do here.</p>
  {:else if phase === "ask"}
    <h1>You need an invite first</h1>
    <p>
      This link moves mods who are already connected to {channel ?? "this channel"}. To join for the first time, ask
      the streamer for an invite from the Mods page in their Motwi.
    </p>
  {:else if phase === "invite"}
    <h1>Help moderate {channel ?? "this channel"}</h1>
    <p>
      You'll be able to answer ban requests, undo Motwi's actions and act on its suggestions from your own chat.
      Anything you do is recorded under your name.
    </p>
    <p>
      Sign in with Twitch so Motwi can check you're a moderator of this channel. It only learns who you are: the
      sign-in asks for no permissions, and it's cancelled once they've checked.
    </p>
    {#if error}<p class="bad" role="alert">{error}</p>{/if}
    <button class="action" onclick={signIn}>{error ? "Try again" : "Sign in with Twitch"}</button>
    <p class="address">Link from <span class="mono">{new URL(origin).host}</span></p>
  {:else if phase === "code"}
    <h1>Approve this on Twitch</h1>
    <p>A Twitch tab just opened. Check it shows this code, then approve it. This page carries on by itself.</p>
    <p class="code mono">{device?.user_code}</p>
    {#if device}<a class="action quiet" href={device.verification_uri} target="_blank" rel="noreferrer">Open Twitch again</a>{/if}
  {:else if phase === "joining"}
    <h1>Checking with Twitch</h1>
    <p>Making sure you moderate {channel ?? "this channel"}.</p>
  {:else if phase === "done"}
    <h1>You're in, {login}</h1>
    <p>
      Motwi now shows up in {channel}'s chat, in this browser. If it ever says Motwi moved, ask the streamer for the
      new link. You won't need to sign in again.
    </p>
    {#if chatUrl}<a class="action" href={chatUrl}>Open the chat</a>{/if}
  {/if}
</div>
