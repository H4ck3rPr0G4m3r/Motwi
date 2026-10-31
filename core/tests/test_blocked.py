import pytest

from motwi.filters.blocked import BlockedWord, BlockedWords
from motwi.models import Ceiling

W = BlockedWord("w1", "strmboost")
P = BlockedWord("w2", "cheap viewers")
A = BlockedWord("w3", "ass")
bw = BlockedWords([W, P, A])


@pytest.mark.parametrize(
    "text",
    [
        "STRMBOOST",
        "s t r m b o o s t",
        "strmb00st",
        "s.t.r.m.b.o.o.s.t",
        "strmbooooost",
        "go to strm\u200bboost now",
    ],
)
def test_obfuscations_match(text):
    assert bw.match(text) == W


def test_phrase_spans_tokens():
    assert bw.match("best CHEAP   viewers here") == P


def test_no_scunthorpe():
    assert bw.match("this class is great, assassin build") is None


def test_whole_word_still_matches():
    assert bw.match("you ass") == A


def test_empty_and_huge():
    assert bw.match("") is None and bw.match("a" * 5000) is None


def test_ass_does_not_match_as():
    assert bw.match("thanks, you as well") is None


@pytest.mark.parametrize(("phrase", "text"), [("szép", "szep"), ("szep", "SZÉP"), ("ő", "o")])
def test_diacritics_are_stripped(phrase, text):
    assert BlockedWords([BlockedWord("d", phrase)]).match(text) is not None


def test_most_severe_match_wins():
    timeout = BlockedWord("t", "foo", base_index=9, ceiling=Ceiling.TIMEOUT)
    ban = BlockedWord("b", "bar", base_index=0, ceiling=Ceiling.BAN)
    warn = BlockedWord("w", "baz", base_index=5, ceiling=Ceiling.WARN)
    assert BlockedWords([timeout, warn, ban]).match("foo bar baz") == ban


def test_ties_break_by_higher_base_index():
    low = BlockedWord("l", "foo", base_index=1, ceiling=Ceiling.TIMEOUT)
    high = BlockedWord("h", "bar", base_index=5, ceiling=Ceiling.TIMEOUT)
    assert BlockedWords([low, high]).match("foo bar") == high


def test_stronger_fixed_action_wins_when_several_words_match():
    upto = BlockedWord("u", "foo", ceiling=Ceiling.TIMEOUT)
    fixed = BlockedWord("f", "bar", ceiling=Ceiling.ALWAYS_TIMEOUT)
    assert BlockedWords([upto, fixed]).match("foo bar") == fixed
    ban = BlockedWord("b", "baz", ceiling=Ceiling.ALWAYS_BAN)
    assert BlockedWords([fixed, ban]).match("bar baz") == ban
