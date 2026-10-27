import pytest

from motwi.filters.blocked import BlockedWord, BlockedWords

W = BlockedWord("w1", "strmboost")
P = BlockedWord("w2", "cheap viewers")
A = BlockedWord("w3", "ass")
bw = BlockedWords([W, P, A])


@pytest.mark.parametrize("text", ["STRMBOOST", "s t r m b o o s t", "strmb00st", "s.t.r.m.b.o.o.s.t",
                                  "strmbooooost", "go to strm\u200bboost now"])
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
