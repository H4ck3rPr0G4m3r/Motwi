from motwi.engine.offenses import OffenseTracker


def test_record_and_last_index():
    t = OffenseTracker()
    assert t.last_index("u", 0) is None
    t.record("u", 0, 1, 100.0)
    t.record("u", 1, 2, 200.0)
    assert t.last_index("u", 300.0) == 1
    assert t.count("u", 300.0) == 2
    assert t.last_index("other", 300.0) is None


def test_delete_only_offense_is_remembered_as_minus_one():
    t = OffenseTracker()
    t.record("u", -1, 1, 0.0)
    assert t.last_index("u", 1.0) == -1


def test_undo_falls_back_to_previous():
    t = OffenseTracker()
    t.record("u", 0, 1, 0.0)
    t.record("u", 1, 2, 10.0)
    t.undo(2)
    assert t.last_index("u", 20.0) == 0
    assert t.count("u", 20.0) == 1
    t.undo(1)
    assert t.last_index("u", 20.0) is None


def test_undo_unknown_or_repeated_is_noop():
    t = OffenseTracker()
    t.record("u", 0, 1, 0.0)
    t.undo(99)
    t.undo(1)
    t.undo(1)
    assert t.last_index("u", 1.0) is None


def test_inactivity_reset():
    t = OffenseTracker(inactivity_reset_s=100)
    t.record("u", 2, 1, 0.0)
    assert t.last_index("u", 100.0) == 2
    assert t.last_index("u", 100.1) is None
    assert t.count("u", 100.1) == 0


def test_inactivity_measured_from_most_recent_offense():
    t = OffenseTracker(inactivity_reset_s=100)
    t.record("u", 0, 1, 0.0)
    t.record("u", 1, 2, 90.0)
    assert t.last_index("u", 180.0) == 1
    assert t.count("u", 180.0) == 2


def test_reset_clears_everything():
    t = OffenseTracker()
    t.record("u", 0, 1, 0.0)
    t.reset()
    assert t.last_index("u", 1.0) is None
    t.undo(1)
