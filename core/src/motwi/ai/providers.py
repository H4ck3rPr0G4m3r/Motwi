"""Provider presets and the factory turning a ProviderConfig into a client."""

from dataclasses import dataclass

import httpx

from motwi.ai.client import OpenAICompatClient
from motwi.settings import ProviderConfig


@dataclass(frozen=True)
class ProviderPreset:
    base_url: str | None
    default_model: str | None


PROVIDERS: dict[str, ProviderPreset] = {
    "groq": ProviderPreset("https://api.groq.com/openai/v1", "openai/gpt-oss-20b"),
    "gemini": ProviderPreset(
        "https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.5-flash-lite"
    ),
    "openrouter": ProviderPreset("https://openrouter.ai/api/v1", "google/gemini-2.5-flash-lite"),
    "custom": ProviderPreset(None, None),
}


def client_for(
    cfg: ProviderConfig, api_key: str, http: httpx.AsyncClient | None = None
) -> OpenAICompatClient:
    base_url = cfg.base_url or PROVIDERS[cfg.provider].base_url
    if not base_url:
        raise ValueError(f"provider {cfg.provider!r} requires a base_url")
    return OpenAICompatClient(
        provider=cfg.provider, base_url=base_url, api_key=api_key, model=cfg.model, http=http
    )
