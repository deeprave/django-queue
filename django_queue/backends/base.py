from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

from asgiref.sync import async_to_sync
from django.utils.module_loading import import_string

from django_queue.backends.exceptions import (
    InvalidQueueBackendError,
    QueueReliableDeliveryUnsupportedError,
)
from django_queue.clock import DEFAULT_CLOCK, QueueClock
from django_queue.entries import QueueEntry

if TYPE_CHECKING:
    from django_queue.worker import AsyncQueueWorker
    from django_queue.worker import QueueHandler as QueueEntryHandler


class BaseQueue(ABC):
    default_claim_lease_seconds = 600
    entry_class: type[QueueEntry] = QueueEntry
    worker_class: type[AsyncQueueWorker] | str = "django_queue.worker.AsyncQueueWorker"
    # Set by the configured queue registry from the alias's TIMEOUT setting,
    # as entry_class and worker_class are. An entry's own budget takes
    # precedence over it, and a worker override over both.
    timeout_seconds: float | None = None
    _queue_name: str = ""
    _clock: QueueClock | None = None

    @property
    def queue_name(self) -> str:
        """Return the stable entry namespace this queue writes under.

        Empty when a backend never set one; entry creation rejects that, so an
        entry-capable backend must supply a name.
        """
        return self._queue_name

    @property
    def clock(self) -> QueueClock:
        """Return the clock this queue timestamps its entries with.

        Local time when a backend never set one, so a component recording times
        alongside this queue's entries can always ask rather than assume. Read
        through here rather than the attribute, so the fallback applies and the
        result is never optional.
        """
        return self._clock or DEFAULT_CLOCK

    def resolve_worker_class(self, alias: str) -> type[AsyncQueueWorker]:
        """Import and validate this queue's configured worker class."""
        # Imported here so the storage layer does not depend on the worker layer.
        from django_queue.worker import AsyncQueueWorker

        worker_class = self.worker_class
        if isinstance(worker_class, str):
            if not worker_class:
                raise InvalidQueueBackendError(
                    f"Queue alias '{alias}' WORKER must be a non-empty dotted path"
                )
            try:
                worker_class = import_string(worker_class)
            except ImportError as exc:
                raise InvalidQueueBackendError(
                    f"Queue alias '{alias}' WORKER could not be imported: {exc}"
                ) from exc
        if not isinstance(worker_class, type) or not issubclass(
            worker_class, AsyncQueueWorker
        ):
            raise InvalidQueueBackendError(
                f"Queue alias '{alias}' WORKER must be an AsyncQueueWorker subclass"
            )
        return worker_class

    def create_worker(self, alias: str, handler: QueueEntryHandler) -> AsyncQueueWorker:
        """Create this queue's configured worker when it becomes active.

        The worker is given this queue's clock, so its recorded time and the
        entries it dispatches share one basis. A configured WORKER subclass
        overriding `__init__` must therefore accept a `clock` keyword.
        """
        return self.resolve_worker_class(alias)(
            {alias: self}, {alias: handler}, clock=self.clock
        )

    @property
    def stack(self):
        return False

    @property
    @abstractmethod
    def capacity(self):
        raise NotImplementedError("capacity")

    def add(self, *items):
        return self._run_synchronously(self.aadd, *items)

    @abstractmethod
    async def aadd(self, *items) -> None:
        raise NotImplementedError("aadd")

    def get(self):
        return self._run_synchronously(self.aget)

    @abstractmethod
    async def aget(self):
        raise NotImplementedError("aget")

    def poll(self):
        return self._run_synchronously(self.apoll)

    @abstractmethod
    async def apoll(self):
        raise NotImplementedError("apoll")

    def peek(self):
        return self._run_synchronously(self.apeek)

    @abstractmethod
    async def apeek(self):
        raise NotImplementedError("apeek")

    def size(self):
        return self._run_synchronously(self.asize)

    @abstractmethod
    async def asize(self):
        raise NotImplementedError("asize")

    def is_empty(self):
        return self.size() == 0

    async def ais_empty(self) -> bool:
        return await self.asize() == 0

    def clear(self):
        return self._run_synchronously(self.aclear)

    async def aclear(self) -> None:
        pass

    def close(self):
        return async_to_sync(self.aclose)()

    async def aclose(self) -> None:
        """Release resources owned by the running event loop."""

    def _run_synchronously(
        self, operation: Callable[..., Awaitable[Any]], *args, **kwargs
    ):
        """Run one async API call and release any bridge-loop resources."""
        return async_to_sync(self._run_and_close)(operation, *args, **kwargs)

    async def _run_and_close(
        self, operation: Callable[..., Awaitable[Any]], *args, **kwargs
    ) -> Any:
        try:
            return await operation(*args, **kwargs)
        finally:
            await self.aclose()

    def enqueue(self, payload, *, timeout_seconds: float | None = None) -> UUID:
        return self._run_synchronously(
            self.aenqueue, payload, timeout_seconds=timeout_seconds
        )

    @abstractmethod
    async def aenqueue(self, payload, *, timeout_seconds: float | None = None) -> UUID:
        """Store a JSON-serialisable payload and return its queue-owned ID.

        An execution budget given here is carried on the entry and persisted
        with it, so it survives enqueue and reaches whichever worker dispatches
        the entry.
        """
        raise NotImplementedError("aenqueue")

    def get_entry(self, entry_id: UUID) -> QueueEntry:
        return self._run_synchronously(self.aget_entry, entry_id)

    @abstractmethod
    async def aget_entry(self, entry_id: UUID) -> QueueEntry:
        """Return the retained entry record for *entry_id*."""
        raise NotImplementedError("aget_entry")

    async def apublish_lifecycle_snapshot(self, entry: QueueEntry) -> None:
        """Best-effort publish one worker-observed lifecycle snapshot."""

    def dequeue_entry(self) -> QueueEntry:
        return self._run_synchronously(self.adequeue_entry)

    @abstractmethod
    async def adequeue_entry(self) -> QueueEntry:
        """Remove and return the next pending entry (best effort)."""
        raise NotImplementedError("adequeue_entry")

    @property
    def supports_claim_leases(self) -> bool:
        """Whether this backend can provide claim-based reliable delivery."""
        return False

    def claim_entry(
        self, worker_id: UUID, lease_seconds: float | None = None
    ) -> QueueEntry:
        return self._run_synchronously(self.aclaim_entry, worker_id, lease_seconds)

    async def aclaim_entry(
        self, worker_id: UUID, lease_seconds: float | None = None
    ) -> QueueEntry:
        raise QueueReliableDeliveryUnsupportedError

    def renew_claim(
        self, entry_id: UUID, worker_id: UUID, lease_seconds: float
    ) -> bool:
        return self._run_synchronously(
            self.arenew_claim, entry_id, worker_id, lease_seconds
        )

    async def arenew_claim(
        self, entry_id: UUID, worker_id: UUID, lease_seconds: float
    ) -> bool:
        raise QueueReliableDeliveryUnsupportedError

    def acknowledge_claim(self, entry_id: UUID, worker_id: UUID) -> bool:
        return self._run_synchronously(self.aacknowledge_claim, entry_id, worker_id)

    async def aacknowledge_claim(self, entry_id: UUID, worker_id: UUID) -> bool:
        raise QueueReliableDeliveryUnsupportedError

    def settle_claim(self, worker_id: UUID, entry: QueueEntry) -> bool:
        return self._run_synchronously(self.asettle_claim, worker_id, entry)

    async def asettle_claim(self, worker_id: UUID, entry: QueueEntry) -> bool:
        """Atomically persist a terminal entry and release its owned claim."""
        raise QueueReliableDeliveryUnsupportedError

    def recover_expired_claims(self) -> int:
        return self._run_synchronously(self.arecover_expired_claims)

    async def arecover_expired_claims(self) -> int:
        raise QueueReliableDeliveryUnsupportedError

    def mark_claim_running(self, entry_id: UUID, worker_id: UUID) -> QueueEntry | None:
        return self._run_synchronously(self.amark_claim_running, entry_id, worker_id)

    async def amark_claim_running(
        self, entry_id: UUID, worker_id: UUID
    ) -> QueueEntry | None:
        """Mark a queued entry running only while its claim remains owned."""
        raise QueueReliableDeliveryUnsupportedError

    def has_pending_entries(self) -> bool:
        return self._run_synchronously(self.ahas_pending_entries)

    @abstractmethod
    async def ahas_pending_entries(self) -> bool:
        """Return whether an entry worker can dequeue pending work."""
        raise NotImplementedError("ahas_pending_entries")

    def mark_running(self, entry_id: UUID) -> QueueEntry:
        return self._run_synchronously(self.amark_running, entry_id)

    @abstractmethod
    async def amark_running(self, entry_id: UUID) -> QueueEntry:
        raise NotImplementedError("amark_running")

    def mark_succeeded(self, entry_id: UUID, result) -> QueueEntry:
        return self._run_synchronously(self.amark_succeeded, entry_id, result)

    @abstractmethod
    async def amark_succeeded(self, entry_id: UUID, result) -> QueueEntry:
        raise NotImplementedError("amark_succeeded")

    def mark_failed(self, entry_id: UUID, error: Exception) -> QueueEntry:
        return self._run_synchronously(self.amark_failed, entry_id, error)

    @abstractmethod
    async def amark_failed(self, entry_id: UUID, error: Exception) -> QueueEntry:
        raise NotImplementedError("amark_failed")

    def mark_cancelled(self, entry_id: UUID) -> QueueEntry:
        return self._run_synchronously(self.amark_cancelled, entry_id)

    @abstractmethod
    async def amark_cancelled(self, entry_id: UUID) -> QueueEntry:
        raise NotImplementedError("amark_cancelled")

    def mark_timed_out(self, entry_id: UUID) -> QueueEntry:
        return self._run_synchronously(self.amark_timed_out, entry_id)

    @abstractmethod
    async def amark_timed_out(self, entry_id: UUID) -> QueueEntry:
        """Record that a handler exceeded its budget and was abandoned.

        Distinct from cancellation, which means the worker stopped the entry
        deliberately and the handler complied.
        """
        raise NotImplementedError("amark_timed_out")

    def __len__(self):
        return self.size()

    def __bool__(self):
        return not self.is_empty()


class AsyncQueue(BaseQueue):
    """A queue whose worker persists task lifecycle outcomes."""

    def _list_entries(self) -> list[QueueEntry]:
        """Return retained task snapshots for lifecycle-observer bootstrap."""
        return self._run_synchronously(self._alist_entries)

    @abstractmethod
    async def _alist_entries(self) -> list[QueueEntry]:
        """Return retained task snapshots for lifecycle-observer bootstrap."""
        raise NotImplementedError("_alist_entries")

    def _initialise_lifecycle_observers(self) -> None:
        """Initialise the process-local observer state owned by this queue."""
        self._lifecycle_observer_lock = threading.RLock()
        self._lifecycle_observers = None


class EventQueue(BaseQueue):
    """A queue whose listeners consume transient events."""
