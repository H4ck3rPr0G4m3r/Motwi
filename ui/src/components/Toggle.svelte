<script lang="ts">
  let {
    checked,
    label,
    onchange,
    disabled = false,
    id,
  }: {
    checked: boolean;
    label: string;
    onchange: (value: boolean) => void;
    disabled?: boolean;
    id?: string;
  } = $props();
</script>

<button
  {id}
  type="button"
  role="switch"
  class="toggle"
  aria-checked={checked}
  aria-label={label}
  {disabled}
  onclick={() => onchange(!checked)}
>
  <span class="knob"></span>
</button>

<style>
  .toggle {
    width: 36px;
    height: 20px;
    border-radius: 10px;
    border: 1px solid var(--line-2);
    background: var(--panel-3);
    position: relative;
    cursor: pointer;
    flex: none;
    transition: background 0.15s;
  }
  .toggle[aria-checked="true"] {
    background: var(--motwi);
    border-color: var(--motwi);
  }
  .knob {
    position: absolute;
    top: 2px;
    left: 2px;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: var(--muted);
    transition: transform 0.18s var(--ease);
  }
  .toggle[aria-checked="true"] .knob {
    transform: translateX(16px);
    background: var(--motwi-ink);
  }
  .toggle:disabled {
    opacity: 0.5;
    cursor: default;
  }
</style>
