"""Reusable asynchronous dispatcher for identified generic queue entries."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

from django_queue.backends.base import BaseQueue
from django_queue.backends.exceptions import QueueEmptyException
from django_queue.entries import QueueEntry, QueueEntryStatus, validate_json_value

logger = logging.getLogger(__name__)

QueueHandler = Callable[[QueueEntry], Awaitable[object]]


class QueueLookup(Protocol):
    """Queue service lookup used by a worker's registered aliases."""

    def __getitem__(self, alias: str) -> BaseQueue: ...


class QueuePersistenceError(RuntimeError):
    """A terminal queue outcome could not be stored safely."""


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
        self.running = False

    async def run(self) -> None:
        """Dispatch registered queue entries until the caller cancels this task."""
        self.running = True
        try:
            while True:
                dispatched = False
                for name, queue in self._queues.items():
                    try:
                        entry = await asyncio.to_thread(queue.dequeue_entry)
                    except QueueEmptyException:
                        continue
                    dispatched = True
                    await self._dispatch(queue, self._handlers[name], entry)
                if not dispatched:
                    await asyncio.sleep(self._idle_delay)
        finally:
            self.running = False

    async def _dispatch(self, queue: BaseQueue, handler: QueueHandler, entry: QueueEntry) -> None:
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
            result = await asyncio.wait_for(asyncio.shield(handler_task), self._cancellation_grace_period)
        except TimeoutError:
            handler_task.cancel()
            handler_task.add_done_callback(lambda task: self._log_late_handler_outcome(entry, task))
            await self._record_terminal(queue, entry, queue.mark_cancelled)
        except Exception as exc:  # noqa: BLE001 - handlers may raise any application exception.
            await self._record_failure(queue, entry, exc)
        else:
            await self._record_result(queue, entry, result)

    @staticmethod
    def _log_late_handler_outcome(entry: QueueEntry, task: asyncio.Task[object]) -> None:
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

    async def _record_result(self, queue: BaseQueue, entry: QueueEntry, result: object) -> None:
        try:
            validate_json_value(result)
        except TypeError as exc:
            await self._record_failure(queue, entry, exc)
        else:
            await self._record_terminal(queue, entry, queue.mark_succeeded, result)

    async def _record_failure(self, queue: BaseQueue, entry: QueueEntry, error: Exception) -> None:
        logger.exception("Queue handler failed for entry %s", entry.id)
        await self._record_terminal(queue, entry, queue.mark_failed, error)

    async def _record_terminal(self, queue: BaseQueue, entry: QueueEntry, update: Callable, *args) -> None:
        try:
            await asyncio.to_thread(update, entry.id, *args)
        except Exception as exc:
            logger.exception("Unable to record terminal queue outcome for entry %s", entry.id)
            if not await self._record_persistence_failure(queue, entry):
                raise QueuePersistenceError("Unable to persist terminal queue outcome") from exc

    async def _record_persistence_failure(self, queue: BaseQueue, entry: QueueEntry) -> bool:
        try:
            current_entry = await asyncio.to_thread(queue.get_entry, entry.id)
        except Exception:
            logger.exception("Unable to inspect queue entry %s after a persistence failure", entry.id)
            return False
        if current_entry.status is not QueueEntryStatus.RUNNING:
            return True
        try:
            await asyncio.to_thread(
                queue.mark_failed,
                entry.id,
                QueuePersistenceError("Unable to persist terminal queue outcome"),
            )
        except Exception:
            logger.exception("Unable to record persistence failure for entry %s", entry.id)
            return False
        return True
