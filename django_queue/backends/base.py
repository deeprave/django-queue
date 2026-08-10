from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from uuid import UUID

from django.utils.module_loading import import_string

from django_queue.backends.exceptions import InvalidQueueBackendError
from django_queue.clock import DEFAULT_CLOCK, QueueClock
from django_queue.entries import QueueEntry

if TYPE_CHECKING:
    from django_queue.worker import AsyncQueueWorker
    from django_queue.worker import QueueHandler as QueueEntryHandler


class BaseQueue(ABC):
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

    @abstractmethod
    def add(self, *items):
        raise NotImplementedError("add")

    @abstractmethod
    def get(self):
        raise NotImplementedError("get")

    @abstractmethod
    def poll(self):
        raise NotImplementedError("poll")

    @abstractmethod
    def peek(self):
        raise NotImplementedError("peek")

    @abstractmethod
    def size(self):
        raise NotImplementedError("size")

    def is_empty(self):
        return self.size() == 0

    def clear(self):
        pass

    def close(self):
        pass

    @abstractmethod
    def enqueue(self, payload, *, timeout_seconds: float | None = None) -> UUID:
        """Store a JSON-serialisable payload and return its queue-owned ID.

        An execution budget given here is carried on the entry and persisted
        with it, so it survives enqueue and reaches whichever worker dispatches
        the entry.
        """
        raise NotImplementedError("enqueue")

    @abstractmethod
    def get_entry(self, entry_id: UUID) -> QueueEntry:
        """Return the retained entry record for *entry_id*."""
        raise NotImplementedError("get_entry")

    @abstractmethod
    def dequeue_entry(self) -> QueueEntry:
        """Remove and return the next pending entry (best effort)."""
        raise NotImplementedError("dequeue_entry")

    @abstractmethod
    def has_pending_entries(self) -> bool:
        """Return whether an entry worker can dequeue pending work."""
        raise NotImplementedError("has_pending_entries")

    @abstractmethod
    def mark_running(self, entry_id: UUID) -> QueueEntry:
        raise NotImplementedError("mark_running")

    @abstractmethod
    def mark_succeeded(self, entry_id: UUID, result) -> QueueEntry:
        raise NotImplementedError("mark_succeeded")

    @abstractmethod
    def mark_failed(self, entry_id: UUID, error: Exception) -> QueueEntry:
        raise NotImplementedError("mark_failed")

    @abstractmethod
    def mark_cancelled(self, entry_id: UUID) -> QueueEntry:
        raise NotImplementedError("mark_cancelled")

    @abstractmethod
    def mark_timed_out(self, entry_id: UUID) -> QueueEntry:
        """Record that a handler exceeded its budget and was abandoned.

        Distinct from cancellation, which means the worker stopped the entry
        deliberately and the handler complied.
        """
        raise NotImplementedError("mark_timed_out")

    def __len__(self):
        return self.size()

    def __bool__(self):
        return not self.is_empty()
