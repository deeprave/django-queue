"""Configured worker and handler for the priority queue dashboard demo."""

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

_RUNNING_DELAY_SECONDS = (30, 60)
_IMMEDIATE_RELEASE_DELAY_SECONDS = 1 / MICROSECONDS_PER_SECOND
_MAX_CONCURRENT_HANDLERS = 5

logger = logging.getLogger(__name__)
_faker = Faker("en_US")

# Four priority tiers, each injected on its own independent random interval.
# Low and normal arrive often, forming a steady backlog; high and urgent
# arrive rarely, so a viewer can watch them consistently jump that backlog --
# and watch low-priority entries occasionally stall while higher tiers keep
# arriving, the other half of what a priority queue demonstrates.
#
# An entry's full visible lifetime -- queued delay + running delay +
# dashboard retention -- averages ~95s (see PRIORITY_TIERS' queued_delay,
# _RUNNING_DELAY_SECONDS, and RETENTION_TIMEOUT in settings.py). Intervals
# are tuned so each tier keeps roughly 3-5 entries in flight at steady
# state (95s / interval) for low/normal, not the dozens a much faster
# spawn rate would flood the table with. high/urgent are deliberately
# rarer than low/normal -- but only ~1.5x/~2.5x, not the ~3x/~5x gap this
# started with, which read as "high and urgent almost never arrive" over
# a short demo session rather than "occasional".
#
# `queued_delay` -- the queued -> running wait -- is scaled by priority
# tier into narrow, non-overlapping bands, highest priority shortest: a
# claimable higher-priority entry always becomes due before a lower-priority
# one that arrived around the same time, so dispatch order tracks priority
# order even through this artificial "still waiting to start" window, not
# just the underlying claim. running -> terminal timing is NOT scaled --
# once dispatched, how long a handler takes has nothing to do with how
# urgently it was picked up.
PRIORITY_TIERS = {
    "low": {"priority": -5, "interval": (22, 32), "queued_delay": (25, 32)},
    "normal": {"priority": 0, "interval": (26, 40), "queued_delay": (17, 23)},
    "high": {"priority": 5, "interval": (45, 55), "queued_delay": (10, 15)},
    "urgent": {"priority": 10, "interval": (65, 85), "queued_delay": (4, 8)},
}


class DemoPriorityQueueWorker(RedisAsyncQueueWorker):
    """Dispatch due entries, letting up to `_MAX_CONCURRENT_HANDLERS`
    handlers run concurrently.

    Routes claim/release through the queue-level `aclaim`/`arelease` hooks
    (`queue.aclaim(...)`, `queue.arelease(...)`) rather than calling the
    provider directly -- `RedisAsyncPriorityQueue` overrides those hooks to
    claim from and release back to its own priority-ordered pending store;
    calling `provider.aclaim(...)`/`provider.arelease(...)` directly would
    silently bypass that and only ever see the plain FIFO path.

    Without a concurrency cap, a steady drip of low/normal-priority entries
    can keep an unbounded number of handlers running at once, drowning out
    the rare high/urgent arrivals this demo exists to make visible. The cap
    is enforced in `_next`, *before* claiming -- not in `_dispatch` after
    the claim -- so a blocked entry stays visibly `queued` and claimable by
    the time a slot frees, rather than sitting claimed-but-idle and
    starving other entries (including higher-priority ones) from being
    claimed at all while a slot is full.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._handler_tasks: set[asyncio.Task[None]] = set()
        self._injection_tasks: list[asyncio.Task[None]] = []
        self._handler_slots = asyncio.Semaphore(_MAX_CONCURRENT_HANDLERS)

    async def run(self) -> None:
        queue = self._queues["demo"]
        self._injection_tasks = [
            asyncio.create_task(self._inject_tier_entries(queue, tier, config))
            for tier, config in PRIORITY_TIERS.items()
        ]
        try:
            await super().run()
        finally:
            for task in self._injection_tasks:
                task.cancel()
            for task in self._injection_tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            tasks = tuple(self._handler_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _inject_tier_entries(
        self, queue: BaseQueue, tier: str, config: dict
    ) -> None:
        """Inject demo work for one priority tier at its own random interval."""
        low, high = config["interval"]
        while True:
            await asyncio.sleep(random.uniform(low, high))
            try:
                await queue.aenqueue(
                    build_demo_payload(
                        generate_demo_message(tier),
                        tier,
                        should_fail=random.randrange(8) == 0,
                    ),
                    priority=config["priority"],
                )
            except Exception:
                logger.exception("Unable to enqueue a %s-priority demo entry", tier)

    async def _next(self, queue: BaseQueue) -> tuple[QueueEntry, float | None] | None:
        await self._recover_expired_claims(queue)
        if self._handler_slots.locked():
            # All _MAX_CONCURRENT_HANDLERS slots are busy -- skip claiming
            # entirely this cycle, leaving every pending entry visibly
            # `queued` and available to be claimed once a slot frees,
            # rather than claiming one and leaving it idle mid-lifecycle.
            return None
        entry = await queue.aclaim(self._worker_id, queue.default_claim_lease_seconds)
        provider = self._providers[queue]
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
            queue,
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
        await self._handler_slots.acquire()
        running_entry = await self._mark_running(queue, entry)
        if running_entry is None:
            # Lost the claim race after all -- no handler task is starting,
            # so the slot this entry would have used must be freed here;
            # _complete_entry, which normally releases it, never runs.
            self._handler_slots.release()
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
        finally:
            self._handler_slots.release()


def seed_one_entry_per_tier() -> None:
    """Enqueue one entry per priority tier if the demo queue is empty.

    `runqueues`'s worker-activation loop waits for the queue to already
    have a pending entry before it ever constructs the worker and calls
    `run()` -- but this worker's per-tier injectors live inside `run()`
    itself, so a genuinely empty queue would otherwise deadlock forever.
    Called once from `DashboardConfig.ready()`, only when the command being
    run is `runqueues`. Guarded by `ahas_pending()` so it's a no-op if the
    queue already has entries (e.g. `manage.py demo` already seeded it, or
    `runqueues` is restarting with existing state).
    """
    from asgiref.sync import async_to_sync

    from django_queue import queues

    async def scenario() -> None:
        queue = queues["demo"]
        try:
            if await queue.ahas_pending():
                return
            for tier, config in PRIORITY_TIERS.items():
                await queue.aenqueue(
                    build_demo_payload(
                        generate_demo_message(tier), tier, should_fail=False
                    ),
                    priority=config["priority"],
                )
        finally:
            await queue.aclose()

    async_to_sync(scenario)()


async def handle_demo_entry(entry: QueueEntry) -> dict[str, str]:
    """Wait independently for the terminal transition, then report its outcome."""
    terminal_state = (
        QueueEntryStatus.FAILED
        if entry.payload["should_fail"]
        else QueueEntryStatus.SUCCEEDED
    )
    await asyncio.sleep(max(0, _transition_at(entry, terminal_state) - time.time()))
    return {"message": entry.payload["message"], "status": "processed"}


def generate_demo_message(tier: str = "normal") -> str:
    """Return one short, English message for a demo entry, tagged by tier."""
    sentence = _faker.sentence(nb_words=random.randint(5, 12))
    if tier == "urgent":
        return f"URGENT: {sentence}"
    if tier == "high":
        return f"High priority: {sentence}"
    if tier == "low":
        return f"Low priority: {sentence}"
    return sentence


def build_demo_payload(message: str, tier: str, should_fail: bool) -> dict:
    """Build payload metadata that drives this entry's two demo transitions."""
    queued_delay = PRIORITY_TIERS[tier]["queued_delay"]
    running_at = time.time() + random.uniform(*queued_delay)
    terminal_state = "failed" if should_fail else "succeeded"
    return {
        "message": message,
        "source": "faker",
        "priority_label": tier,
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
    queue: BaseQueue, entry: QueueEntry, worker_id, delay_seconds: float
) -> None:
    """Return a not-yet-due queued entry to this demo's pending store."""
    if not await queue.arelease(entry.id, worker_id, delay_seconds):
        logger.warning("Lost claim for queue entry %s before requeueing", entry.id)
