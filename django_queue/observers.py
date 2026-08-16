"""Passive, process-local async-queue lifecycle observation."""

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
    started_at_sequence: int = 0
    bootstrapped_through_sequence: int = 0


@dataclass(frozen=True, slots=True)
class _Delivery:
    entry: QueueEntry
    target: _Registration | None = None
    sequence: int = 0


class _QueueObservers:
    """Local observer state owned by one AsyncQueue instance."""

    def __init__(self, queue: AsyncQueue) -> None:
        self.queue = queue
        self.lock = threading.RLock()
        self.registrations: list[_Registration] = []
        self.events: Queue[_Delivery] = Queue(maxsize=_EVENT_QUEUE_SIZE)
        self.drop_logged = False
        self.sequence = 0
        self.dispatcher: threading.Thread | None = None
        self.receiver: threading.Thread | None = None

    def register(self, registration: _Registration) -> None:
        with self.lock:
            registration.started_at_sequence = self.sequence
            self.registrations.append(registration)
            if self.dispatcher is None:
                self.dispatcher = threading.Thread(
                    target=self._run_dispatcher,
                    name=f"django-queue-observers-{self.queue.queue_name}",
                    daemon=True,
                )
                self.dispatcher.start()
            if self.receiver is None:
                receiver = self.queue._observer_receiver(self.publish)
                if receiver is not None:
                    self.receiver = threading.Thread(
                        target=self._run_receiver,
                        args=(receiver,),
                        name=f"django-queue-observer-{self.queue.queue_name}",
                        daemon=True,
                    )
                    self.receiver.start()

    def unregister(self, registration: _Registration) -> None:
        with self.lock:
            registration.active = False
            for index, registered in enumerate(self.registrations):
                if registered is registration:
                    self.registrations.pop(index)
                    break

    def publish(self, entry: QueueEntry) -> None:
        """Snapshot registrations under lock, then publish without it."""
        with self.lock:
            self.sequence += 1
            delivery = _Delivery(entry, sequence=self.sequence)
            registrations = tuple(self.registrations)
            for registration in registrations:
                if registration.initialising:
                    registration.pending.append(entry)
        if any(not registration.initialising for registration in registrations):
            self._queue_event(delivery)

    def activate(
        self, registration: _Registration, snapshots: list[QueueEntry]
    ) -> None:
        """Queue an ordered bootstrap batch, then enable ordinary delivery."""
        with self.lock:
            snapshots.extend(registration.pending)
            registration.pending.clear()
            registration.bootstrapped_through_sequence = self.sequence
            registration.initialising = False
        for entry in _order_snapshots(snapshots):
            self._queue_event(_Delivery(entry, target=registration))

    def _queue_event(self, delivery: _Delivery) -> None:
        """Queue best-effort observer delivery without blocking queue workers."""
        try:
            self.events.put_nowait(delivery)
        except Full:
            if not self.drop_logged:
                logger.warning(
                    "Queue lifecycle observer delivery queue is full; dropping snapshots",
                    extra={
                        "queue": self.queue.queue_name,
                        "capacity": _EVENT_QUEUE_SIZE,
                    },
                )
                self.drop_logged = True

    def _run_receiver(self, receiver: Callable[[], None]) -> None:
        try:
            receiver()
            logger.warning(
                "Queue lifecycle receiver stopped",
                extra={"queue": self.queue.queue_name},
            )
        except Exception:
            logger.exception(
                "Queue lifecycle receiver failed",
                extra={"queue": self.queue.queue_name},
            )
        finally:
            with self.lock:
                self.receiver = None

    def _run_dispatcher(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while True:
            delivery = self.events.get()
            if delivery.target is None:
                with self.lock:
                    registrations = tuple(self.registrations)
            else:
                registrations = (delivery.target,)
            for registration in registrations:
                if not registration.active:
                    continue
                if registration.initialising:
                    continue
                if (
                    delivery.target is None
                    and registration.started_at_sequence
                    < delivery.sequence
                    <= registration.bootstrapped_through_sequence
                ):
                    continue
                if (
                    registration.entry_id is not None
                    and registration.entry_id != delivery.entry.id
                ):
                    continue
                try:
                    if inspect.iscoroutinefunction(registration.callback):
                        result = registration.callback(delivery.entry)
                    else:
                        result = loop.run_until_complete(
                            sync_to_async(registration.callback)(delivery.entry)
                        )
                    if inspect.isawaitable(result):
                        loop.run_until_complete(result)
                except Exception:
                    logger.exception(
                        "Queue lifecycle observer failed",
                        extra={
                            "queue": self.queue.queue_name,
                            "entry_id": str(delivery.entry.id),
                        },
                    )


@dataclass(slots=True)
class QueueSubscription:
    """A local lifecycle-observer registration."""

    _queue: AsyncQueue
    _registration: _Registration
    _active: bool = True

    def unsubscribe(self) -> None:
        """Stop this observer from receiving future lifecycle snapshots."""
        if self._active:
            _observers_for(self._queue).unregister(self._registration)
            self._active = False


def _observers_for(queue: AsyncQueue) -> _QueueObservers:
    """Return the observer state owned by *queue*, creating it once."""
    with queue._lifecycle_observer_lock:
        if queue._lifecycle_observers is None:
            queue._lifecycle_observers = _QueueObservers(queue)
        return queue._lifecycle_observers


def _state_timestamp(entry: QueueEntry) -> object:
    """Return the persisted timestamp identifying this lifecycle snapshot."""
    return entry.finished_at or entry.dispatched_at or entry.queued_at


def _order_snapshots(snapshots: list[QueueEntry]) -> list[QueueEntry]:
    """Preserve each entry's lifecycle order within a bootstrap batch."""
    order = {
        "queued": 0,
        "running": 1,
        "succeeded": 2,
        "failed": 2,
        "cancelled": 2,
        "timeout": 2,
        "terminated": 3,
    }
    return sorted(
        snapshots,
        key=lambda entry: (
            entry.id,
            order[entry.status],
            _state_timestamp(entry),
        ),
    )


def _queue_for_name(queue_name: str) -> AsyncQueue:
    """Resolve a configured queue lazily, after Django has initialised it."""
    from django_queue import queues

    return queues[queue_name]


def publish(queue: AsyncQueue, entry: QueueEntry) -> None:
    """Submit a lifecycle snapshot to one queue's local observers.

    A final ``terminated`` snapshot is observer-only and is never retained.
    """
    _observers_for(queue).publish(entry)


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
    observers = _observers_for(configured_queue)
    observers.register(registration)
    subscription = QueueSubscription(configured_queue, registration)
    try:
        snapshots = configured_queue.list()
    except Exception:
        observers.unregister(registration)
        raise
    observers.activate(registration, snapshots)
    return subscription
