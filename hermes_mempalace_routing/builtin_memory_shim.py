from __future__ import annotations

from typing import Any, Protocol


class HermesBuiltinDurableMemory(Protocol):
    """Shape expected by some Hermes hosts for long-term memory (replace with MemPalace-first)."""

    def persist_long_term(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def recall_long_term(self, *args: Any, **kwargs: Any) -> Any:
        ...


class NoOpBuiltinDurableMemory:
    """
    Compatibility shim: disable Hermes built-in durable persistence/recall when the host
    routes durable memory through MemPalace + this package.

    Keeps transient/session buffers unchanged in the host; only the durable API is no-op.
    """

    def persist_long_term(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def recall_long_term(self, *_args: Any, **_kwargs: Any) -> None:
        return None
