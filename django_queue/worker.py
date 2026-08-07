"""Reusable asynchronous dispatcher for identified generic queue entries."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from django_queue.backends.base import BaseQueue
from django_queue.backends.exceptions import QueueEmptyException
from django_queue.entries import QueueEntry, QueueEntryStatus, validate_json_value

logger = logging.getLogger(__name__)

WORKER_EVENT_MESSAGES: Mapping[str, str] = {
    "started": "Queue worker started",
    "dispatch_started": "Queue worker began dispatching an entry",
    "terminal_recorded": "Queue worker recorded a terminal outcome",
    "stopped": "Queue worker stopped",
}

QueueHandler = Callable[[QueueEntry], Awaitable[object]]


class QueueLookup(Protocol):
    """Queue service lookup used by a worker's registered aliases."""

    def __getitem__(self, alias: str) -> BaseQueue: ...


class QueuePersistenceError(RuntimeError):
    """A terminal queue outcome could not be stored safely."""


@dataclass(frozen=True, slots=True)
class WorkerSnapshot:
    """Immutable, process-local state for an asynchronous queue worker."""

    worker_id: UUID
    running: bool
    started_at: datetime | None
    active_entry_id: UUID | None
    active_queue_name: str | None
    queue_names: tuple[str, ...]
    dispatch_count: int
    succeeded_count: int
    failed_count: int
    cancelled_count: int


class AsyncQueueWorker:
    """Sequentially process registered queues until its task is cancelled."""

    def __init__(
        self,
        queues: QueueLookup,
        handlers: Mapping[str, QueueHandler],
        *,
        idle_delay: float = 0.1,
        cancellation_grace_period: float = 30,
    ) -> None:
        self._queues = {name: queues[name] for name in handlers}
        self._handlers = dict(handlers)
        self._idle_delay = idle_delay
        self._cancellation_grace_period = cancellation_grace_period
        self._worker_id = uuid.uuid7()
        self._started_at: datetime | None = None
        self._active_entry_id: UUID | None = None
        self._active_queue_name: str | None = None
        self._dispatch_count = 0
        self._succeeded_count = 0
        self._failed_count = 0
        self._cancelled_count = 0
        self._running = False

    @property
    def running(self) -> bool:
        """Return whether this worker's dispatch loop is running."""
        return self._running

    @property
    def snapshot(self) -> WorkerSnapshot:
        """Return an immutable snapshot of this worker's local state."""
        return WorkerSnapshot(
            worker_id=self._worker_id,
            running=self.running,
            started_at=self._started_at,
            active_entry_id=self._active_entry_id,
            active_queue_name=self._active_queue_name,
            queue_names=tuple(self._queues),
            dispatch_count=self._dispatch_count,
            succeeded_count=self._succeeded_count,
            failed_count=self._failed_count,
            cancelled_count=self._cancelled_count,
        )

    async def run(self) -> None:
        """Dispatch registered queue entries until the caller cancels this task."""
        self._running = True
        self._started_at = datetime.now(UTC)
        self._log_state_change("started")
        try:
            while True:
                dispatched = False
                for name, queue in self._queues.items():
                    try:
                        entry = await asyncio.to_thread(queue.dequeue_entry)
                    except QueueEmptyException:
                        continue
                    dispatched = True
                    self._active_entry_id = entry.id
                    self._active_queue_name = name
                    self._dispatch_count += 1
                    self._log_state_change("dispatch_started")
                    await self._dispatch(queue, self._handlers[name], entry)
                if not dispatched:
                    await asyncio.sleep(self._idle_delay)
        finally:
            self._running = False
            self._active_entry_id = None
            self._active_queue_name = None
            self._log_state_change("stopped")

    async def _dispatch(
        self, queue: BaseQueue, handler: QueueHandler, entry: QueueEntry
    ) -> None:
        await asyncio.to_thread(queue.mark_running, entry.id)
        handler_task = asyncio.create_task(handler(entry))
        try:
            result = await asyncio.shield(handler_task)
        except asyncio.CancelledError:
            await self._finish_cancellation(queue, entry, handler_task)
            raise
        except Exception as exc:  # noqa: BLE001 - handlers may raise any application exception.
            await self._record_failure(queue, entry, exc)
        else:
            await self._record_result(queue, entry, result)

    async def _finish_cancellation(
        self,
        queue: BaseQueue,
        entry: QueueEntry,
        handler_task: asyncio.Task[object],
    ) -> None:
        try:
            result = await asyncio.wait_for(
                asyncio.shield(handler_task), self._cancellation_grace_period
            )
        except TimeoutError:
            handler_task.cancel()
            handler_task.add_done_callback(
                lambda task: self._log_late_handler_outcome(entry, task)
            )
            await self._record_terminal(queue, entry, queue.mark_cancelled)
        except Exception as exc:  # noqa: BLE001 - handlers may raise any application exception.
            await self._record_failure(queue, entry, exc)
        else:
            await self._record_result(queue, entry, result)

    @staticmethod
    def _log_late_handler_outcome(
        entry: QueueEntry, task: asyncio.Task[object]
    ) -> None:
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            logger.error(
                "Queue handler failed after cancellation for entry %s",
                entry.id,
                exc_info=(type(error), error, error.__traceback__),
            )

    async def _record_result(
        self, queue: BaseQueue, entry: QueueEntry, result: object
    ) -> None:
        try:
            validate_json_value(result)
        except TypeError as exc:
            await self._record_failure(queue, entry, exc)
        else:
            await self._record_terminal(queue, entry, queue.mark_succeeded, result)

    async def _record_failure(
        self, queue: BaseQueue, entry: QueueEntry, error: Exception
    ) -> None:
        logger.exception("Queue handler failed for entry %s", entry.id)
        await self._record_terminal(queue, entry, queue.mark_failed, error)

    async def _record_terminal(
        self,
        queue: BaseQueue,
        entry: QueueEntry,
        update: Callable[..., QueueEntry],
        *args: object,
    ) -> None:
        try:
            terminal_entry = await asyncio.to_thread(update, entry.id, *args)
        except Exception as exc:
            logger.exception(
                "Unable to record terminal queue outcome for entry %s", entry.id
            )
            terminal_entry = await self._record_persistence_failure(queue, entry)
            if terminal_entry is None:
                raise QueuePersistenceError(
                    "Unable to persist terminal queue outcome"
                ) from exc
        self._record_terminal_outcome(terminal_entry)

    async def _record_persistence_failure(
        self, queue: BaseQueue, entry: QueueEntry
    ) -> QueueEntry | None:
        try:
            current_entry = await asyncio.to_thread(queue.get_entry, entry.id)
        except Exception:
            logger.exception(
                "Unable to inspect queue entry %s after a persistence failure", entry.id
            )
            return None
        if current_entry.status is not QueueEntryStatus.RUNNING:
            return current_entry
        try:
            return await asyncio.to_thread(
                queue.mark_failed,
                entry.id,
                QueuePersistenceError("Unable to persist terminal queue outcome"),
            )
        except Exception:
            logger.exception(
                "Unable to record persistence failure for entry %s", entry.id
            )
            return None

    def _record_terminal_outcome(self, entry: QueueEntry) -> None:
        match entry.status:
            case QueueEntryStatus.SUCCEEDED:
                self._succeeded_count += 1
            case QueueEntryStatus.FAILED:
                self._failed_count += 1
            case QueueEntryStatus.CANCELLED:
                self._cancelled_count += 1
            case _:
                return
        self._active_entry_id = None
        self._active_queue_name = None
        self._log_state_change("terminal_recorded")

    def _log_state_change(self, event: str) -> None:
        snapshot = self.snapshot
        message = WORKER_EVENT_MESSAGES.get(event, event)
        logger.info(
            message,
            extra={
                "queue_worker_event": event,
                "queue_worker_id": str(snapshot.worker_id),
                "queue_worker_running": snapshot.running,
                "queue_worker_started_at": snapshot.started_at.isoformat()
                if snapshot.started_at is not None
                else None,
                "queue_worker_active_entry_id": str(snapshot.active_entry_id)
                if snapshot.active_entry_id is not None
                else None,
                "queue_worker_active_queue_name": snapshot.active_queue_name,
                "queue_worker_queue_names": snapshot.queue_names,
                "queue_worker_dispatch_count": snapshot.dispatch_count,
                "queue_worker_succeeded_count": snapshot.succeeded_count,
                "queue_worker_failed_count": snapshot.failed_count,
                "queue_worker_cancelled_count": snapshot.cancelled_count,
            },
        )
