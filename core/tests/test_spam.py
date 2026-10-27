from pathlib import Path

import pytest

from motwi.filters.spam import SpamConfig, SpamFilter, SpamVerdict
from motwi.models import ChatMessage

EMOTES = {"KEKW", "OMEGALUL", "LUL", "Kappa", "PogChamp", "Pog", "monkaS", "Copium", "Prayge"}


def msg(user: str, text: str, ts: float) -> ChatMessage:
    free = " ".join(w for w in text.split() if w not in EMOTES)
    return ChatMessage(
        id=f"{user}-{ts}",
        user_id=user,
        user_login=user,
        display_name=user,
        text=text,
        emote_free_text=free,
        badges=frozenset(),
        ts=ts,
    )


def load(name: str) -> list[str]:
    path = Path(__file__).parent / "fixtures" / name
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def feed(f: SpamFilter, user: str, texts: list[str], start: float, step: float, **kw):
    return [
        f.check(msg(user, t, start + i * step), known_chatter=kw.get("known_chatter", True))
        for i, t in enumerate(texts)
    ]


def test_corpus_is_big_enough():
    assert len(load("normal_chat.txt")) >= 60


def test_normal_corpus_never_spam():
    f = SpamFilter(SpamConfig())
    t = 0.0
    for i, line in enumerate(load("normal_chat.txt")):
        t += 0.7
        r = f.check(msg(f"user{i % 25}", line, t), known_chatter=i >= 25)
        assert r.verdict is not SpamVerdict.SPAM, line


def test_normal_corpus_from_known_chatters_is_ok():
    f = SpamFilter(SpamConfig())
    t = 0.0
    for i, line in enumerate(load("normal_chat.txt")):
        t += 0.7
        r = f.check(msg(f"user{i % 25}", line, t), known_chatter=True)
        assert r.verdict is SpamVerdict.OK, line


def test_copypasta_by_many_users_ok():
    f = SpamFilter(SpamConfig())
    pasta = "I'm not saying it was aliens but it was aliens " * 2
    assert all(
        f.check(msg(f"u{i}", pasta, i * 0.3), known_chatter=True).verdict is SpamVerdict.OK
        for i in range(20)
    )


def test_short_repeats_ok():
    f = SpamFilter(SpamConfig())
    results = feed(f, "u", ["W"] * 5, 0, 4.0)
    results += feed(f, "v", ["LETS GOOO"] * 3, 0, 8.0)
    assert all(r.verdict is SpamVerdict.OK for r in results)


def test_flood_spam():
    f = SpamFilter(SpamConfig())
    results = feed(
        f,
        "u",
        ["hello there", "what a play", "did you see it", "no way man", "look at that", "so good"],
        0,
        0.8,
    )
    assert results[-1].verdict is SpamVerdict.SPAM and results[-1].reason == "flood"
    assert results[3].verdict is SpamVerdict.BORDERLINE and results[3].recent
    assert results[2].verdict is SpamVerdict.OK


def test_four_messages_in_window_borderline_with_recent():
    f = SpamFilter(SpamConfig())
    results = feed(f, "u", ["line a", "line b", "line c", "line d"], 0, 1.0)
    assert results[-1].verdict is SpamVerdict.BORDERLINE
    assert results[-1].recent == ("line a", "line b", "line c", "line d")


def test_flood_window_expires():
    f = SpamFilter(SpamConfig())
    results = feed(f, "u", ["a", "b", "c", "d", "e", "f"], 0, 2.0)
    assert all(r.verdict is not SpamVerdict.SPAM for r in results)


def test_repeat_spam():
    f = SpamFilter(SpamConfig())
    text = "come watch my stream it is great"
    results = feed(f, "u", [text] * 3, 0, 10.0)
    assert results[1].verdict is SpamVerdict.OK
    assert results[2].verdict is SpamVerdict.SPAM and results[2].reason == "repeat"


def test_near_identical_repeat_spam():
    f = SpamFilter(SpamConfig())
    texts = [
        "come watch my stream it is great",
        "come watch my stream it is great!",
        "come watch my stream it is great!!",
    ]
    assert feed(f, "u", texts, 0, 10.0)[-1].reason == "repeat"


def test_repeat_outside_window_ok():
    f = SpamFilter(SpamConfig())
    results = feed(f, "u", ["come watch my stream it is great"] * 3, 0, 20.0)
    assert all(r.verdict is SpamVerdict.OK for r in results)


def test_repeat_ignores_emotes():
    f = SpamFilter(SpamConfig())
    results = feed(f, "u", ["KEKW KEKW KEKW KEKW KEKW"] * 3, 0, 10.0)
    assert all(r.verdict is SpamVerdict.OK for r in results)


def test_obfuscated_link_spam():
    r = SpamFilter(SpamConfig()).check(
        msg("viewz4cheap", "best viewers + followers at strmboost .shop", 0), known_chatter=False
    )
    assert r.verdict is SpamVerdict.SPAM and r.reason == "link spam"


@pytest.mark.parametrize(
    "link",
    [
        "https://spam.example/x",
        "www.bigfollows.net",
        "bigfollows dot com",
        "bigfollows(dot)gg",
        "bigfollows [dot] xyz",
        "bigfollows.io",
    ],
)
def test_link_forms_with_spam_phrase(link):
    r = SpamFilter(SpamConfig()).check(
        msg("u", f"buy followers cheap viewers {link}", 0), known_chatter=True
    )
    assert r.verdict is SpamVerdict.SPAM and r.reason == "link spam"


def test_link_from_new_chatter_borderline_with_recent():
    f = SpamFilter(SpamConfig())
    r = f.check(msg("new", "check out twitch.tv/somebody", 0), known_chatter=False)
    assert r.verdict is SpamVerdict.BORDERLINE and r.recent == ("check out twitch.tv/somebody",)


def test_link_from_known_chatter_ok():
    r = SpamFilter(SpamConfig()).check(
        msg("reg", "build guide: fextralife.com", 0), known_chatter=True
    )
    assert r.verdict is SpamVerdict.OK and r.recent == ()


def test_spam_phrase_without_link_from_new_chatter_borderline():
    f = SpamFilter(SpamConfig())
    r = f.check(msg("new", "become famous today", 0), known_chatter=False)
    assert r.verdict is SpamVerdict.BORDERLINE
    assert f.check(msg("old", "become famous today", 0), known_chatter=True).verdict is (
        SpamVerdict.OK
    )


def test_sentence_ending_is_not_a_link():
    r = SpamFilter(SpamConfig()).check(msg("new", "i died. me too", 0), known_chatter=False)
    assert r.verdict is SpamVerdict.OK


def test_char_flood_and_mass_mentions():
    f = SpamFilter(SpamConfig())
    assert f.check(msg("a", "a" * 59, 0), known_chatter=True).verdict is SpamVerdict.OK
    r = f.check(msg("b", "n" + "o" * 60, 0), known_chatter=True)
    assert r.verdict is SpamVerdict.SPAM and r.reason == "character flood"
    four = f.check(msg("c", "@a1 @b2 @c3 @d4 hi", 0), known_chatter=True)
    assert four.verdict is SpamVerdict.OK
    five = f.check(msg("d", "@a1 @b2 @c3 @d4 @e5 hi", 0), known_chatter=True)
    assert five.verdict is SpamVerdict.SPAM and five.reason == "mass mentions"
    same = f.check(msg("e", "@a1 @A1 @a1 @a1 @a1 @a1", 0), known_chatter=True)
    assert same.verdict is SpamVerdict.OK


def test_caps_never_matters():
    r = SpamFilter(SpamConfig()).check(
        msg("u", "THIS BOSS IS ABSOLUTELY INSANE CHAT", 0), known_chatter=False
    )
    assert r.verdict is SpamVerdict.OK


def test_users_are_independent():
    f = SpamFilter(SpamConfig())
    results = [
        f.check(msg(f"u{i}", "same line every user", i * 0.1), known_chatter=True)
        for i in range(10)
    ]
    assert all(r.verdict is SpamVerdict.OK for r in results)


def test_reset_and_recent_for():
    f = SpamFilter(SpamConfig(history_size=3))
    feed(f, "u", ["one", "two", "three", "four"], 0, 1.0)
    assert f.recent_for("u") == ("two", "three", "four")
    assert f.recent_for("nobody") == ()
    f.reset()
    assert f.recent_for("u") == ()


def test_tracked_users_are_bounded_lru():
    f = SpamFilter(SpamConfig(), max_users=3)
    for u in ("a", "b", "c"):
        f.check(msg(u, "hello", 0), known_chatter=True)
    f.check(msg("a", "again", 1), known_chatter=True)  # a is now most recent
    f.check(msg("d", "hello", 2), known_chatter=True)  # evicts b
    assert f.recent_for("b") == ()
    assert f.recent_for("a") != () and f.recent_for("c") != () and f.recent_for("d") != ()
