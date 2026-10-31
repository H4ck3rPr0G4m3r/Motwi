import json

import httpx
import pytest
import respx

from motwi.ai.client import LLMError, OpenAICompatClient
from motwi.ai.pricing import cost_for
from motwi.ai.providers import PROVIDERS, client_for
from motwi.settings import ProviderConfig

URL = "https://api.test/v1/chat/completions"
MSGS = [{"role": "user", "content": "hi"}]


def ok(content="{}", usage=None):
    body = {"choices": [{"message": {"content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


def make(provider="gemini", model="gemini-2.5-flash-lite", key="sk-secret"):
    return OpenAICompatClient(
        provider=provider, base_url="https://api.test/v1/", api_key=key, model=model
    )


@respx.mock
async def test_success_parses_text_usage_and_cost():
    route = respx.post(URL).mock(
        return_value=ok(
            '{"results": []}',
            {"prompt_tokens": 120, "completion_tokens": 30, "cost": 0.0004},
        )
    )
    c = make(provider="openrouter", model="google/gemini-2.5-flash-lite")
    out = await c.complete(MSGS, timeout_s=5)
    assert (out.text, out.prompt_tokens, out.completion_tokens) == ('{"results": []}', 120, 30)
    assert out.cost_usd == 0.0004 and out.provider == "openrouter"
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer sk-secret"
    body = json.loads(req.content)
    assert body["temperature"] == 0.2 and body["response_format"] == {"type": "json_object"}
    assert body["usage"] == {"include": True} and body["messages"] == MSGS
    await c.aclose()


@respx.mock
async def test_non_openrouter_has_no_usage_flag():
    route = respx.post(URL).mock(return_value=ok())
    await make().complete(MSGS, timeout_s=5)
    assert "usage" not in json.loads(route.calls.last.request.content)


@respx.mock
async def test_cost_falls_back_to_price_table():
    respx.post(URL).mock(
        return_value=ok("x", {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000})
    )
    out = await make().complete(MSGS, timeout_s=5)
    assert out.cost_usd == pytest.approx(0.50)


@respx.mock
async def test_unknown_model_cost_is_none():
    respx.post(URL).mock(return_value=ok("x", {"prompt_tokens": 5, "completion_tokens": 5}))
    assert (await make(model="mystery").complete(MSGS, timeout_s=5)).cost_usd is None


@respx.mock
async def test_timeout_raises_llm_error():
    respx.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(LLMError, match="timed out"):
        await make().complete(MSGS, timeout_s=0.1)


@respx.mock
async def test_network_error_raises_llm_error():
    respx.post(URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(LLMError):
        await make().complete(MSGS, timeout_s=1)


@respx.mock
async def test_http_500_raises_without_leaking_key():
    respx.post(URL).mock(return_value=httpx.Response(500, text="oops key sk-secret failed"))
    with pytest.raises(LLMError) as ei:
        await make().complete(MSGS, timeout_s=1)
    assert "sk-secret" not in str(ei.value) and "500" in str(ei.value)


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"nope": 1}),
        httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
    ],
)
async def test_malformed_envelope_raises(response):
    respx.post(URL).mock(return_value=response)
    with pytest.raises(LLMError):
        await make().complete(MSGS, timeout_s=1)


@respx.mock
async def test_response_format_rejected_retries_without_it():
    route = respx.post(URL).mock(
        side_effect=[httpx.Response(400, text="response_format is not supported"), ok("fine")]
    )
    c = make()
    assert (await c.complete(MSGS, timeout_s=1)).text == "fine"
    assert len(route.calls) == 2
    assert "response_format" in json.loads(route.calls[0].request.content)
    assert "response_format" not in json.loads(route.calls[1].request.content)
    # remembered for the client's life
    route.side_effect = [ok("again")]
    await c.complete(MSGS, timeout_s=1)
    assert len(route.calls) == 3
    assert "response_format" not in json.loads(route.calls[2].request.content)


@respx.mock
async def test_other_400_is_not_retried():
    route = respx.post(URL).mock(return_value=httpx.Response(400, text="bad model"))
    with pytest.raises(LLMError):
        await make().complete(MSGS, timeout_s=1)
    assert len(route.calls) == 1


def test_custom_requires_base_url():
    with pytest.raises(ValueError):
        client_for(ProviderConfig("custom", "m"), "key")
    c = client_for(ProviderConfig("custom", "m", base_url="http://localhost:1234/v1"), "key")
    assert c.model == "m" and c.provider == "custom"


def test_presets_and_client_for_defaults():
    assert PROVIDERS["gemini"].default_model == "gemini-2.5-flash-lite"
    assert PROVIDERS["openrouter"].base_url == "https://openrouter.ai/api/v1"
    c = client_for(ProviderConfig("gemini", "gemini-2.5-flash"), "k")
    assert c._url == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"


def test_groq_preset():
    assert PROVIDERS["groq"].default_model == "openai/gpt-oss-20b"
    c = client_for(ProviderConfig("groq", "openai/gpt-oss-20b"), "k")
    assert c._url == "https://api.groq.com/openai/v1/chat/completions"


def test_cost_for():
    assert cost_for("gemini-2.5-flash", 1_000_000, 0) == pytest.approx(0.30)
    assert cost_for("google/gemini-2.5-flash-lite", 0, 1_000_000) == pytest.approx(0.40)
    assert cost_for("openai/gpt-oss-20b", 1_000_000, 1_000_000) == pytest.approx(0.375)
    assert cost_for("nope", 1, 1) is None
