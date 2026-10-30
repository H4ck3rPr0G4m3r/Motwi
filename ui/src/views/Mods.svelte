<script lang="ts">
  import Icon from "../components/Icon.svelte";
</script>

<div class="page">
  <div class="pagehead">
    <div>
      <h1>Mods</h1>
      <p>Let your mods answer ban requests and review Motwi from their own computers.</p>
    </div>
  </div>
  <div class="card intro">
    <Icon name="shield-check" size={22} />
    <div>
      <p class="t">Mod invites are coming</p>
      <p class="muted">
        Soon you'll invite a mod with a one-time link. They install the extension, sign in with Twitch, and Motwi
        checks they really mod your channel. Until then your mods keep moderating through Twitch as usual, and Motwi
        leaves their messages alone.
      </p>
    </div>
  </div>
</div>

<style>
  .intro {
    display: flex;
    gap: 14px;
    max-width: 680px;
    align-items: flex-start;
  }
  .intro :global(.ic) {
    color: var(--motwi);
    margin-top: 2px;
  }
  .t {
    font-weight: 600;
    margin-bottom: 4px;
  }
  .muted {
    font-size: 13px;
  }
</style>
