"""Configured worker and handler for the AsyncQueue dashboard demo."""

from __future__ import annotations

import asyncio
import random
import time

from django_queue.backends.base import BaseQueue
from django_queue.entries import QueueEntry, QueueEntryStatus
from django_queue.worker import AsyncQueueWorker

_QUEUED_DELAY_SECONDS = (10, 30)
_RUNNING_DELAY_SECONDS = (30, 60)


class DemoQueueWorker(AsyncQueueWorker):
    """Dispatch due entries, letting their handlers complete concurrently."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._handler_tasks: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        try:
            await super().run()
        finally:
            tasks = tuple(self._handler_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _next_entry(
        self, queue: BaseQueue
    ) -> tuple[QueueEntry, float | None] | None:
        entry = await queue.adequeue_entry()
        if _transition_due(entry, QueueEntryStatus.RUNNING):
            return entry, None
        await _requeue_entry(queue, entry)
        return None

    async def _dispatch(
        self,
        queue: BaseQueue,
        handler,
        entry: QueueEntry,
        lease_seconds: float | None = None,
    ) -> None:
        running_entry = await queue.amark_running(entry.id)
        await queue.apublish_lifecycle_snapshot(running_entry)
        task = asyncio.create_task(self._complete_entry(queue, handler, running_entry))
        self._handler_tasks.add(task)
        task.add_done_callback(self._handler_tasks.discard)

    async def _complete_entry(
        self, queue: BaseQueue, handler, entry: QueueEntry
    ) -> None:
        """Settle one independently running demo handler."""
        try:
            result = await handler(entry)
        except asyncio.CancelledError:
            await self._record_failure(
                queue, entry, RuntimeError("Demo handler terminated during shutdown")
            )
            raise
        except Exception as exc:  # noqa: BLE001 - demo failures are intentional.
            await self._record_failure(queue, entry, exc)
        else:
            await self._record_result(queue, entry, result)


async def handle_demo_entry(entry: QueueEntry) -> dict[str, str]:
    """Wait independently for the terminal transition, then report its outcome."""
    terminal_state = (
        QueueEntryStatus.FAILED
        if entry.payload["should_fail"]
        else QueueEntryStatus.SUCCEEDED
    )
    await asyncio.sleep(max(0, _transition_at(entry, terminal_state) - time.time()))
    if entry.payload["should_fail"]:
        raise RuntimeError("Intentional demo failure")
    return {"message": entry.payload["message"], "status": "processed"}


def build_demo_payload(message: str, should_fail: bool) -> dict:
    """Build payload metadata that drives this entry's two demo transitions."""
    running_at = time.time() + random.uniform(*_QUEUED_DELAY_SECONDS)
    terminal_state = "failed" if should_fail else "succeeded"
    return {
        "message": message,
        "source": "man -k .",
        "should_fail": should_fail,
        "transitions": [
            {"at": running_at, "state": "running"},
            {
                "at": running_at + random.uniform(*_RUNNING_DELAY_SECONDS),
                "state": terminal_state,
            },
        ],
    }


def _transition_due(entry: QueueEntry, state: QueueEntryStatus) -> bool:
    """Return whether the payload's next transition to ``state`` is due."""
    return time.time() >= _transition_at(entry, state)


def _transition_at(entry: QueueEntry, state: QueueEntryStatus) -> float:
    """Return the timestamp at which ``entry`` should transition to ``state``."""
    return next(
        transition["at"]
        for transition in entry.payload["transitions"]
        if transition["state"] == state
    )


async def _requeue_entry(queue, entry: QueueEntry) -> None:
    """Return a not-yet-due queued entry to the end of this demo's pending list."""
    await queue._async_redis().rpush(queue._entry_pending_name, str(entry.id))
