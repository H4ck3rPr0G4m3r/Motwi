import pytest

from motwi.models import DEFAULT_LADDER, PRESETS, ChatMessage, Kind, Thresholds


def test_threshold_order_enforced():
    with pytest.raises(ValueError):
        Thresholds(7.0, 6.0, 9.0)
    with pytest.raises(ValueError):
        Thresholds(6.0, 7.5, 10.5)


def test_presets():
    assert PRESETS["standard"] == Thresholds(6.0, 7.5, 9.0)
    assert PRESETS["relaxed"] == Thresholds(7.0, 8.5, 9.5)
    assert PRESETS["strict"] == Thresholds(5.0, 6.5, 8.0)


def test_default_ladder():
    assert [(s.kind, s.duration_s) for s in DEFAULT_LADDER] == [
        (Kind.WARN, None), (Kind.TIMEOUT, 60), (Kind.TIMEOUT, 600), (Kind.BAN_REQUEST, None)]


def test_badges():
    m = ChatMessage("1", "u", "u", "U", "hi", "hi", frozenset({"moderator"}), 0.0)
    assert m.is_mod and not m.is_broadcaster and not m.is_vip
