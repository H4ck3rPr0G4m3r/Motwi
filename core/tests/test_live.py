from motwi.live import LiveConfig
from motwi.models import Ceiling, StreamContext
from motwi.settings import Settings


async def test_load_defaults(store):
    live = await LiveConfig.load(store)
    assert live.settings == Settings()
    assert live.rules == [] and live.trusted == set() and live.vips == set()
    assert live.blocked.match("anything") is None
    assert live.stream == StreamContext()


async def test_reload_picks_up_changes_and_keeps_stream(store):
    live = await LiveConfig.load(store)
    live.stream = StreamContext("t", "g", "s1")
    rule = await store.add_rule("No spoilers", Ceiling.DELETE, "stream")
    await store.add_blocked("badword")
    await store.add_person("vip", "Kofi")
    await store.add_person("trusted", "Ann")
    await store.save_settings(Settings(strictness="strict"))
    await live.reload(store)
    assert live.settings.strictness == "strict"
    assert live.rules_by_id() == {rule.id: rule}
    assert live.blocked.match("you badword") is not None
    assert live.vips == {"kofi"} and live.trusted == {"ann"}
    assert live.stream == StreamContext("t", "g", "s1")
