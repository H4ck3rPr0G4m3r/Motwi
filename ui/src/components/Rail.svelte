<script lang="ts">
  import { core } from "../lib/events.svelte";
  import { money, modelLabel, PROVIDER_LABEL } from "../lib/format";
  import type { IconName } from "../lib/icons";
  import { nav, type Route } from "../lib/router.svelte";
  import Icon from "./Icon.svelte";

  const items: { route: Route; label: string; icon: IconName }[] = [
    { route: "overview", label: "Overview", icon: "house" },
    { route: "rules", label: "Rules", icon: "scroll" },
    { route: "history", label: "History", icon: "clock-counter-clockwise" },
    { route: "people", label: "People", icon: "users" },
    { route: "stats", label: "Stats", icon: "chart-bar" },
    { route: "mods", label: "Mods", icon: "shield-check" },
    { route: "settings", label: "Settings", icon: "gear-six" },
  ];

  const s = $derived(core.status);
  const channel = $derived(s?.accounts.streamer?.login ?? "");
  const state = $derived.by(() => {
    if (!s) return { tone: "off", text: "" };
    switch (s.engine) {
      case "watching":
        return { tone: "on", text: s.stream.live ? `Watching ${channel}` : `Watching ${channel}, offline` };
      case "suggesting":
        return { tone: "on", text: `Suggesting in ${channel}` };
      case "paused":
        return { tone: "warn", text: "Paused" };
      case "connecting":
        return { tone: "warn", text: "Connecting to Twitch" };
      case "reconnect":
        return { tone: "bad", text: "Reconnect Twitch" };
      default:
        return { tone: "off", text: s.twitch.state === "error" ? "Twitch problem" : "Not watching" };
    }
  });
  const ai = $derived.by(() => {
    const p = s?.ai.primary;
    if (!p) return "No AI set up";
    const main = p.provider === "gemini" ? modelLabel(p.model) : `${modelLabel(p.model)} via ${PROVIDER_LABEL[p.provider]}`;
    return s?.ai.backup ? `${main}, backup ${PROVIDER_LABEL[s.ai.backup.provider]}` : main;
  });
</script>

<nav class="rail" aria-label="Motwi">
  <div class="brand"><span class="logo" aria-hidden="true">M</span><b>Motwi</b></div>
  {#each items as item (item.route)}
    <a
      class="nav"
      href="#/{item.route}"
      aria-current={nav.route === item.route ? "page" : undefined}
    >
      <Icon name={item.icon} size={17} />{item.label}
      {#if item.route === "overview" && s && s.open_requests > 0}
        <span class="badge" aria-label="{s.open_requests} open ban requests">{s.open_requests}</span>
      {/if}
    </a>
  {/each}
  <div class="status" aria-live="polite">
    <div><span class="dot {state.tone}" aria-hidden="true"></span>{state.text}</div>
    <div class:warn={s?.ai.state !== "ok"}>
      <Icon name="cpu" size={15} /><span>{s?.ai.state === "down" ? "AI down" : ai}</span>
    </div>
    <div><Icon name="coins" size={15} /><span><span class="mono">{money(s?.month_cost)}</span> this month</span></div>
  </div>
</nav>

<style>
  .rail {
    background: var(--rail);
    border-right: 1px solid var(--line);
    padding: 14px 10px;
    display: flex;
    flex-direction: column;
    gap: 2px;
    height: 100%;
    overflow-y: auto;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 4px 8px 16px;
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
  .brand b {
    font-size: 15px;
  }
  .nav {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 10px;
    border-radius: var(--r);
    color: var(--muted);
    font-weight: 500;
    font-size: 13.5px;
    text-decoration: none;
  }
  .nav:hover {
    color: var(--text);
    background: #17171b;
  }
  .nav[aria-current="page"] {
    background: var(--panel-2);
    color: var(--text);
  }
  .badge {
    margin-left: auto;
    font: 600 10.5px var(--mono);
    background: var(--ban);
    color: #1d0711;
    padding: 1px 6px;
    border-radius: 9px;
  }
  .status {
    margin-top: auto;
    border-top: 1px solid var(--line);
    padding: 12px 8px 2px;
    font-size: 12px;
    color: var(--muted);
    display: grid;
    gap: 8px;
  }
  .status div {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .status .warn {
    color: var(--caution);
  }
  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    margin: 0 4px;
    flex: none;
    background: #5a5a64;
  }
  .dot.on {
    background: var(--motwi);
    box-shadow: 0 0 0 3px var(--motwi-soft);
  }
  .dot.warn {
    background: var(--caution);
  }
  .dot.bad {
    background: var(--error);
  }
</style>
