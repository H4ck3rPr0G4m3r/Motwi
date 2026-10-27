import json

import pytest

from motwi.models import DEFAULT_LADDER, Kind, LadderStep, Thresholds
from motwi.settings import ProviderConfig, Settings


def test_roundtrip_and_unknown_keys():
    s = Settings(
        strictness="strict",
        suggest_only=True,
        primary=ProviderConfig("gemini", "gemini-2.5-flash-lite"),
    )
    d = s.to_dict()
    d["future_key"] = 1
    assert Settings.from_dict(d) == s


def test_roundtrip_is_json_serializable():
    s = Settings(
        strictness="custom",
        custom_thresholds=Thresholds(4, 5, 6),
        backup=ProviderConfig("custom", "m", base_url="http://x/v1"),
    )
    assert Settings.from_dict(json.loads(json.dumps(s.to_dict()))) == s


def test_missing_keys_use_defaults():
    assert Settings.from_dict({}) == Settings()
    assert Settings.from_dict({"batch_max": 3}).batch_max == 3


def test_custom_requires_thresholds():
    with pytest.raises(ValueError):
        Settings(strictness="custom").validate()
    custom = Settings(strictness="custom", custom_thresholds=Thresholds(4, 5, 6))
    assert custom.thresholds() == Thresholds(4, 5, 6)


def test_preset_thresholds():
    assert Settings(strictness="relaxed").thresholds() == Thresholds(7.0, 8.5, 9.5)


def test_defaults_validate():
    Settings().validate()


@pytest.mark.parametrize(
    "field,value",
    [
        ("strictness", "bogus"),
        ("vip_leeway", -0.1),
        ("vip_leeway", 5.1),
        ("hold_timeout_s", 59),
        ("hold_timeout_s", 1_209_601),
        ("batch_max", 0),
        ("batch_max", 51),
        ("batch_wait_s", 0.1),
        ("batch_wait_s", 10.5),
        ("ai_timeout_s", 0.5),
        ("ai_timeout_s", 61),
        ("context_lines", -1),
        ("context_lines", 51),
        ("retention_days", 0),
        ("retention_days", 366),
    ],
)
def test_out_of_range_rejected(field, value):
    with pytest.raises(ValueError):
        Settings(**{field: value}).validate()


@pytest.mark.parametrize(
    "ladder",
    [
        (),
        (LadderStep(Kind.DELETE),),
        (LadderStep(Kind.TIMEOUT),),
        (LadderStep(Kind.TIMEOUT, 0),),
    ],
)
def test_bad_ladder_rejected(ladder):
    with pytest.raises(ValueError):
        Settings(ladder=ladder).validate()


def test_default_ladder_roundtrip():
    assert Settings.from_dict(Settings().to_dict()).ladder == DEFAULT_LADDER
