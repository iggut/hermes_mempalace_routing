import pytest

from hermes_mempalace_routing.redaction import redact_text


def test_mask_api_key_assignment() -> None:
    r = redact_text('api_key="sk-123456789012345678901234567890"', "mask")
    assert r.replacements >= 1
    assert "sk-" not in r.text or "[REDACTED]" in r.text


def test_mask_bearer() -> None:
    r = redact_text("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "mask")
    assert "Bearer [REDACTED]" in r.text


def test_mask_credential_line() -> None:
    r = redact_text("client_secret=supersecretvalue", "mask")
    assert "[REDACTED]" in r.text


def test_none_policy_noop() -> None:
    s = "api_key=secret"
    r = redact_text(s, "none")
    assert r.text == s
    assert r.replacements == 0
