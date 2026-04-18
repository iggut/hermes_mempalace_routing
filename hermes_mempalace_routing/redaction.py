from __future__ import annotations

import re
from dataclasses import dataclass

_RE_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_RE_SK_OPENAI_STYLE = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{16,}\b")
_RE_ASSIGN_SECRET = re.compile(
    r"(?i)^(\s*)([A-Za-z0-9_.-]*(api[_-]?key|access[_-]?token|client_secret|password|oauth[_-]?token|refresh[_-]?token))\s*[:=]\s*(\S+)"
)


@dataclass(slots=True)
class RedactionResult:
    text: str
    status: str  # none | masked | dropped
    replacements: int = 0


def redact_text(text: str, policy: str) -> RedactionResult:
    """
    Apply redaction policy before persistence.

    Policies:
    - none: no change
    - mask: replace known secret spans with [REDACTED]
    - drop: if any secret pattern matches, return empty string
    """
    if policy == "none":
        return RedactionResult(text=text, status="none", replacements=0)
    masked, n = _mask_secrets(text)
    if policy == "drop":
        if n > 0:
            return RedactionResult(text="", status="dropped", replacements=n)
        return RedactionResult(text=text, status="none", replacements=0)
    if policy == "mask":
        st = "masked" if n else "none"
        return RedactionResult(text=masked, status=st, replacements=n)
    raise ValueError(f"Unknown redaction policy: {policy}")


def _mask_secrets(text: str) -> tuple[str, int]:
    s = text
    n = 0

    def repl_bearer(m: re.Match[str]) -> str:
        nonlocal n
        n += 1
        return "Bearer [REDACTED]"

    s = _RE_BEARER.sub(repl_bearer, s)

    def repl_sk(m: re.Match[str]) -> str:
        nonlocal n
        n += 1
        return "sk-[REDACTED]"

    s = _RE_SK_OPENAI_STYLE.sub(repl_sk, s)

    def repl_assign(m: re.Match[str]) -> str:
        nonlocal n
        n += 1
        indent, lhs, _key, _val = m.group(1), m.group(2), m.group(3), m.group(4)
        return f"{indent}{lhs}=[REDACTED]"

    s = _RE_ASSIGN_SECRET.sub(repl_assign, s, count=1000)
    return s, n
