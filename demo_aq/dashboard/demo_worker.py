"""Configured worker and handler for the AsyncQueue dashboard demo."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Mapping

from faker import Faker

from django_queue.backends.base import BaseQueue
from django_queue.backends.redis import RedisAsyncQueueWorker
from django_queue.clock import MICROSECONDS_PER_SECOND
from django_queue.entries import QueueEntry, QueueEntryStatus

_QUEUED_DELAY_SECONDS = (10, 30)
_RUNNING_DELAY_SECONDS = (30, 60)
_FOLLOW_UP_DELAY_SECONDS = (5, 20)
_IMMEDIATE_RELEASE_DELAY_SECONDS = 1 / MICROSECONDS_PER_SECOND

logger = logging.getLogger(__name__)
_faker = Faker("en_US")


class DemoQueueWorker(RedisAsyncQueueWorker):
    """Dispatch due entries, letting their handlers complete concurrently."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._handler_tasks: set[asyncio.Task[None]] = set()
        self._injection_task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        self._injection_task = asyncio.create_task(
            self._inject_follow_up_entries(self._queues["demo"])
        )
        try:
            await super().run()
        finally:
            if self._injection_task is not None:
                self._injection_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._injection_task
            tasks = tuple(self._handler_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _inject_follow_up_entries(self, queue: BaseQueue) -> None:
        """Inject normal demo work at independently randomized intervals."""
        while True:
            await asyncio.sleep(random.uniform(*_FOLLOW_UP_DELAY_SECONDS))
            try:
                await queue.aenqueue(
                    build_demo_payload(
                        generate_demo_message(), should_fail=random.randrange(8) == 0
                    )
                )
            except Exception:
                logger.exception("Unable to enqueue a follow-up demo entry")

    async def _next(self, queue: BaseQueue) -> tuple[QueueEntry, float | None] | None:
        await self._recover_expired_claims(queue)
        provider = self._providers[queue]
        entry = await provider.aclaim(
            self._worker_id, queue.default_claim_lease_seconds
        )
        try:
            transition_due = _transition_due(entry, QueueEntryStatus.RUNNING)
        except (TypeError, ValueError) as exc:
            running_entry = await self._mark_running(queue, entry)
            if running_entry is None:
                return None
            await queue.apublish(running_entry)
            await self._record_failure(queue, running_entry, exc, self._worker_id)
            return None
        if transition_due:
            lease_seconds = (
                self.budget_for(queue, entry) + self._cancellation_grace_period
            )
            if await provider.arenew(entry.id, self._worker_id, lease_seconds):
                return entry, lease_seconds
            return None
        await _requeue_entry(
            provider,
            entry,
            self._worker_id,
            max(
                _IMMEDIATE_RELEASE_DELAY_SECONDS,
                _transition_at(entry, QueueEntryStatus.RUNNING) - time.time(),
            ),
        )
        return None

    async def _dispatch(
        self,
        queue: BaseQueue,
        handler,
        entry: QueueEntry,
        lease_seconds: float | None = None,
    ) -> None:
        running_entry = await self._mark_running(queue, entry)
        if running_entry is None:
            return
        await queue.apublish(running_entry)
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
                queue,
                entry,
                RuntimeError("Demo handler terminated during shutdown"),
                self._worker_id,
            )
            raise
        except Exception as exc:  # noqa: BLE001 - demo failures are intentional.
            await self._record_failure(queue, entry, exc, self._worker_id)
        else:
            if entry.payload["should_fail"]:
                await self._settle_terminal(
                    queue,
                    entry,
                    self._worker_id,
                    QueueEntryStatus.FAILED,
                    error={
                        "type": "RuntimeError",
                        "message": "Intentional demo failure",
                    },
                )
            else:
                await self._record_result(queue, entry, result, self._worker_id)


async def handle_demo_entry(entry: QueueEntry) -> dict[str, str]:
    """Wait independently for the terminal transition, then report its outcome."""
    terminal_state = (
        QueueEntryStatus.FAILED
        if entry.payload["should_fail"]
        else QueueEntryStatus.SUCCEEDED
    )
    await asyncio.sleep(max(0, _transition_at(entry, terminal_state) - time.time()))
    return {"message": entry.payload["message"], "status": "processed"}


def generate_demo_message() -> str:
    """Return one short, English message for a demo entry."""
    return _faker.sentence(nb_words=random.randint(5, 12))


def build_demo_payload(message: str, should_fail: bool) -> dict:
    """Build payload metadata that drives this entry's two demo transitions."""
    running_at = time.time() + random.uniform(*_QUEUED_DELAY_SECONDS)
    terminal_state = "failed" if should_fail else "succeeded"
    return {
        "message": message,
        "source": "faker",
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
    if not isinstance(entry.payload, Mapping):
        raise TypeError(f"Demo entry {entry.id} payload must be a mapping")
    transitions = entry.payload.get("transitions")
    if not isinstance(transitions, list):
        raise TypeError(f"Demo entry {entry.id} must define a transitions list")
    for transition in transitions:
        if (
            not isinstance(transition, Mapping)
            or transition.get("state") != state.value
        ):
            continue
        transition_at = transition.get("at")
        if isinstance(transition_at, (int, float)) and not isinstance(
            transition_at, bool
        ):
            return float(transition_at)
        raise ValueError(
            f"Demo entry {entry.id} has an invalid {state.value} transition timestamp"
        )
    raise ValueError(f"Demo entry {entry.id} has no {state.value} transition")


async def _requeue_entry(
    provider, entry: QueueEntry, worker_id, delay_seconds: float
) -> None:
    """Return a not-yet-due queued entry to the end of this demo's pending list."""
    if not await provider.arelease(entry.id, worker_id, delay_seconds):
        logger.warning("Lost claim for queue entry %s before requeueing", entry.id)
