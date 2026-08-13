"""Passive, process-local task lifecycle observation."""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from queue import Full, Queue
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async

from django_queue.backends.base import AsyncQueue
from django_queue.entries import QueueEntry

logger = logging.getLogger(__name__)

Observer = Callable[[QueueEntry], Any]
_EVENT_QUEUE_SIZE = 128


@dataclass(eq=False, slots=True)
class _Registration:
    callback: Observer
    entry_id: UUID | None
    active: bool = True
    initialising: bool = True
    pending: list[QueueEntry] = field(default_factory=list)


@dataclass(slots=True)
class QueueSubscription:
    """A local lifecycle-observer registration."""

    _queue: AsyncQueue
    _queue_name: str
    _registration: _Registration
    _active: bool = True

    def unsubscribe(self) -> None:
        """Stop this observer from receiving future lifecycle snapshots."""
        if self._active:
            _runtime.unregister(self._queue_name, self._registration)
            self._active = False


class _ObserverRuntime:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._registrations: dict[str, list[_Registration]] = {}
        self._redis_receivers: set[str] = set()
        self._events: Queue[tuple[str, QueueEntry, _Registration]] = Queue(
            maxsize=_EVENT_QUEUE_SIZE
        )
        self._drop_logged = False
        self._thread: threading.Thread | None = None

    def register(
        self, queue_name: str, registration: _Registration, configured_queue: AsyncQueue
    ) -> None:
        with self._lock:
            self._registrations.setdefault(queue_name, []).append(registration)
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="django-queue-observers", daemon=True
                )
                self._thread.start()
            receiver = _receiver_for(configured_queue)
            if receiver is not None and queue_name not in self._redis_receivers:
                self._redis_receivers.add(queue_name)
                thread = threading.Thread(
                    target=self._run_receiver,
                    args=(queue_name, receiver),
                    name=f"django-queue-observer-{queue_name}",
                    daemon=True,
                )
                thread.start()

    def _run_receiver(
        self, queue_name: str, receiver: Callable[[Observer], None]
    ) -> None:
        try:
            receiver(self.publish)
            logger.warning(
                "Queue lifecycle receiver stopped",
                extra={"queue": queue_name},
            )
        except Exception:
            logger.exception(
                "Queue lifecycle receiver failed",
                extra={"queue": queue_name},
            )
        finally:
            with self._lock:
                self._redis_receivers.discard(queue_name)

    def unregister(self, queue_name: str, registration: _Registration) -> None:
        with self._lock:
            registration.active = False
            registrations = self._registrations.get(queue_name, [])
            for index, registered in enumerate(registrations):
                if registered is registration:
                    registrations.pop(index)
                    break

    def publish(self, entry: QueueEntry) -> None:
        with self._lock:
            registrations = tuple(self._registrations.get(entry.queue, ()))
            for registration in registrations:
                if registration.initialising:
                    registration.pending.append(entry)
                else:
                    self._queue_event(entry.queue, entry, registration)

    def activate(
        self,
        queue_name: str,
        registration: _Registration,
        snapshots: list[QueueEntry],
    ) -> None:
        """Queue an ordered bootstrap batch, then enable ordinary delivery."""
        with self._lock:
            snapshots.extend(registration.pending)
            registration.pending.clear()
            registration.initialising = False
            for entry in _order_snapshots(snapshots):
                self._queue_event(queue_name, entry, registration)

    def _queue_event(
        self, queue_name: str, entry: QueueEntry, registration: _Registration
    ) -> None:
        """Queue best-effort observer delivery without blocking queue workers."""
        try:
            self._events.put_nowait((queue_name, entry, registration))
        except Full:
            if not self._drop_logged:
                logger.warning(
                    "Queue lifecycle observer delivery queue is full; dropping snapshots",
                    extra={"queue": queue_name, "capacity": _EVENT_QUEUE_SIZE},
                )
                self._drop_logged = True

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while True:
            queue_name, entry, registration = self._events.get()
            if not registration.active:
                continue
            if registration.entry_id is not None and registration.entry_id != entry.id:
                continue
            try:
                if inspect.iscoroutinefunction(registration.callback):
                    result = registration.callback(entry)
                else:
                    result = loop.run_until_complete(
                        sync_to_async(registration.callback)(entry)
                    )
                if inspect.isawaitable(result):
                    loop.run_until_complete(result)
            except Exception:
                logger.exception(
                    "Queue lifecycle observer failed",
                    extra={"queue": queue_name, "entry_id": str(entry.id)},
                )


_runtime = _ObserverRuntime()


def _state_timestamp(entry: QueueEntry) -> object:
    """Return the persisted timestamp identifying this lifecycle snapshot."""
    return entry.finished_at or entry.dispatched_at or entry.queued_at


def _receiver_for(configured_queue: AsyncQueue):
    """Return the internal cross-process transport for a supported backend."""
    try:
        from django_queue.backends.redis.redisqueue import (
            RedisLifecycleObserverTransport,
            RedisQueue,
        )
    except ImportError:
        return None
    if isinstance(configured_queue, RedisQueue):
        return RedisLifecycleObserverTransport(configured_queue).receive
    return None


def _order_snapshots(snapshots: list[QueueEntry]) -> list[QueueEntry]:
    """Preserve each entry's lifecycle order within a bootstrap batch."""
    order = {
        "queued": 0,
        "running": 1,
        "succeeded": 2,
        "failed": 2,
        "cancelled": 2,
        "timeout": 2,
    }
    return sorted(
        snapshots,
        key=lambda entry: (
            entry.id,
            order[entry.status.value],
            _state_timestamp(entry),
        ),
    )


def _queue_for_name(queue_name: str) -> AsyncQueue:
    """Resolve a configured queue lazily, after Django has initialised it."""
    from django_queue import queues

    return queues[queue_name]


def publish_snapshot(entry: QueueEntry) -> None:
    """Submit a persisted task-entry snapshot to local observers."""
    _runtime.publish(entry)


def queue_observer(
    queue_name: str, callback: Observer, *, entry_id: UUID | None = None
) -> QueueSubscription:
    """Observe retained and future lifecycle snapshots for an AsyncQueue.

    Observers are passive and local to this Django process.
    """
    if not isinstance(queue_name, str) or not queue_name:
        raise ValueError("queue_name must be a non-empty string")
    if not callable(callback):
        raise TypeError("callback must be callable")
    if entry_id is not None and not isinstance(entry_id, UUID):
        raise TypeError("entry_id must be a UUID or None")

    configured_queue = _queue_for_name(queue_name)
    if not isinstance(configured_queue, AsyncQueue):
        raise TypeError("queue_observer only supports AsyncQueue instances")

    registration = _Registration(callback, entry_id)
    observer_queue_name = configured_queue.queue_name
    _runtime.register(observer_queue_name, registration, configured_queue)
    subscription = QueueSubscription(
        configured_queue, observer_queue_name, registration
    )
    try:
        snapshots = configured_queue.list_entries()
    except Exception:
        _runtime.unregister(observer_queue_name, registration)
        raise
    _runtime.activate(observer_queue_name, registration, snapshots)
    return subscription
