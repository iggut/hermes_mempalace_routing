from hermes_mempalace_routing.tokenizer import count_tokens, estimate_tokens_fallback, truncate_to_tokens


def test_estimate_fallback_is_conservative_vs_chars_over_4():
    text = "a" * 120
    est = estimate_tokens_fallback(text)
    naive = len(text) // 4
    assert est >= naive


def test_count_tokens_never_raises():
    assert count_tokens("hello", strategy="estimate") > 0
    assert count_tokens("", strategy="estimate") == 0


def test_truncate_respects_cap():
    long_text = "word " * 500
    out = truncate_to_tokens(long_text, max_tokens=10, strategy="estimate")
    assert count_tokens(out, strategy="estimate") <= 10
