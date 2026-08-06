from abc import ABC, abstractmethod
from uuid import UUID

from django_queue.entries import QueueEntry


class BaseQueue(ABC):
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
