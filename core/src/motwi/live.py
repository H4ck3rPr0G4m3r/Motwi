"""Hot-reloadable configuration snapshot shared by the running engine."""

from dataclasses import dataclass, field

from motwi.filters.blocked import BlockedWords
from motwi.models import Rule, StreamContext
from motwi.settings import Settings
from motwi.store import Store


@dataclass
class LiveConfig:
    settings: Settings
    rules: list[Rule]
    blocked: BlockedWords
    trusted: set[str]
    vips: set[str]
    stream: StreamContext = field(default_factory=StreamContext)

    @classmethod
    async def load(cls, store: Store) -> "LiveConfig":
        live = cls(Settings(), [], BlockedWords([]), set(), set())
        await live.reload(store)
        return live

    async def reload(self, store: Store) -> None:
        """Re-read everything persisted. The stream context is runtime-only and is kept."""
        self.settings = await store.get_settings()
        self.rules = await store.list_rules()
        self.blocked = BlockedWords(await store.list_blocked())
        self.trusted = set(await store.list_people("trusted"))
        self.vips = set(await store.list_people("vip"))

    def rules_by_id(self) -> dict[str, Rule]:
        return {r.id: r for r in self.rules}
