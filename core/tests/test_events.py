from motwi.events import AIStateChanged, EventBus


async def test_slow_subscriber_drops_oldest_not_blocks():
    bus = EventBus()
    slow = bus.subscribe(maxsize=3)
    fast = bus.subscribe(maxsize=100)
    for i in range(5):
        bus.publish(AIStateChanged("ok", str(i)))  # never blocks, even though slow is full
    assert [slow.get_nowait().detail for _ in range(slow.qsize())] == ["2", "3", "4"]
    assert fast.qsize() == 5


async def test_unsubscribe_stops_delivery():
    bus = EventBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    bus.publish(AIStateChanged("ok", ""))
    assert q.empty()
