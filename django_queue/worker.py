"""Reusable asynchronous dispatcher for identified generic queue entries."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import uuid
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any, Protocol
from uuid import UUID

from django_queue.backends.base import AsyncQueue, BaseQueue
from django_queue.backends.exceptions import (
    QueueClaimConflictError,
    QueueEmptyException,
    QueueEntryMissingError,
    QueueEntryNotFoundError,
)
from django_queue.clock import (
    DEFAULT_CLOCK,
    ClockTime,
    QueueClock,
    QueueClockError,
    elapsed_time,
)
from django_queue.entries import (
    QueueEntry,
    QueueEntryStatus,
    validate_budget,
    validate_json_value,
)

logger = logging.getLogger(__name__)

WORKER_EVENT_MESSAGES: Mapping[str, str] = {
    "started": "Queue worker started",
    "dispatch_started": "Queue worker began dispatching an entry",
    "terminal_recorded": "Queue worker recorded a terminal outcome",
    "stopped": "Queue worker stopped",
}

_CLAIM_CONFLICT_LOG_INTERVAL_SECONDS = 60

# A coroutine rather than any awaitable: the worker schedules handlers with
# asyncio.create_task, and runqueues already rejects non-coroutine handlers.
Handler = Callable[[QueueEntry], Coroutine[Any, Any, object]]

# The budget that governs when nothing else specifies one. There is no value
# meaning unlimited: an unbounded handler is the defect the budget removes.
DEFAULT_TIMEOUT_SECONDS = 600


@dataclass(slots=True)
class _ActiveTimeout:
    timeout: asyncio.Timeout
    budget: float
    active: bool = True


_active_timeout: ContextVar[_ActiveTimeout | None] = ContextVar(
    "django_queue_active_timeout", default=None
)


def heartbeat() -> None:
    """Restart the current handler's execution budget after real progress."""
    active_timeout = _active_timeout.get()
    if (
        active_timeout is None
        or not active_timeout.active
        or active_timeout.timeout.expired()
    ):
        raise RuntimeError("Queue heartbeat requires an active handler dispatch")
    active_timeout.timeout.reschedule(
        asyncio.get_running_loop().time() + active_timeout.budget
    )


class QueueLookup(Protocol):
    """Queue service lookup used by a worker's registered aliases."""

    # Positional-only: subscripting never passes the key by keyword, and a plain
    # dict would not otherwise satisfy this protocol.
    def __getitem__(self, alias: str, /) -> BaseQueue: ...


class QueuePersistenceError(RuntimeError):
    """A terminal queue outcome could not be stored safely."""


class BaseQueueWorker:
    """Common local worker identity and idle-loop state."""

    provider_kind = "generic"
    provider_type = "generic"

    def __init__(
        self, *, idle_delay: float = 0.1, worker_id: UUID | None = None
    ) -> None:
        self._idle_delay = idle_delay
        self._worker_id = uuid.uuid7() if worker_id is None else worker_id
        self._running = False

    @property
    def running(self) -> bool:
        return self._running


@dataclass(frozen=True, slots=True)
class WorkerSnapshot:
    """Immutable, process-local state for an asynchronous queue worker.

    The queue clock determines ``running_for``. A synchronous inspection of a
    running Redis-backed worker can therefore calibrate its Redis clock.
    """

    worker_id: UUID
    running: bool
    started_at: ClockTime | None
    running_for: float | None
    active_entry_id: UUID | None
    active_queue_name: str | None
    queue_names: tuple[str, ...]
    dispatch_count: int
    succeeded_count: int
    failed_count: int
    cancelled_count: int
    timed_out_count: int


class AsyncQueueWorker(BaseQueueWorker):
    """Sequentially process registered queues until its task is cancelled."""

    def __init__(
        self,
        queues: QueueLookup,
        handlers: Mapping[str, Handler],
        *,
        idle_delay: float = 0.1,
        cancellation_grace_period: float = 30,
        clock: QueueClock | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        if timeout_seconds is not None:
            validate_budget(timeout_seconds)
        if type(cancellation_grace_period) not in (int, float) or (
            not math.isfinite(cancellation_grace_period)
            or cancellation_grace_period < 0
        ):
            raise ValueError(
                "Cancellation grace period must be a finite non-negative number of seconds"
            )
        super().__init__(idle_delay=idle_delay)
        self._timeout_seconds = timeout_seconds
        self._clock: QueueClock = clock or DEFAULT_CLOCK
        self._queues = {}
        for name in handlers:
            queue = queues[name]
            if not isinstance(queue, AsyncQueue):
                raise TypeError("AsyncQueueWorker requires AsyncQueue instances")
            if self.provider_kind != queue.worker_provider_kind:
                raise TypeError(
                    f"{type(queue).__name__} requires a "
                    f"{queue.worker_provider_kind} worker"
                )
            if not queue._worker_class_is_compatible(type(self)):
                raise TypeError(
                    f"{type(self).__name__} is not compatible with "
                    f"{type(queue).__name__}"
                )
            self._queues[name] = queue
        self._handlers = dict(handlers)
        self._cancellation_grace_period = cancellation_grace_period
        self._started_at: ClockTime | None = None
        self._stopped_at: ClockTime | None = None
        self._active_entry_id: UUID | None = None
        self._active_queue_name: str | None = None
        self._dispatch_count = 0
        self._succeeded_count = 0
        self._failed_count = 0
        self._cancelled_count = 0
        self._timed_out_count = 0
        self._last_recovery_at: dict[AsyncQueue, float] = {}
        self._last_retention_cleanup_at: dict[AsyncQueue, float] = {}
        self._last_first_seen_scan_at: dict[AsyncQueue, float] = {}
        self._last_observed_entry_id: dict[AsyncQueue, UUID] = {}
        self._last_claim_conflict_at: dict[UUID, float] = {}

    @property
    def clock(self) -> QueueClock:
        """Return the clock this worker times itself on.

        Supplied by the queue that created it, so a worker's recorded time and
        the entries it dispatches share one basis.
        """
        return self._clock

    def budget_for(self, queue: AsyncQueue, entry: QueueEntry) -> float:
        """Return the execution budget governing this entry, in seconds.

        A worker override wins over the entry's own budget, which wins over the
        queue default, which falls back to `DEFAULT_TIMEOUT_SECONDS`. The worker
        takes precedence because it is the component that knows the runtime it
        is actually operating in.
        """
        for budget in (
            self._timeout_seconds,
            entry.timeout_seconds,
            queue.timeout_seconds,
        ):
            if budget is not None:
                return budget
        return DEFAULT_TIMEOUT_SECONDS

    @property
    def snapshot(self) -> WorkerSnapshot:
        """Return an immutable snapshot of this worker's local state."""
        return WorkerSnapshot(
            worker_id=self._worker_id,
            running=self.running,
            started_at=self._started_at,
            running_for=self._running_for(),
            active_entry_id=self._active_entry_id,
            active_queue_name=self._active_queue_name,
            queue_names=tuple(self._queues),
            dispatch_count=self._dispatch_count,
            succeeded_count=self._succeeded_count,
            failed_count=self._failed_count,
            cancelled_count=self._cancelled_count,
            timed_out_count=self._timed_out_count,
        )

    def _running_for(self) -> float | None:
        """Seconds this worker has been running, or ran before it stopped.

        Measured on the worker's own clock, so a reader needs no second source
        of time; frozen at the stop instant, since a stopped worker whose
        runtime keeps growing is simply wrong. Absent if the clock has moved
        back behind the start, which a recalibrated Redis clock may do.
        """
        if self._started_at is None:
            return None
        try:
            ended_at = self._stopped_at or self._clock.now()
        except QueueClockError:
            return None
        return elapsed_time(self._started_at, ended_at)

    async def run(self) -> None:
        """Dispatch registered queue entries until the caller cancels this task."""
        self._running = True
        # Start before clearing the stop: a reader caught between these sees a
        # fresh start with a stale stop -- at worst a short duration -- rather
        # than the previous run's start with no stop at all.
        self._started_at = await self._clock.anow()
        self._stopped_at = None
        self._log_state_change("started")
        try:
            while True:
                dispatched = False
                for name, queue in self._queues.items():
                    await self._publish_new(queue)
                    await self._prune_expired(queue)
                    try:
                        next_entry = await self._next(queue)
                    except QueueEmptyException:
                        continue
                    except QueueClaimConflictError as exc:
                        self._log_claim_conflict(exc.entry_id)
                        continue
                    except QueueEntryMissingError as exc:
                        await self._discard_missing(queue, exc.entry_id)
                        continue
                    except QueueEntryNotFoundError as exc:
                        self._log_missing_entry(exc.entry_id)
                        continue
                    if next_entry is None:
                        continue
                    entry, lease_seconds = next_entry
                    dispatched = True
                    self._active_entry_id = entry.id
                    self._active_queue_name = name
                    self._dispatch_count += 1
                    self._log_state_change("dispatch_started")
                    try:
                        await self._dispatch(
                            queue, self._handlers[name], entry, lease_seconds
                        )
                    except QueueEntryNotFoundError as exc:
                        self._log_missing_entry(exc.entry_id)
                        if lease_seconds is not None:
                            await self._discard_missing(queue, exc.entry_id)
                if not dispatched:
                    await asyncio.sleep(self._idle_delay)
        finally:
            self._running = False
            self._stopped_at = await self._clock.anow()
            self._active_entry_id = None
            self._active_queue_name = None
            self._log_state_change("stopped")

    async def _publish_new(self, queue: AsyncQueue) -> None:
        """Publish snapshots beyond this worker's completed-scan UUIDv7 cursor."""
        now = asyncio.get_running_loop().time()
        if now - self._last_first_seen_scan_at.get(queue, float("-inf")) < 1:
            return
        self._last_first_seen_scan_at[queue] = now
        entries = await queue.alist()
        previous_entry_id = self._last_observed_entry_id.get(queue)
        new_entries = [
            entry
            for entry in entries
            if previous_entry_id is None or entry.id > previous_entry_id
        ]
        for entry in sorted(new_entries, key=lambda entry: entry.id):
            await queue.apublish(entry)
        if entries:
            largest_entry_id = max(entry.id for entry in entries)
            if previous_entry_id is None or largest_entry_id > previous_entry_id:
                self._last_observed_entry_id[queue] = largest_entry_id

    async def _prune_expired(self, queue: AsyncQueue) -> None:
        """Periodically remove expired AsyncQueue terminal records."""
        if queue.retention_timeout is None:
            return
        now = asyncio.get_running_loop().time()
        if now - self._last_retention_cleanup_at.get(queue, float("-inf")) < 1:
            return
        self._last_retention_cleanup_at[queue] = now
        await queue._aprune_expired()

    async def _next(self, queue: AsyncQueue) -> tuple[QueueEntry, float | None] | None:
        """Get one entry, claiming and leasing it where the backend supports it."""
        return await queue.adequeue(), None

    async def _discard_missing(self, queue: AsyncQueue, entry_id: UUID) -> None:
        """Drop a claimed ID whose durable entry record no longer exists."""
        logger.error("Discarded missing queue entry %s", entry_id)

    async def _renew_claim(
        self, queue: AsyncQueue, entry: QueueEntry, lease_seconds: float
    ) -> bool:
        """Renew a backend-specific claim lease.

        The generic worker never creates leases; a delivery-specific worker
        must override this alongside its claim implementation.
        """
        raise RuntimeError("Claim renewal requires a delivery-specific worker")

    async def _mark_running(
        self, queue: AsyncQueue, entry: QueueEntry
    ) -> QueueEntry | None:
        """Persist running state while a backend-specific claim is owned."""
        raise RuntimeError("Claim settlement requires a delivery-specific worker")

    async def _settle(self, queue: AsyncQueue, entry: QueueEntry) -> bool:
        """Atomically settle a backend-specific claim."""
        raise RuntimeError("Claim settlement requires a delivery-specific worker")

    @staticmethod
    def _log_missing_entry(entry_id: UUID) -> None:
        """Report an entry removed outside the queue lifecycle without stopping."""
        logger.error("Queue entry %s was unexpectedly removed", entry_id)

    async def _dispatch(
        self,
        queue: AsyncQueue,
        handler: Handler,
        entry: QueueEntry,
        lease_seconds: float | None = None,
    ) -> None:
        if not self._observed(queue, entry):
            await queue.apublish(entry)
        if lease_seconds is not None:
            running_entry = await self._mark_running(queue, entry)
            if running_entry is None:
                logger.warning(
                    "Lost claim for queue entry %s before dispatch", entry.id
                )
                return
            entry = running_entry
        else:
            entry = await queue._amark_running(entry.id)
        await queue.apublish(entry)
        timeout_seconds = self.budget_for(queue, entry)
        active_timeout: _ActiveTimeout | None = None
        timeout_token = None
        handler_task: asyncio.Task[object] | None = None
        renewal_task = (
            asyncio.create_task(self._renew_claim(queue, entry, lease_seconds))
            if lease_seconds is not None
            else None
        )
        claim_worker_id = self._worker_id if lease_seconds is not None else None
        try:
            # The budget cancels this await; the shield keeps the handler task
            # alive so the abandon path can stop it deliberately and report it.
            # An outer cancellation arrives as CancelledError instead, which is
            # how shutdown stays distinguishable from a budget that ran out.
            async with asyncio.timeout(timeout_seconds) as budget:
                active_timeout = _ActiveTimeout(budget, timeout_seconds)
                timeout_token = _active_timeout.set(active_timeout)
                handler_task = asyncio.create_task(handler(entry))
                done, _ = await asyncio.wait(
                    (handler_task, renewal_task)
                    if renewal_task is not None
                    else (handler_task,),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if (
                    renewal_task is not None
                    and renewal_task in done
                    and renewal_task.result() is False
                ):
                    handler_task.cancel()
                    handler_task.add_done_callback(
                        lambda task: self._log_late_handler_outcome(entry, task)
                    )
                    return
                result = await asyncio.shield(handler_task)
        except TimeoutError as exc:
            if active_timeout is None or handler_task is None:
                raise
            active_timeout.active = False
            # TimeoutError is asyncio.TimeoutError, so a handler that wraps its
            # own I/O in a deadline raises the same class this budget expires
            # with. Only the context knows which happened; without asking, an
            # ordinary handler failure would be recorded as never having
            # answered and its error discarded.
            if budget.expired():
                if renewal_task is not None:
                    await self._stop_renewal_task(renewal_task)
                await self._abandon_unresponsive_handler(
                    queue, entry, handler_task, claim_worker_id
                )
            else:
                if renewal_task is not None:
                    await self._stop_renewal_task(renewal_task)
                await self._record_failure(queue, entry, exc, claim_worker_id)
        except asyncio.CancelledError:
            if active_timeout is None or handler_task is None:
                raise
            active_timeout.active = False
            await self._finish_cancellation(queue, entry, handler_task, claim_worker_id)
            raise
        except Exception as exc:  # noqa: BLE001 - handlers may raise any application exception.
            if active_timeout is not None:
                active_timeout.active = False
            if renewal_task is not None:
                await self._stop_renewal_task(renewal_task)
            await self._record_failure(queue, entry, exc, claim_worker_id)
        else:
            if active_timeout is None:
                raise RuntimeError("Queue handler completed without an active timeout")
            active_timeout.active = False
            if renewal_task is not None:
                await self._stop_renewal_task(renewal_task)
            await self._record_result(queue, entry, result, claim_worker_id)
        finally:
            if active_timeout is not None:
                active_timeout.active = False
            if timeout_token is not None:
                _active_timeout.reset(timeout_token)
            if renewal_task is not None:
                await self._stop_renewal_task(renewal_task)
            self._active_entry_id = None
            self._active_queue_name = None

    def _observed(self, queue: AsyncQueue, entry: QueueEntry) -> bool:
        """Return whether an AsyncQueue scan already published this entry."""
        return (
            entry_id := self._last_observed_entry_id.get(queue)
        ) is not None and entry.id <= entry_id

    async def _stop_renewal_task(self, renewal_task: asyncio.Task[bool]) -> None:
        """Stop a claim heartbeat before the entry is settled."""
        renewal_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await renewal_task

    def _log_claim_conflict(self, entry_id: UUID) -> None:
        """Log each persistent claim conflict at a useful, bounded cadence."""
        now = asyncio.get_running_loop().time()
        previous = self._last_claim_conflict_at.get(entry_id)
        if previous is None or now - previous >= _CLAIM_CONFLICT_LOG_INTERVAL_SECONDS:
            logger.warning("Queue entry %s is already claimed", entry_id)
            self._last_claim_conflict_at[entry_id] = now

    async def _abandon_unresponsive_handler(
        self,
        queue: AsyncQueue,
        entry: QueueEntry,
        handler_task: asyncio.Task[object],
        claim_worker_id: UUID | None,
    ) -> QueueEntry:
        """Stop a handler that stopped answering and record it as timed out.

        Named for what its two callers have in common rather than for either
        deadline: a dispatch may exceed its execution budget, or a handler may
        ignore shutdown cancellation past the grace period. Neither is an
        orderly stop, which is the distinction `cancelled` would lose.
        """
        handler_task.cancel()
        handler_task.add_done_callback(
            lambda task: self._log_late_handler_outcome(entry, task)
        )
        if claim_worker_id is not None:
            return await self._settle_terminal(
                queue, entry, claim_worker_id, QueueEntryStatus.TIMEOUT
            )
        return await self._publish_terminal(queue, entry, queue._amark_timed_out)

    async def _finish_cancellation(
        self,
        queue: AsyncQueue,
        entry: QueueEntry,
        handler_task: asyncio.Task[object],
        claim_worker_id: UUID | None,
    ) -> QueueEntry:
        try:
            # asyncio.timeout rather than wait_for for the same reason as the
            # budget: only the context can tell the grace period running out
            # from a handler raising TimeoutError of its own while it winds up.
            async with asyncio.timeout(self._cancellation_grace_period) as grace:
                result = await asyncio.shield(handler_task)
        except TimeoutError as exc:
            if grace.expired():
                return await self._abandon_unresponsive_handler(
                    queue, entry, handler_task, claim_worker_id
                )
            else:
                return await self._record_failure(queue, entry, exc, claim_worker_id)
        except Exception as exc:  # noqa: BLE001 - handlers may raise any application exception.
            return await self._record_failure(queue, entry, exc, claim_worker_id)
        else:
            return await self._record_result(queue, entry, result, claim_worker_id)

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
        self,
        queue: AsyncQueue,
        entry: QueueEntry,
        result: object,
        claim_worker_id: UUID | None = None,
    ) -> QueueEntry:
        try:
            validate_json_value(result)
        except TypeError as exc:
            return await self._record_failure(queue, entry, exc, claim_worker_id)
        else:
            if claim_worker_id is not None:
                return await self._settle_terminal(
                    queue,
                    entry,
                    claim_worker_id,
                    QueueEntryStatus.SUCCEEDED,
                    result=result,
                )
            return await self._publish_terminal(
                queue, entry, queue._amark_succeeded, result
            )

    async def _record_failure(
        self,
        queue: AsyncQueue,
        entry: QueueEntry,
        error: Exception,
        claim_worker_id: UUID | None = None,
    ) -> QueueEntry:
        logger.exception("Queue handler failed for entry %s", entry.id)
        if claim_worker_id is not None:
            return await self._settle_terminal(
                queue,
                entry,
                claim_worker_id,
                QueueEntryStatus.FAILED,
                error={"type": type(error).__name__, "message": str(error)},
            )
        return await self._publish_terminal(queue, entry, queue._amark_failed, error)

    async def _settle_terminal(
        self,
        queue: AsyncQueue,
        entry: QueueEntry,
        worker_id: UUID,
        status: QueueEntryStatus,
        *,
        result: object | None = None,
        error: dict[str, str] | None = None,
    ) -> QueueEntry:
        try:
            current_entry = await queue.afind(entry.id)
            terminal_entry = replace(
                current_entry,
                status=status,
                result=result,
                error=error,
                finished_at=await queue.clock.anow(),
            )
            settled = await self._settle(queue, terminal_entry)
        except QueueEntryNotFoundError:
            self._log_missing_entry(entry.id)
            await self._discard_missing(queue, entry.id)
            return entry
        except Exception as exc:
            logger.exception("Unable to settle claimed queue entry %s", entry.id)
            return await self._record_claim_persistence_failure(
                queue, entry, worker_id, exc
            )
        if not settled:
            logger.warning("Lost claim for queue entry %s before settlement", entry.id)
            return entry
        await queue.apublish(terminal_entry)
        self._record_terminal(terminal_entry)
        return terminal_entry

    async def _record_claim_persistence_failure(
        self,
        queue: AsyncQueue,
        entry: QueueEntry,
        worker_id: UUID,
        cause: Exception,
    ) -> QueueEntry:
        """Safely record a settlement infrastructure failure while still owner."""
        try:
            current_entry = await queue.afind(entry.id)
        except Exception:
            logger.exception(
                "Unable to inspect queue entry %s after settlement failure", entry.id
            )
            raise QueuePersistenceError(
                "Unable to persist terminal queue outcome"
            ) from cause
        if current_entry.status is not QueueEntryStatus.RUNNING:
            return current_entry
        try:
            failure_entry = replace(
                current_entry,
                status=QueueEntryStatus.FAILED,
                error={
                    "type": "QueuePersistenceError",
                    "message": "Unable to persist terminal queue outcome",
                },
                finished_at=await queue.clock.anow(),
            )
            settled = await self._settle(queue, failure_entry)
        except Exception:
            logger.exception(
                "Unable to record settlement failure for entry %s", entry.id
            )
            raise QueuePersistenceError(
                "Unable to persist terminal queue outcome"
            ) from cause
        if not settled:
            raise QueuePersistenceError(
                "Unable to persist terminal queue outcome"
            ) from cause
        await queue.apublish(failure_entry)
        self._record_terminal(failure_entry)
        return failure_entry

    async def _publish_terminal(
        self,
        queue: AsyncQueue,
        entry: QueueEntry,
        update: Callable[..., Awaitable[QueueEntry]],
        *args: object,
    ) -> QueueEntry:
        try:
            terminal_entry = await asyncio.shield(update(entry.id, *args))
        except Exception as exc:
            logger.exception(
                "Unable to record terminal queue outcome for entry %s", entry.id
            )
            terminal_entry = await self._record_persistence_failure(queue, entry)
            if terminal_entry is None:
                raise QueuePersistenceError(
                    "Unable to persist terminal queue outcome"
                ) from exc
        await queue.apublish(terminal_entry)
        self._record_terminal(terminal_entry)
        return terminal_entry

    async def _record_persistence_failure(
        self, queue: AsyncQueue, entry: QueueEntry
    ) -> QueueEntry | None:
        try:
            current_entry = await queue.afind(entry.id)
        except Exception:
            logger.exception(
                "Unable to inspect queue entry %s after a persistence failure", entry.id
            )
            return None
        if current_entry.status is not QueueEntryStatus.RUNNING:
            return current_entry
        try:
            return await queue._amark_failed(
                entry.id,
                QueuePersistenceError("Unable to persist terminal queue outcome"),
            )
        except Exception:
            logger.exception(
                "Unable to record persistence failure for entry %s", entry.id
            )
            return None

    def _record_terminal(self, entry: QueueEntry) -> None:
        match entry.status:
            case QueueEntryStatus.SUCCEEDED:
                self._succeeded_count += 1
            case QueueEntryStatus.FAILED:
                self._failed_count += 1
            case QueueEntryStatus.CANCELLED:
                self._cancelled_count += 1
            case QueueEntryStatus.TIMEOUT:
                self._timed_out_count += 1
            case _:
                return
        self._active_entry_id = None
        self._active_queue_name = None
        self._last_claim_conflict_at.pop(entry.id, None)
        self._log_state_change("terminal_recorded", entry)

    def _log_state_change(self, event: str, entry: QueueEntry | None = None) -> None:
        snapshot = self.snapshot
        message = WORKER_EVENT_MESSAGES.get(event, event)
        logger.info(
            message,
            extra={
                "queue_worker_event": event,
                "queue_worker_id": str(snapshot.worker_id),
                "queue_worker_running": snapshot.running,
                "queue_worker_started_at": snapshot.started_at.to_timestamp()
                if snapshot.started_at is not None
                else None,
                "queue_worker_running_for": snapshot.running_for,
                # Present only on a terminal record, where an entry is in hand.
                "queue_worker_entry_queued_for": entry.queued_for
                if entry is not None
                else None,
                "queue_worker_entry_ran_for": entry.ran_for
                if entry is not None
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
                "queue_worker_timed_out_count": snapshot.timed_out_count,
            },
        )
