from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from uuid import UUID

from django.utils.module_loading import import_string

from django_queue.backends.exceptions import InvalidQueueBackendError
from django_queue.entries import QueueEntry

if TYPE_CHECKING:
    from django_queue.worker import AsyncQueueWorker
    from django_queue.worker import QueueHandler as QueueEntryHandler


class BaseQueue(ABC):
    entry_class: type[QueueEntry] = QueueEntry
    worker_class: type[AsyncQueueWorker] | str = "django_queue.worker.AsyncQueueWorker"
    _queue_name: str | None = None

    @property
    def queue_name(self) -> str | None:
        """Return the stable entry namespace this queue writes under."""
        return self._queue_name

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
        """Create this queue's configured worker when it becomes active."""
        return self.resolve_worker_class(alias)({alias: self}, {alias: handler})

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
    def enqueue(self, payload) -> UUID:
        """Store a JSON-serialisable payload and return its queue-owned ID."""
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

    def __len__(self):
        return self.size()

    def __bool__(self):
        return not self.is_empty()
