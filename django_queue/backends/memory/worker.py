"""Memory-aware default workers selected by memory queue backends."""

import logging

from django_queue.backends.exceptions import (
    QueueClaimConflictError,
    QueueEmptyException,
    QueueEntryExpiredError,
    QueueEntryMissingError,
    QueueEntryNotFoundError,
)
from django_queue.entries import QueueEntry
from django_queue.event_worker import EventQueueWorker
from django_queue.worker import AsyncQueueWorker

logger = logging.getLogger(__name__)


class MemoryAsyncQueueWorker(AsyncQueueWorker):
    """Default worker for queues composed with QueueProviderMemory."""


class MemoryEventQueueWorker(EventQueueWorker):
    """Default event worker for queues composed with QueueProviderMemory."""

    provider_kind = "memory"
    provider_type = "memory"

    def __init__(self, queue, **kwargs) -> None:
        super().__init__(queue, **kwargs)
        self._provider = queue._provider

    async def _next_event(self) -> tuple[QueueEntry, float | None] | None:
        expired_entry_ids = await self._provider.aexpire_due()
        for entry_id in expired_entry_ids:
            logger.warning(
                "Discarded expired event",
                extra={"queue": self._queue.queue_name, "entry_id": str(entry_id)},
            )
        try:
            entry = await self._provider.aclaim_unexpired(self._worker_id)
        except QueueEmptyException:
            return None
        except QueueClaimConflictError as exc:
            logger.debug(
                "Event entry is already claimed by another worker",
                extra={"queue": self._queue.queue_name, "entry_id": str(exc.entry_id)},
            )
            return None
        except QueueEntryExpiredError as exc:
            logger.debug(
                "Skipping event entry that expired before dispatch",
                extra={"queue": self._queue.queue_name, "entry_id": str(exc.entry_id)},
            )
            return None
        except QueueEntryMissingError as exc:
            logger.warning(
                "Discarding claim for missing event entry",
                extra={"queue": self._queue.queue_name, "entry_id": str(exc.entry_id)},
            )
            await self._provider.aremove(exc.entry_id, self._worker_id)
            return None
        except QueueEntryNotFoundError as exc:
            logger.warning(
                "Skipping event entry that disappeared before dispatch",
                extra={"queue": self._queue.queue_name, "entry_id": str(exc.entry_id)},
            )
            return None
        return entry, None

    async def _release(self, entry: QueueEntry) -> None:
        if not await self._provider.arelease(
            entry.id, self._worker_id, self.release_delay
        ):
            logger.warning("Lost claim for event entry %s before release", entry.id)

    async def _remove(self, entry: QueueEntry) -> None:
        if not await self._provider.aremove(entry.id, self._worker_id):
            logger.warning("Lost claim for event entry %s before removal", entry.id)
