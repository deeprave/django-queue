"""Local worker for transient event queues."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from abc import ABC, abstractmethod
from typing import Any

from asgiref.sync import sync_to_async

from django_queue.backends.base import EventQueue
from django_queue.entries import QueueEntry
from django_queue.listeners import ListenerRegistration, listeners_for
from django_queue.worker import BaseQueueWorker

logger = logging.getLogger(__name__)


class EventQueueWorker(BaseQueueWorker, ABC):
    """Provider-agnostic listener orchestration for event workers."""

    release_delay = 1.0
    recovery_interval = 1.0

    def __init__(
        self,
        queue: EventQueue,
        *,
        alias: str | None = None,
        idle_delay: float = 0.1,
    ) -> None:
        if not isinstance(queue, EventQueue):
            raise TypeError("EventQueueWorker requires an EventQueue")
        if self.provider_kind != queue.worker_provider_kind:
            raise TypeError(
                f"{type(queue).__name__} requires a {queue.worker_provider_kind} worker"
            )
        if not queue._worker_class_is_compatible(type(self)):
            raise TypeError(
                f"{type(self).__name__} is not compatible with {type(queue).__name__}"
            )
        super().__init__(
            idle_delay=idle_delay,
            worker_id=queue._worker_id_for_runtime(),
        )
        self._queue = queue
        self._alias = queue.queue_name if alias is None else alias
        self._cursor = -1

    async def run(self) -> None:
        self._running = True
        try:
            while True:
                if not await self.adispatch_once():
                    await asyncio.sleep(self._idle_delay)
        finally:
            self._running = False

    async def adispatch_once(self) -> bool:
        """Receive one provider-specific delivery and dispatch its listeners."""
        delivery = await self._next()
        if delivery is None:
            return False
        entry, lease_seconds = delivery
        await self._dispatch(entry, lease_seconds)
        return True

    @abstractmethod
    async def _next(self) -> tuple[QueueEntry, float | None] | None:
        """Receive one event using this worker's provider-specific delivery."""
        raise NotImplementedError

    async def _dispatch(
        self, entry: QueueEntry, lease_seconds: float | None = None
    ) -> None:
        renewal_task = (
            asyncio.create_task(self._renew_claim(entry, lease_seconds))
            if lease_seconds is not None
            else None
        )
        try:
            registrations = listeners_for(self._alias)
            for index, registration in self._rotated(registrations):
                self._cursor = index
                try:
                    if registration.filter is not None and not await self._invoke(
                        registration.filter, entry
                    ):
                        continue
                    result = await self._invoke(registration.callback, entry)
                except Exception:
                    logger.exception(
                        "Event listener failed; releasing event for retry",
                        extra={
                            "queue": self._queue.queue_name,
                            "entry_id": str(entry.id),
                        },
                    )
                    await self._release(entry)
                    return
                if (
                    renewal_task is not None
                    and renewal_task.done()
                    and not self._renewal_succeeded(renewal_task, entry)
                ):
                    return
                if result is True:
                    await self._remove(entry)
                    return
                if result is False:
                    logger.warning(
                        "Event listener rejected event",
                        extra={
                            "queue": self._queue.queue_name,
                            "entry_id": str(entry.id),
                        },
                    )
                    await self._remove(entry)
                    return
            await self._release(entry)
        except asyncio.CancelledError:
            await self._release(entry)
            raise
        finally:
            if renewal_task is not None:
                renewal_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await renewal_task

    def _renewal_succeeded(
        self, renewal_task: asyncio.Task[bool], entry: QueueEntry
    ) -> bool:
        """Return whether a completed renewal task retained this event's lease."""
        try:
            return renewal_task.result()
        except asyncio.CancelledError:
            logger.warning("Event claim renewal was cancelled for %s", entry.id)
        except Exception:
            logger.exception("Event claim renewal failed for %s", entry.id)
        return False

    async def _renew_claim(self, entry: QueueEntry, lease_seconds: float) -> bool:
        """Renew a provider-specific delivery lease while a listener runs."""
        raise RuntimeError("Lease renewal requires a provider-specific event worker")

    def _rotated(
        self, registrations: tuple[ListenerRegistration, ...]
    ) -> tuple[tuple[int, ListenerRegistration], ...]:
        if not registrations:
            return ()
        start = (self._cursor + 1) % len(registrations)
        return tuple(
            ((start + offset) % len(registrations), registration)
            for offset, registration in enumerate(
                registrations[start:] + registrations[:start]
            )
        )

    @abstractmethod
    async def _release(self, entry: QueueEntry) -> None:
        """Return an unhandled event through provider-specific delivery."""
        raise NotImplementedError

    @abstractmethod
    async def _remove(self, entry: QueueEntry) -> None:
        """Consume an event through provider-specific delivery."""
        raise NotImplementedError

    async def _invoke(self, callback: Any, entry: QueueEntry) -> Any:
        if inspect.iscoroutinefunction(callback):
            return await callback(entry)
        result = await sync_to_async(callback)(entry)
        if inspect.isawaitable(result):
            return await result
        return result
