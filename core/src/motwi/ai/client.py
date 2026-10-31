"""OpenAI-compatible chat-completions client (Groq, Gemini, OpenRouter, custom)."""

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from motwi.ai.pricing import cost_for


@dataclass(frozen=True)
class Completion:
    text: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float | None
    provider: str
    model: str


class LLMError(Exception):
    """A provider call failed. Messages are short and never contain the API key."""


class LLMClient(Protocol):
    provider: str
    model: str

    async def complete(self, messages: list[dict], *, timeout_s: float) -> Completion: ...


class OpenAICompatClient:
    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str,
        model: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._http = http or httpx.AsyncClient()
        self._owns_http = http is None
        self._json_mode = True

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, "***") if self._api_key else text

    def _body(self, messages: list[dict]) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0.2}
        if self._json_mode:
            body["response_format"] = {"type": "json_object"}
        if self.provider == "openrouter":
            body["usage"] = {"include": True}
        return body

    async def _post(self, messages: list[dict], timeout_s: float) -> httpx.Response:
        try:
            return await self._http.post(
                self._url,
                json=self._body(messages),
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=timeout_s,
            )
        except httpx.TimeoutException as e:
            raise LLMError(f"{self.provider}: request timed out") from e
        except httpx.HTTPError as e:
            raise LLMError(f"{self.provider}: network error ({type(e).__name__})") from e

    async def complete(self, messages: list[dict], *, timeout_s: float) -> Completion:
        resp = await self._post(messages, timeout_s)
        if resp.status_code == 400 and self._json_mode and "response_format" in resp.text:
            self._json_mode = False
            resp = await self._post(messages, timeout_s)
        if not resp.is_success:
            snippet = self._redact(resp.text[:200].replace("\n", " "))
            raise LLMError(f"{self.provider}: HTTP {resp.status_code}: {snippet}")
        return self._parse(resp)

    def _parse(self, resp: httpx.Response) -> Completion:
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LLMError(f"{self.provider}: malformed response") from e
        if not isinstance(text, str):
            raise LLMError(f"{self.provider}: malformed response")
        usage = data.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        cost = usage.get("cost")
        if not isinstance(cost, (int, float)) or isinstance(cost, bool):
            cost = cost_for(self.model, prompt, completion)
        return Completion(text, prompt, completion, cost, self.provider, self.model)
