import pytest

from motwi.ai.parse import ParseError, parse_verdicts
from motwi.ai.prompt import BuiltRequest
from motwi.models import Verdict


def req_with_one_message_and_rule():
    return BuiltRequest(messages=[], id_map={"m1": "abc-123"}, rule_map={"r1": "RULEID"})


def test_parse_ignores_unknown_ids_and_clamps():
    text = (
        '```json\n{"results":[{"id":"m1","score":14,"rule":"r9","reason":"r"},'
        '{"id":"m7","score":10,"rule":"r1","reason":"injected"}]}\n```'
    )
    out = parse_verdicts(text, req_with_one_message_and_rule())
    assert out == {"abc-123": Verdict("abc-123", 10.0, None, "r")}


def test_parse_maps_known_rule_and_clamps_low():
    out = parse_verdicts(
        '{"results":[{"id":"m1","score":-3,"rule":"r1","reason":"ok"}]}',
        req_with_one_message_and_rule(),
    )
    assert out == {"abc-123": Verdict("abc-123", 0.0, "RULEID", "ok")}


def test_parse_bare_list_and_missing_reason():
    out = parse_verdicts(
        '[{"id": "m1", "score": 6.5, "rule": null}]', req_with_one_message_and_rule()
    )
    assert out == {"abc-123": Verdict("abc-123", 6.5, None, "")}


def test_parse_garbage_raises():
    with pytest.raises(ParseError):
        parse_verdicts("I think m1 is fine", req_with_one_message_and_rule())


@pytest.mark.parametrize("text", ['{"foo": 1}', '"just a string"', "42", "null"])
def test_parse_wrong_shape_raises(text):
    with pytest.raises(ParseError):
        parse_verdicts(text, req_with_one_message_and_rule())


def test_parse_duplicate_first_wins():
    text = (
        '{"results":[{"id":"m1","score":2,"rule":null,"reason":"first"},'
        '{"id":"m1","score":9,"rule":"r1","reason":"second"}]}'
    )
    out = parse_verdicts(text, req_with_one_message_and_rule())
    assert out == {"abc-123": Verdict("abc-123", 2.0, None, "first")}


def test_parse_non_numeric_score_skipped_and_reason_trimmed():
    req = BuiltRequest([], {"m1": "a", "m2": "b", "m3": "c"}, {})
    text = (
        '{"results":[{"id":"m1","score":"high","reason":"x"},'
        '{"id":"m2","score":true,"reason":"x"},'
        '{"id":"m3","score":5,"reason":"' + "r" * 500 + '"}, "junk", 7]}'
    )
    out = parse_verdicts(text, req)
    assert list(out) == ["c"] and len(out["c"].reason) == 200


def test_parse_plain_fence_without_language():
    out = parse_verdicts('```\n[{"id":"m1","score":1}]\n```', req_with_one_message_and_rule())
    assert out["abc-123"].score == 1.0


def test_parse_empty_results_is_empty_dict():
    assert parse_verdicts('{"results": []}', req_with_one_message_and_rule()) == {}
