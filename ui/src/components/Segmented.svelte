<script lang="ts" generics="T extends string">
  let {
    value,
    options,
    label,
    onchange,
  }: {
    value: T;
    options: { value: T; label: string }[];
    label: string;
    onchange: (value: T) => void;
  } = $props();

  function key(e: KeyboardEvent, i: number) {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = options[(i + step + options.length) % options.length];
    onchange(next.value);
    const group = (e.currentTarget as HTMLElement).parentElement;
    queueMicrotask(() => (group?.querySelector('[aria-checked="true"]') as HTMLElement)?.focus());
  }
</script>

<div class="seg" role="radiogroup" aria-label={label} style:--n={options.length}>
  {#each options as o, i (o.value)}
    <button
      type="button"
      role="radio"
      aria-checked={o.value === value}
      tabindex={o.value === value ? 0 : -1}
      onclick={() => onchange(o.value)}
      onkeydown={(e) => key(e, i)}>{o.label}</button
    >
  {/each}
</div>

<style>
  .seg {
    display: grid;
    grid-template-columns: repeat(var(--n), 1fr);
    background: var(--panel-2);
    border-radius: 5px;
    padding: 3px;
    gap: 3px;
  }
  button {
    border: 0;
    background: none;
    font-size: 12.5px;
    font-weight: 600;
    padding: 6px 10px;
    border-radius: var(--r-sm);
    color: var(--muted);
    cursor: pointer;
    white-space: nowrap;
  }
  button:hover {
    color: var(--text);
  }
  button[aria-checked="true"] {
    background: var(--line-2);
    color: var(--text);
  }
</style>
