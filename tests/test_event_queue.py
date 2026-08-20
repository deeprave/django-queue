import asyncio
from uuid import uuid4

import pytest

from django_queue.backends import MemoryAsyncQueue, MemoryEventQueue
from django_queue.backends.base import EventQueue
from django_queue.backends.exceptions import (
    InvalidQueueBackendError,
    QueueEmptyException,
    QueueEntryNotFoundError,
)
from django_queue.backends.memory import MemoryEventQueueWorker
from django_queue.backends.redis import RedisEventQueue, RedisEventQueueWorker
from django_queue.entries import QueueEntryStatus
from django_queue.event_worker import EventQueueWorker
from django_queue.worker import AsyncQueueWorker
from tests.helpers import CustomQueueEntry


def test_task_outcomes_are_retained_but_directly_consumed_events_are_removed():
    task_queue = MemoryAsyncQueue(queue_name="tasks")
    event_queue = MemoryEventQueue(queue_name="events")

    async def exercise():
        task_id = await task_queue.aenqueue("task")

        async def handle(entry):
            return "done"

        worker = AsyncQueueWorker(
            {"tasks": task_queue}, {"tasks": handle}, idle_delay=0.001
        )
        worker_task = asyncio.create_task(worker.run())
        while (
            await task_queue.afind(task_id)
        ).status is not QueueEntryStatus.SUCCEEDED:
            await asyncio.sleep(0.001)
        worker_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker_task
        event_id = await event_queue.aenqueue("event")
        return task_id, event_id

    task_id, event_id = asyncio.run(exercise())

    assert event_queue.dequeue().id == event_id
    assert task_queue.find(task_id).status is QueueEntryStatus.SUCCEEDED
    with pytest.raises(QueueEntryNotFoundError):
        event_queue.find(event_id)


def test_event_backends_inherit_the_composed_entry_facade():
    assert MemoryEventQueue.aenqueue is EventQueue.aenqueue
    assert RedisEventQueue.aenqueue is EventQueue.aenqueue


def test_redis_event_queue_uses_a_redis_specific_default_worker():
    queue = RedisEventQueue("redis://localhost:6379/0")

    assert queue.resolve_worker("events") is RedisEventQueueWorker


def test_redis_event_queue_rejects_the_generic_event_worker(redis_url):
    class GenericEventWorker(EventQueueWorker):
        async def _next(self):
            return None

        async def _release(self, entry):
            pass

        async def _remove(self, entry):
            pass

    queue = RedisEventQueue(redis_url, queue_name="events")

    with pytest.raises(TypeError, match="requires a redis worker"):
        GenericEventWorker(queue)


def test_redis_event_queue_rejects_a_generic_worker_override(redis_url):
    queue = RedisEventQueue(redis_url, queue_name="events")
    queue.worker_class = EventQueueWorker

    with pytest.raises(InvalidQueueBackendError, match="requires a redis worker"):
        queue.resolve_worker("events")


def test_memory_event_queue_uses_the_default_event_worker():
    queue = MemoryEventQueue()

    assert queue.resolve_worker("events") is MemoryEventQueueWorker


def test_memory_event_queue_rejects_a_redis_worker():
    queue = MemoryEventQueue(queue_name="events")

    with pytest.raises(TypeError, match="requires a memory worker"):
        RedisEventQueueWorker(queue)


def test_directly_dequeuing_an_event_removes_its_record():
    queue = MemoryEventQueue(queue_name="events")
    event_id = queue.enqueue("event")

    assert queue.dequeue().id == event_id
    with pytest.raises(QueueEntryNotFoundError):
        queue.find(event_id)


@pytest.mark.parametrize(
    ("entry_timeout", "queue_timeout", "expected_timeout"),
    [(15, 30, 15), (None, 30, 30), (None, None, 60)],
    ids=["entry-override", "queue-default", "built-in-default"],
)
def test_event_lifetime_resolves_from_entry_then_queue_then_default(
    entry_timeout, queue_timeout, expected_timeout
):
    queue = MemoryEventQueue(queue_name="events")
    queue.timeout_seconds = queue_timeout
    entry_id = queue.enqueue("event", timeout_seconds=entry_timeout)

    assert queue.find(entry_id).timeout_seconds == expected_timeout


def test_redis_event_queue_uses_event_semantics(redis_client):
    assert isinstance(RedisEventQueue(redis_client), EventQueue)


def test_redis_provider_adelete_removes_every_store_atomically(redis_client):
    """adelete previously issued six separate, non-atomic Redis calls (DEL,
    LREM, three ZREMs, plus adiscard_priority's own script) -- a crash
    partway through left a partial clear with no way to detect or resume it
    (cursor.json CR-2, re-flagged on a second review pass after being
    deferred once as dead code, task 8.4). Fixed with one atomic
    _DELETE_SCRIPT. Proves every store adelete's docstring claims to clean
    is actually empty afterwards, exercised through EventQueue.aclear() --
    adelete's only real caller. Two entries cover disjoint preconditions
    that don't naturally coexist on one entry: an unclaimed event (record,
    pending list, unclaimed-deadlines ZSET all populated by astore_event/
    apush) and a claimed one (claim key, claim-deadlines ZSET populated by
    aclaim_unexpired, which removes the entry from unclaimed-deadlines as
    part of claiming it)."""
    queue = RedisEventQueue(redis_client, queue_name=f"events-{uuid4().hex}")
    provider = queue._provider

    async def scenario():
        # aclaim_unexpired claims FIFO, so the first-pushed entry is the one
        # that ends up claimed; the second stays unclaimed.
        claimed_id = await queue.aenqueue("claimed")
        unclaimed_id = await queue.aenqueue("unclaimed")
        worker_id = uuid4()
        claimed = await provider.aclaim_unexpired(worker_id, lease_seconds=60)
        assert claimed.id == claimed_id

        await queue.aclear()

        client = provider._async_redis()
        results = {}
        for label, entry_id in (("unclaimed", unclaimed_id), ("claimed", claimed_id)):
            entry_id_value = str(entry_id)
            results[label] = {
                "record": await client.get(provider._entry_key(entry_id)),
                "pending": await client.lpos(
                    provider._entry_pending_name, entry_id_value
                ),
                "delayed": await client.zscore(
                    provider._entry_delayed_name, entry_id_value
                ),
                "claim_deadline": await client.zscore(
                    provider._entry_claim_deadlines_name, entry_id_value
                ),
                "unclaimed_deadline": await client.zscore(
                    provider._entry_unclaimed_deadlines_name, entry_id_value
                ),
                "claim": await client.get(provider._claim_key(entry_id)),
            }
        await queue.aclose()
        return results

    results = asyncio.run(scenario())
    empty = {
        "record": None,
        "pending": None,
        "delayed": None,
        "claim_deadline": None,
        "unclaimed_deadline": None,
        "claim": None,
    }
    assert results["unclaimed"] == empty
    assert results["claimed"] == empty


def test_redis_event_aenqueue_routes_through_the_atomic_store_and_push_path(
    redis_client,
):
    """Event-queue equivalent of the atomic-enqueue wiring tests in
    test_redis_entries.py / test_redispqueuejson.py -- aenqueue()'s
    astore_event()+apush() split was previously two (astore_event() is
    itself two writes) separate round-trips. RedisEventQueue._astore_and_push
    should route through astore_event_and_push() instead, never the plain,
    non-atomic astore_event()/apush() individually."""
    queue = RedisEventQueue(redis_client, queue_name=f"events-{uuid4().hex}")
    provider = queue._provider

    async def _fail(*args, **kwargs):
        raise AssertionError(
            "aenqueue() must not call the non-atomic astore_event/apush"
        )

    provider.astore_event = _fail
    provider.apush = _fail

    async def exercise():
        event_id = await queue.aenqueue("event")
        entry = await queue.afind(event_id)
        dequeued = await queue.adequeue()
        await queue.aclose()
        return entry, dequeued, event_id

    entry, dequeued, event_id = asyncio.run(exercise())

    assert entry.id == event_id
    assert dequeued.id == event_id


def test_directly_dequeuing_a_redis_event_removes_its_record(redis_client):
    queue = RedisEventQueue(redis_client)

    async def exercise():
        event_id = await queue.aenqueue("event")
        entry = await queue.adequeue()
        with pytest.raises(QueueEntryNotFoundError):
            await queue.afind(event_id)
        await queue.aclose()
        return entry, event_id

    entry, event_id = asyncio.run(exercise())

    assert entry.id == event_id


def test_redis_event_queue_restores_the_configured_entry_class(redis_client):
    queue = RedisEventQueue(redis_client, entry_class=CustomQueueEntry)

    async def exercise():
        entry_id = await queue.aenqueue("event")
        entry = await queue.afind(entry_id)
        await queue.aclose()
        return entry

    assert isinstance(asyncio.run(exercise()), CustomQueueEntry)


@pytest.mark.parametrize("queue_type", [MemoryEventQueue, RedisEventQueue])
def test_event_queue_prunes_an_expired_unconsumed_event(queue_type, redis_client):
    queue = (
        queue_type(redis_client, queue_name=f"events-{uuid4().hex}")
        if queue_type is RedisEventQueue
        else queue_type(queue_name="events")
    )

    async def exercise():
        event_id = await queue.aenqueue("event", timeout_seconds=0.001)
        await asyncio.sleep(0.01)
        assert not await queue.ahas_pending()
        with pytest.raises(QueueEntryNotFoundError):
            await queue.afind(event_id)
        await queue.aclose()

    asyncio.run(exercise())


def test_memory_event_queues_are_local_to_their_instances():
    first = MemoryEventQueue(queue_name="events")
    second = MemoryEventQueue(queue_name="events")
    first.enqueue("event")

    with pytest.raises(QueueEmptyException):
        second.dequeue()


def test_clearing_a_memory_event_queue_removes_raw_items_and_event_entries():
    queue = MemoryEventQueue(queue_name="events")
    queue.add("raw item")
    entry_id = queue.enqueue("event")

    queue.clear()

    assert queue.size() == 0
    with pytest.raises(QueueEntryNotFoundError):
        queue.find(entry_id)
