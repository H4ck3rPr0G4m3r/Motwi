"""Twitch tokens and API keys, kept in the OS credential store (Windows Credential Manager)."""

import json
from dataclasses import asdict, dataclass
from typing import Literal, Protocol

SERVICE = "Motwi"
Role = Literal["streamer", "bot"]


@dataclass(frozen=True)
class Token:
    access_token: str
    refresh_token: str
    expires_at: float
    scopes: tuple[str, ...]
    user_id: str
    login: str


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class KeyringSecrets:
    def get(self, name: str) -> str | None:
        import keyring

        return keyring.get_password(SERVICE, name)

    def set(self, name: str, value: str) -> None:
        import keyring

        keyring.set_password(SERVICE, name, value)

    def delete(self, name: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(SERVICE, name)
        except PasswordDeleteError:
            pass


class MemorySecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


class TokenStore:
    def __init__(self, secrets: SecretStore) -> None:
        self._secrets = secrets

    @staticmethod
    def _name(role: Role) -> str:
        return f"twitch:{role}"

    def load(self, role: Role) -> Token | None:
        raw = self._secrets.get(self._name(role))
        if raw is None:
            return None
        try:
            d = json.loads(raw)
            return Token(**{**d, "scopes": tuple(d["scopes"])})
        except (ValueError, TypeError, KeyError):
            return None

    def save(self, role: Role, token: Token) -> None:
        self._secrets.set(self._name(role), json.dumps(asdict(token)))

    def delete(self, role: Role) -> None:
        self._secrets.delete(self._name(role))


def ai_key_name(slot: Literal["primary", "backup"]) -> str:
    return f"ai:{slot}"
