"""Minimal lifecycle contract for queue providers."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class QueueProvider(Protocol):
    """Release provider resources when its owning queue shuts down.

    Provider-specific delivery and storage operations intentionally do not
    belong here. Promote one only after multiple transports require the same
    transport-independent contract.
    """

    async def aclose(self) -> None: ...
