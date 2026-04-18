from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def estimate_tokens_fallback(text: str) -> int:
    """Conservative token estimate when no model tokenizer is available (slight overcount)."""
    if not text:
        return 0
    # ~4 chars/token is common; use /3 to bias high vs naive /4
    return max(1, (len(text) + 2) // 3)


def count_tokens(
    text: str,
    model_hint: str | None = None,
    provider_hint: str | None = None,
    *,
    strategy: str = "estimate",
) -> int:
    """
    Count tokens for prompt budgeting. Never raises — falls back to conservative estimate.
    """
    if not text:
        return 0
    if strategy in ("tiktoken", "auto"):
        try:
            import tiktoken  # type: ignore[import-untyped]

            enc_name = "cl100k_base"
            if model_hint and "gpt-4" in model_hint.lower():
                enc_name = "cl100k_base"
            enc = tiktoken.get_encoding(enc_name)
            return len(enc.encode(text))
        except Exception as exc:  # pragma: no cover - optional dependency
            logger.debug("tiktoken unavailable or failed: %s", exc)
    try:
        return estimate_tokens_fallback(text)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("estimate_tokens_fallback failed: %s", exc)
        return max(1, len(text) // 2)


def truncate_to_tokens(
    text: str,
    max_tokens: int,
    model_hint: str | None = None,
    provider_hint: str | None = None,
    *,
    strategy: str = "estimate",
) -> str:
    """Truncate text so count_tokens is <= max_tokens (character-boundary safe)."""
    if max_tokens <= 0:
        return ""
    if count_tokens(text, model_hint, provider_hint, strategy=strategy) <= max_tokens:
        return text
    lo, hi = 0, len(text)
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        chunk = text[:mid]
        n = count_tokens(chunk, model_hint, provider_hint, strategy=strategy)
        if n <= max_tokens:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return text[:best]
