"""Token prices used when a provider does not report the cost itself."""

# USD per million tokens: (input, output).
# Best-knowledge figures as of 2026-10-27; verify against provider pricing page.
PRICES: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
}


def cost_for(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    # OpenRouter-style ids carry a vendor prefix ("google/gemini-2.5-flash-lite").
    price = PRICES.get(model) or PRICES.get(model.rsplit("/", 1)[-1])
    if price is None:
        return None
    return (prompt_tokens * price[0] + completion_tokens * price[1]) / 1_000_000
