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


class _DispatcherStop:
    """Sentinel enqueued to unblock `_run_dispatcher`'s blocking `events.get()`."""


_DISPATCHER_STOP = _DispatcherStop()


class _QueueObservers:
    """Local observer state for one alias, shared across all its AsyncQueue instances."""

    def __init__(self, queue: AsyncQueue) -> None:
        self.queue = queue
        self.lock = threading.RLock()
        self.registrations: list[_Registration] = []
        self.events: Queue[_Delivery | _DispatcherStop] = Queue(
            maxsize=_EVENT_QUEUE_SIZE
        )
        self.drop_logged = False
        self.sequence = 0
        self.dispatcher: threading.Thread | None = None

    def register(self, registration: _Registration) -> None:
        with self.lock:
            registration.started_at_sequence = self.sequence
            self.registrations.append(registration)
            if self.dispatcher is None:
                self.dispatcher = threading.Thread(
                    target=self._run_dispatcher,
                    name=f"django-queues-observers-{self.queue.queue_name}",
                    daemon=True,
                )
                self.dispatcher.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop this alias's dispatcher thread, if one is running.

        Enqueues a stop sentinel to unblock the dispatcher's blocking
        `events.get()` and joins the thread. Primarily for callers that
        discard this `_QueueObservers` entirely (e.g. test teardown popping
        an alias from `_observers_by_alias` between runs) -- a running
        application never discards an alias's observer state, since one
        dispatcher per alias is meant to live for the process lifetime.
        """
        with self.lock:
            dispatcher = self.dispatcher
        if dispatcher is None:
            return
        try:
            self.events.put(_DISPATCHER_STOP, timeout=timeout)
        except Full:
            logger.warning(
                "Queue lifecycle observer delivery queue is full; "
                "could not enqueue dispatcher stop sentinel",
                extra={"queue": self.queue.queue_name},
            )
            return
        dispatcher.join(timeout=timeout)

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

    def _run_dispatcher(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            self._dispatch_forever(loop)
        finally:
            loop.close()

    def _dispatch_forever(self, loop: asyncio.AbstractEventLoop) -> None:
        while True:
            delivery = self.events.get()
            if isinstance(delivery, _DispatcherStop):
                return
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


_observers_by_alias: dict[str, _QueueObservers] = {}
_observers_by_alias_lock = threading.RLock()


def _observers_for(queue: AsyncQueue) -> _QueueObservers:
    """Return the observer state owned by *queue*'s alias, creating it once.

    Keyed by alias rather than by queue instance: QueueRegistry's default
    thread-local connection_scope gives distinct threads distinct instances
    for the same alias, and registration/dispatch state must be shared
    across all of them for one alias to have exactly one observer runtime.
    """
    with _observers_by_alias_lock:
        alias = queue.queue_name
        if alias not in _observers_by_alias:
            _observers_by_alias[alias] = _QueueObservers(queue)
        return _observers_by_alias[alias]


def _discard_observers_for(alias: str) -> None:
    """Remove and stop an alias's `_QueueObservers`, if one exists.

    The one correct way to discard an alias's observer state: popping
    `_observers_by_alias[alias]` directly leaves its dispatcher thread
    running forever with nothing left able to reach or stop it, since
    `_run_dispatcher` blocks on `events.get()` with no other exit path.
    A running application never needs this -- one alias's `_QueueObservers`
    lives for the process lifetime -- but test teardown resetting state
    between parametrized runs does, to avoid leaking a dispatcher thread
    per reset.
    """
    with _observers_by_alias_lock:
        observers = _observers_by_alias.pop(alias, None)
    if observers is not None:
        observers.stop()


def _alias_has_observer_registration(alias: str) -> bool:
    """Return whether *alias* has a live or pending registration.

    Used by the runtime to decide whether an AsyncQueue alias needs a
    receiver task. Deliberately does not call `_observers_for`, which would
    create an empty `_QueueObservers` entry as a side effect for every
    configured alias the runtime ever looks at, defeating "no-op when
    nothing is registered".
    """
    with _observers_by_alias_lock:
        observers = _observers_by_alias.get(alias)
    if observers is not None:
        with observers.lock:
            if observers.registrations:
                return True
    with _pending_by_alias_lock:
        pending = _pending_by_alias.get(alias)
        return pending is not None and any(
            not entry.cancelled and not entry.activating and entry.subscription is None
            for entry in pending
        )


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


@dataclass(eq=False, slots=True)
class _PendingRegistration:
    """A decorator-registered observer awaiting runtime activation."""

    callback: Observer
    entry_id: UUID | None
    cancelled: bool = False
    activating: bool = False
    subscription: QueueSubscription | None = None


class DecoratorSubscription:
    """Unsubscribe handle for a decorator-registered observer.

    Usable immediately at decoration time, before any real `QueueSubscription`
    exists. Unsubscribing before activation cancels the pending registration
    so the runtime skips it; unsubscribing after activation delegates to the
    real subscription the runtime created.
    """

    __slots__ = ("_pending",)

    def __init__(self, pending: _PendingRegistration) -> None:
        self._pending = pending

    def unsubscribe(self) -> None:
        with _pending_by_alias_lock:
            if self._pending.subscription is None:
                self._pending.cancelled = True
                return
        self._pending.subscription.unsubscribe()


_pending_by_alias: dict[str, list[_PendingRegistration]] = {}
_pending_by_alias_lock = threading.RLock()


def _register_now(
    queue_name: str,
    callback: Observer,
    entry_id: UUID | None,
    configured_queue: AsyncQueue | None = None,
) -> QueueSubscription:
    """Register and activate an observer immediately (today's behaviour).

    `configured_queue`, when given, is used as-is instead of being resolved
    via `_queue_for_name`/`queues[queue_name]`. `_activate_pending_for`
    always supplies it, because it already has the just-built queue instance
    in hand from `QueueRuntime._classify` -- re-resolving through the
    registry there would call back into `QueueRegistry.create_connection`
    for the same alias while its own call is still in progress (it has not
    cached the alias yet), building a second queue instance and firing
    `queue_created` a second time. The direct-call path (`queue_observer(name,
    callback)`) has no instance in hand and passes nothing, so it keeps
    resolving `queue_name` normally.
    """
    if configured_queue is None:
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

    # A direct registration can happen at any time, not just before the
    # runtime starts, so it must (re-)notify the runtime that this alias now
    # has a registration -- otherwise a receiver task is never scheduled for
    # it. start_one already idempotently guards per-alias task creation.
    from django_queue.queue_runtime import queue_runtime

    queue_runtime.start_one(queue_name, configured_queue)
    return subscription


def _register_deferred(
    queue_name: str, callback: Observer, entry_id: UUID | None
) -> DecoratorSubscription:
    """Record an observer without querying any backend or opening a connection.

    Activation happens later, when the process-wide runtime starts and calls
    `_activate_pending_for` for this alias.

    KNOWN GAP: if `queue_name` was already resolved and cached before this
    decorator ever ran (e.g. a view module Django imports lazily, on first
    request, well after `ready()`'s eager startup walk already resolved
    every alias), nothing revisits that alias again -- `create_connection`
    only runs on a cache miss -- so this registration stays pending forever
    and silently never receives snapshots. Not yet fixed: an earlier attempt
    to activate inline here (checking whether `queue_name` was already
    cached) made this function's "record only, no I/O, no activation"
    contract conditional, which broke tests that rely on activation being
    fully explicit and deferred (e.g. tests controlling activation timing
    directly via `_activate_pending_for`). Needs a fix that doesn't change
    `_register_deferred` itself -- likely a way for the runtime or registry
    to detect and revisit stranded pending registrations without coupling
    decoration to activation.
    """
    pending = _PendingRegistration(callback, entry_id)
    with _pending_by_alias_lock:
        _pending_by_alias.setdefault(queue_name, []).append(pending)
    return DecoratorSubscription(pending)


def _activate_pending_for(queue_name: str, configured_queue: AsyncQueue) -> None:
    """Activate every not-yet-activated, non-cancelled registration for one alias.

    `configured_queue` must be the already-built instance for `queue_name`
    (`QueueRuntime._classify` always has one in hand at its one call site) and
    is passed straight through to `_register_now`, never re-resolved via
    `queues[queue_name]`. Re-resolving here would call back into
    `QueueRegistry.create_connection` for the same alias while its own call
    is still on the stack -- `create_connection` calls `start_one` (which
    reaches this function) *before* Django caches the alias, so a reentrant
    lookup builds a second queue instance and fires `queue_created` a second
    time rather than reusing the one already under construction.

    Marks each entry `activating` before calling `_register_now`, which
    itself calls back into `QueueRuntime.start_one` -> `_classify` ->
    `_activate_pending_for` for the same alias: without the flag, that
    re-entrant call would see the same not-yet-`subscription`-bearing entry
    as still needing activation and recurse indefinitely.

    The check-and-set of `activating` happens under `_pending_by_alias_lock`
    so two threads racing to activate the same alias (e.g. both resolving it
    for the first time via `create_connection`) cannot both win the check and
    both call `_register_now` for the same pending entry -- that would
    double-register the callback and leak the loser's `QueueSubscription`
    unreachably once `pending.subscription` is overwritten. `_register_now`
    itself stays outside the lock: it does blocking I/O and reenters this
    function for the same alias.

    Resets `activating` under the same lock in a `finally`, whether or not
    `_register_now` raised -- otherwise a failed activation (e.g. the
    backend's `list()` call fails) would leave `activating` permanently
    `True`, and every later activation attempt for that alias would skip the
    entry forever, silently dropping the registration. If the registration
    was unsubscribed (`cancelled`) while `_register_now` was in flight, the
    freshly created subscription is torn down immediately rather than left
    live and unreachable from `DecoratorSubscription`.
    """
    with _pending_by_alias_lock:
        pending_entries = list(_pending_by_alias.get(queue_name, ()))
    to_activate = []
    for pending in pending_entries:
        with _pending_by_alias_lock:
            if (
                pending.cancelled
                or pending.activating
                or pending.subscription is not None
            ):
                continue
            pending.activating = True
        to_activate.append(pending)
    for pending in to_activate:
        subscription = None
        try:
            subscription = _register_now(
                queue_name, pending.callback, pending.entry_id, configured_queue
            )
        finally:
            with _pending_by_alias_lock:
                pending.activating = False
                if subscription is not None:
                    pending.subscription = subscription
                cancelled = pending.cancelled
        if cancelled and subscription is not None:
            subscription.unsubscribe()


def queue_observer(
    queue_name: str, callback: Observer | None = None, *, entry_id: UUID | None = None
) -> QueueSubscription | Callable[[Observer], Observer]:
    """Observe retained and future lifecycle snapshots for an AsyncQueue.

    Observers are passive and local to this Django process. Called directly
    with a callback, registration and activation happen immediately. Used as
    a decorator factory (`@queue_observer(queue_name)`), registration is
    recorded without any I/O and activation is deferred until the process-wide
    runtime starts.
    """
    if not isinstance(queue_name, str) or not queue_name:
        raise ValueError("queue_name must be a non-empty string")
    if entry_id is not None and not isinstance(entry_id, UUID):
        raise TypeError("entry_id must be a UUID or None")

    if callback is not None:
        if not callable(callback):
            raise TypeError("callback must be callable")
        return _register_now(queue_name, callback, entry_id)

    def decorator(fn: Any) -> Any:
        if not callable(fn):
            raise TypeError("queue observers must be callable")
        fn._queue_observer_subscription = _register_deferred(queue_name, fn, entry_id)
        return fn

    return decorator
