import logging

from motwi import paths
from motwi.logs import setup_logging
from motwi.twitch.tokens import MemorySecrets, Token, TokenStore

TOKEN = Token("acc", "ref", 1000.0, ("user:read:chat",), "42", "streamer")


def test_token_round_trip_per_role():
    store = TokenStore(MemorySecrets())
    store.save("streamer", TOKEN)
    assert store.load("streamer") == TOKEN
    assert store.load("bot") is None


def test_delete_and_corrupt_values_read_as_missing():
    secrets = MemorySecrets()
    store = TokenStore(secrets)
    store.save("bot", TOKEN)
    store.delete("bot")
    assert store.load("bot") is None
    secrets.set("twitch:streamer", "{not json")
    assert store.load("streamer") is None


def test_data_dir_honours_motwi_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTWI_HOME", str(tmp_path / "home"))
    assert paths.db_path() == tmp_path / "home" / "motwi.db"
    assert paths.log_dir().is_dir()


def test_setup_logging_writes_file(tmp_path):
    path = setup_logging(tmp_path)
    logging.getLogger("motwi.test").info("hello")
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "baseFilename", None) == str(path):
            h.flush()
            root.removeHandler(h)
            h.close()
    assert "hello" in path.read_text(encoding="utf-8")
