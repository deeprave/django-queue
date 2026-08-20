import asyncio
from uuid import uuid4

import pytest

from django_queue.backends import (
    QueueEmptyException,
    QueueEncodingException,
)
from django_queue.backends.redis import (
    RedisAsyncPriorityQueueJson,
    RedisAsyncQueueWorker,
)
from django_queue.entries import QueueEntryStatus


@pytest.fixture
def redis_queue(redis_url) -> RedisAsyncPriorityQueueJson:
    return RedisAsyncPriorityQueueJson(redis_url)


@pytest.fixture
def tracked_redis_queue(redis_url) -> RedisAsyncPriorityQueueJson:
    """A distinct queue_name per test so tracked-path ordering assertions
    are not disturbed by another test's entries sharing a Redis key."""
    return RedisAsyncPriorityQueueJson(
        redis_url, options={"queue_name": f"priority-{uuid4().hex}"}
    )


def test_get_valid_json(redis_queue):
    item = (1, {"key": "value"})
    redis_queue.add(item)
    fetched_item = redis_queue.get()
    assert fetched_item == {"key": "value"}
    assert redis_queue.size() == 0


def test_get_valid_string(redis_queue):
    item = (1, "test_string")
    redis_queue.add(item)
    fetched_item = redis_queue.get()
    assert fetched_item == "test_string"
    assert redis_queue.size() == 0


def test_poll_with_timeout(redis_queue):
    item = (1, {"key": "value"})
    redis_queue.add(item)
    fetched_item = redis_queue.poll(timeout=5)
    assert fetched_item == {"key": "value"}
    assert redis_queue.size() == 0


def test_peek_valid_json(redis_queue):
    item = (1, {"key": "value"})
    redis_queue.add(item)
    peeked_item = redis_queue.peek()
    assert peeked_item == {"key": "value"}
    assert redis_queue.size() == 1


def test_add_invalid_item_raise_exception(redis_queue):
    with pytest.raises(QueueEncodingException):
        redis_queue.add((1, {"invalid"}))


def test_get_empty_queue(redis_queue):
    with pytest.raises(QueueEmptyException):
        redis_queue.get()


def test_peek_empty_queue(redis_queue):
    with pytest.raises(QueueEmptyException):
        redis_queue.peek()


@pytest.mark.slow
def test_large_queue(redis_queue):
    values = []
    # sourcery skip: no-loop-in-tests
    for p in range(1000):
        item = 1000 - p, f"item_{p}"
        redis_queue.add(item)
        values.append(item[1])
    for value in values:
        assert redis_queue.get() == value
    with pytest.raises(QueueEmptyException):
        redis_queue.peek()
    values = []
    for p in reversed(range(1000)):
        item = p, f"item_{p}"
        redis_queue.add(item)
        values.append(item[1])
    for value in values:
        assert redis_queue.get() == value


@pytest.mark.slow
def test_poll_empty_queue(redis_queue):
    with pytest.raises(QueueEmptyException):
        redis_queue.poll(timeout=1, retries=2)


def test_aenqueue_routes_through_the_atomic_store_and_push_priority_path(
    tracked_redis_queue,
):
    """Priority-variant equivalent of the plain-queue atomic-enqueue wiring
    test in test_redis_entries.py -- RedisAsyncPriorityQueue._astore_and_push
    should route through astore_and_push_priority(), never the plain,
    non-atomic astore()/apush_priority() individually."""
    provider = tracked_redis_queue._provider

    async def _fail(*args, **kwargs):
        raise AssertionError(
            "aenqueue() must not call the non-atomic astore/apush_priority"
        )

    provider.astore = _fail
    provider.apush_priority = _fail

    entry_id = tracked_redis_queue.enqueue("work", priority=5)

    assert tracked_redis_queue.find(entry_id).id == entry_id
    assert tracked_redis_queue.dequeue().id == entry_id


def test_dispatches_the_tracked_path_in_priority_order(tracked_redis_queue):
    low_id = tracked_redis_queue.enqueue("low", priority=1)
    high_id = tracked_redis_queue.enqueue("high", priority=10)

    assert tracked_redis_queue.dequeue().id == high_id
    assert tracked_redis_queue.dequeue().id == low_id


def test_preserves_arrival_order_within_one_priority(tracked_redis_queue):
    first_id = tracked_redis_queue.enqueue("first", priority=5)
    second_id = tracked_redis_queue.enqueue("second", priority=5)

    assert tracked_redis_queue.dequeue().id == first_id
    assert tracked_redis_queue.dequeue().id == second_id


def test_dequeued_entry_is_findable_and_runs_the_lifecycle(tracked_redis_queue):
    entry_id = tracked_redis_queue.enqueue("work", priority=3)
    dequeued = tracked_redis_queue.dequeue()

    assert dequeued.id == entry_id
    assert tracked_redis_queue.find(entry_id).id == entry_id
    running = tracked_redis_queue._mark_running(entry_id)
    assert running.status == QueueEntryStatus.RUNNING


def test_defaults_priority_to_zero_and_still_dispatches(tracked_redis_queue):
    entry_id = tracked_redis_queue.enqueue("work")

    entry = tracked_redis_queue.find(entry_id)

    assert entry.priority == 0
    assert tracked_redis_queue.dequeue().id == entry_id


def test_has_pending_after_a_tracked_priority_enqueue(tracked_redis_queue):
    """has_pending() previously only inspected the plain FIFO pending list
    and the delayed set, so a priority-only queue never reported readiness
    -- runqueues polls exactly this predicate before ever constructing a
    worker (runqueues.py:104), so this bug meant a priority backend's
    worker was never started at all."""
    assert not tracked_redis_queue.has_pending()
    tracked_redis_queue.enqueue("work", priority=5)
    assert tracked_redis_queue.has_pending()
    tracked_redis_queue.dequeue()
    assert not tracked_redis_queue.has_pending()


def test_failure_removes_entry_from_priority_pending_store(tracked_redis_queue):
    entry_id = tracked_redis_queue.enqueue("work", priority=5)
    tracked_redis_queue._mark_failed(entry_id, ValueError("boom"))

    with pytest.raises(QueueEmptyException):
        tracked_redis_queue.dequeue()


def test_arrival_order_survives_a_full_drain_and_refill(tracked_redis_queue):
    """The tracked-path sequence counter resets to 0 whenever the priority
    ZSET drains empty (see QueueProviderRedis.apop_priority), so it never
    runs indefinitely -- draining and refilling repeatedly must not corrupt
    arrival order for what's pushed afterwards, the way a stale pre-reset
    sequence value landing late would."""
    for _ in range(3):
        first_id = tracked_redis_queue.enqueue("first", priority=5)
        second_id = tracked_redis_queue.enqueue("second", priority=5)

        assert tracked_redis_queue.dequeue().id == first_id
        assert tracked_redis_queue.dequeue().id == second_id


def test_sequence_counter_resets_once_the_priority_store_drains(tracked_redis_queue):
    provider = tracked_redis_queue._provider
    first_id = tracked_redis_queue.enqueue("first", priority=1)
    second_id = tracked_redis_queue.enqueue("second", priority=1)
    assert tracked_redis_queue.dequeue().id == first_id

    async def sequence_value():
        client = provider._async_redis()
        try:
            return await client.get(provider._entry_pending_priority_sequence_name)
        finally:
            await provider.aclose()

    from asgiref.sync import async_to_sync

    assert async_to_sync(sequence_value)() is not None

    assert tracked_redis_queue.dequeue().id == second_id

    assert async_to_sync(sequence_value)() == b"0"


def test_provider_adelete_also_removes_a_still_pending_priority_entry(
    tracked_redis_queue,
):
    """adelete's contract is "remove entry_id from every store it could be
    sitting in" -- not reachable through the public AsyncQueue/EventQueue
    API today (adelete is only ever called by EventQueue.aclear(), which
    never touches the priority pending store), but a future caller must not
    be able to silently orphan an entry that's still queued in a priority
    backend's pending store."""
    from asgiref.sync import async_to_sync

    provider = tracked_redis_queue._provider
    entry_id = tracked_redis_queue.enqueue("work", priority=5)

    async def scenario():
        try:
            await provider.adelete(entry_id)
        finally:
            await provider.aclose()

    async_to_sync(scenario)()

    with pytest.raises(QueueEmptyException):
        tracked_redis_queue.dequeue()


def test_real_worker_claims_and_dispatches_a_priority_entry(tracked_redis_queue):
    """RedisAsyncQueueWorker._next previously called provider.aclaim(...)
    directly, which runs _CLAIM_SCRIPT against the plain pending list only
    -- a priority-tracked entry, which only ever lives in the priority
    ZSET, was never claimed or dispatched by a real worker. Exercises the
    real RedisAsyncQueueWorker, not just the provider's claim method in
    isolation."""

    async def exercise():
        try:
            entry_id = await tracked_redis_queue.aenqueue("work", priority=5)

            async def handle(entry):
                return entry.payload

            worker = RedisAsyncQueueWorker(
                {"priority": tracked_redis_queue},
                {"priority": handle},
                idle_delay=0.001,
            )
            task = asyncio.create_task(worker.run())
            try:
                while (await tracked_redis_queue.afind(entry_id)).status not in {
                    QueueEntryStatus.SUCCEEDED,
                    QueueEntryStatus.FAILED,
                }:
                    await asyncio.sleep(0.001)
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            return await tracked_redis_queue.afind(entry_id)
        finally:
            await tracked_redis_queue.aclose()

    entry = asyncio.run(asyncio.wait_for(exercise(), timeout=2))

    assert entry.status is QueueEntryStatus.SUCCEEDED
    assert entry.result == "work"


def test_release_does_not_let_a_low_priority_claim_jump_ahead_of_a_higher_priority_entry(
    tracked_redis_queue,
):
    """RedisAsyncQueueWorker._mark_running releases a lost claim via
    provider.arelease(...), which always parks the entry on the plain
    delayed set. _CLAIM_SCRIPT_WITH_PRIORITY promotes the plain delayed/
    pending list to claimable status before ever checking the priority
    ZSET, so a released low-priority entry could jump ahead of a
    genuinely higher-priority entry still waiting there. RedisAsyncQueue
    now routes release through queue.arelease(...), which
    RedisAsyncPriorityQueue overrides to redeliver via the priority ZSET
    instead."""

    async def exercise():
        try:
            low_id = await tracked_redis_queue.aenqueue("low", priority=1)
            worker_id = uuid4()
            claimed = await tracked_redis_queue.aclaim(worker_id, lease_seconds=60)
            assert claimed.id == low_id

            high_id = await tracked_redis_queue.aenqueue("high", priority=10)

            released = await tracked_redis_queue.arelease(
                low_id, worker_id, 1 / 1_000_000
            )
            assert released is True

            next_entry = await tracked_redis_queue.aclaim(uuid4(), lease_seconds=60)
            return next_entry.id, high_id, low_id
        finally:
            await tracked_redis_queue.aclose()

    next_id, high_id, low_id = asyncio.run(exercise())
    assert next_id == high_id, (
        f"expected higher-priority entry {high_id} to claim next, "
        f"got {next_id} (low-priority released entry was {low_id})"
    )
