"""Token prices used when a provider does not report the cost itself."""

# USD per million tokens: (input, output).
# Best-knowledge figures as of 2026-10-27; verify against provider pricing page.
# The gpt-oss figures are from Groq's model list, checked 2026-10-31.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-oss-20b": (0.075, 0.30),
    "gpt-oss-120b": (0.15, 0.60),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
}


def cost_for(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    # OpenRouter and Groq ids carry a vendor prefix ("google/gemini-2.5-flash-lite").
    price = PRICES.get(model) or PRICES.get(model.rsplit("/", 1)[-1])
    if price is None:
        return None
    return (prompt_tokens * price[0] + completion_tokens * price[1]) / 1_000_000
