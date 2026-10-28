<script lang="ts">
  import { onDestroy } from "svelte";
  import { message, post } from "../lib/api";
  import { onCore } from "../lib/events.svelte";
  import type { DeviceCode, Role } from "../lib/types";
  import Icon from "./Icon.svelte";

  let {
    role,
    label,
    ondone,
  }: {
    role: Role;
    label: string;
    ondone?: (login: string, warning: string | null) => void;
  } = $props();

  let phase = $state<"idle" | "asking" | "code" | "error">("idle");
  let code = $state<DeviceCode | null>(null);
  let error = $state<string | null>(null);
  let copied = $state(false);
  let left = $state(0);
  let timer: ReturnType<typeof setInterval> | undefined;

  const stop = onCore((m) => {
    if (m.type !== "login" || m.role !== role || phase !== "code") return;
    clearInterval(timer);
    if (m.ok) {
      phase = "idle";
      code = null;
      ondone?.(m.login ?? "", m.error);
    } else {
      phase = "error";
      error = m.error ?? "Sign-in failed.";
    }
  });

  onDestroy(() => {
    stop();
    clearInterval(timer);
    if (phase === "code") post("/twitch/cancel", { role }).catch(() => {});
  });

  async function start() {
    phase = "asking";
    error = null;
    try {
      code = await post<DeviceCode>("/twitch/login", { role });
      phase = "code";
      left = code.expires_in;
      clearInterval(timer);
      timer = setInterval(() => {
        left -= 1;
        if (left <= 0) clearInterval(timer);
      }, 1000);
    } catch (e) {
      phase = "error";
      error = message(e);
    }
  }

  async function cancel() {
    clearInterval(timer);
    phase = "idle";
    code = null;
    await post("/twitch/cancel", { role }).catch(() => {});
  }

  async function copy() {
    if (!code) return;
    await navigator.clipboard.writeText(code.user_code);
    copied = true;
    setTimeout(() => (copied = false), 1600);
  }
</script>

{#if phase === "code" && code}
  <div class="flow" aria-live="polite">
    <p class="step">Open Twitch's activation page and enter this code:</p>
    <div class="code">
      <span class="mono">{code.user_code}</span>
      <button class="btn sm ghost" onclick={copy} aria-label="Copy code">
        <Icon name={copied ? "check" : "copy"} size={14} />{copied ? "Copied" : "Copy"}
      </button>
    </div>
    <div class="row">
      <a class="btn pri" href={code.verification_uri} target="_blank" rel="noreferrer">
        <Icon name="twitch-logo" size={15} />Open twitch.tv/activate<Icon
          name="arrow-square-out"
          size={13}
        />
      </a>
      <button class="btn ghost" onclick={cancel}>Cancel</button>
    </div>
    <p class="wait">
      <Icon name="circle-notch" size={14} spin />Waiting for you to approve Motwi
      {#if left > 0}<span class="dim mono">{Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")}</span>{/if}
    </p>
  </div>
{:else}
  <div class="start">
  <button class="btn pri" onclick={start} disabled={phase === "asking"}>
    {#if phase === "asking"}<Icon name="circle-notch" size={15} spin />{:else}<Icon
        name="twitch-logo"
        size={15}
      />{/if}
    {label}
  </button>
  {#if phase === "error"}<p class="err" role="alert">{error}</p>{/if}
  </div>
{/if}

<style>
  .start {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
  }
  .flow {
    display: grid;
    gap: 12px;
    padding: 14px;
    border: 1px solid var(--line);
    border-radius: var(--r);
    background: var(--panel-2);
  }
  .step {
    font-size: 13px;
    color: var(--muted);
  }
  .code {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .code .mono {
    font-size: 26px;
    font-weight: 600;
    letter-spacing: 0.12em;
  }
  .row {
    display: flex;
    gap: 8px;
  }
  a.btn {
    text-decoration: none;
  }
  .wait {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12.5px;
    color: var(--muted);
  }
</style>
