import asyncio
from uuid import uuid4, uuid7

import pytest

from django_queue.backends import (
    MemoryAsyncQueue,
    MemoryEventQueue,
)
from django_queue.backends.base import EventQueue
from django_queue.backends.exceptions import (
    InvalidQueueBackendError,
    QueueEmptyException,
    QueueEntryExpiredError,
    QueueEntryNotFoundError,
)
from django_queue.backends.memory import MemoryEventQueueWorker
from django_queue.backends.redis import RedisEventQueue, RedisEventQueueWorker
from django_queue.entries import QueueEntryStatus
from django_queue.event_worker import EventQueueWorker
from tests.helpers import FIXED_CLOCK_TIME, CustomQueueEntry, FixedClock


def test_task_outcomes_are_retained_but_consumed_events_are_removed():
    task_queue = MemoryAsyncQueue(queue_name="tasks")
    task_id = task_queue.enqueue("task")
    task_queue._mark_running(task_id)
    task_queue._mark_succeeded(task_id, "done")

    event_queue = MemoryEventQueue(queue_name="events")

    async def consume_event():
        event_id = await event_queue.aenqueue("event")
        worker_id = uuid7()
        entry = await event_queue._provider.aclaim_unexpired(worker_id)
        assert entry is not None and entry.id == event_id
        assert await event_queue._provider.aremove(event_id, worker_id)
        return event_id

    event_id = asyncio.run(consume_event())

    assert task_queue.get_entry(task_id).status is QueueEntryStatus.SUCCEEDED
    with pytest.raises(QueueEntryNotFoundError):
        event_queue.get_entry(event_id)


def test_event_backends_inherit_the_composed_entry_facade():
    assert MemoryEventQueue.aenqueue is EventQueue.aenqueue
    assert RedisEventQueue.aenqueue is EventQueue.aenqueue


def test_redis_event_queue_uses_a_redis_specific_default_worker():
    queue = RedisEventQueue("redis://localhost:6379/0")

    assert queue.resolve_event_worker_class("events") is RedisEventQueueWorker


def test_redis_event_queue_rejects_the_generic_event_worker(redis_url):
    class GenericEventWorker(EventQueueWorker):
        async def _next_event(self):
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
        queue.resolve_event_worker_class("events")


def test_redis_event_queue_rejects_a_spoofed_redis_worker_override(redis_url):
    class SpoofedRedisEventWorker(EventQueueWorker):
        provider_kind = "redis"

    queue = RedisEventQueue(redis_url, queue_name="events")
    queue.worker_class = SpoofedRedisEventWorker

    with pytest.raises(InvalidQueueBackendError, match="not compatible"):
        queue.resolve_event_worker_class("events")


def test_memory_event_queue_uses_the_default_event_worker():
    queue = MemoryEventQueue()

    assert queue.resolve_event_worker_class("events") is MemoryEventQueueWorker


def test_memory_event_queue_rejects_a_relabelled_redis_worker():
    class RelabelledRedisEventWorker(RedisEventQueueWorker):
        provider_kind = "generic"

    queue = MemoryEventQueue(queue_name="events")

    with pytest.raises(TypeError, match="requires a memory worker"):
        RelabelledRedisEventWorker(queue)


def test_directly_dequeuing_an_event_removes_its_record():
    queue = MemoryEventQueue(queue_name="events")
    event_id = queue.enqueue("event")

    entry = queue.dequeue_entry()

    assert entry.id == event_id
    with pytest.raises(QueueEntryNotFoundError):
        queue.get_entry(event_id)


@pytest.mark.parametrize(
    ("entry_timeout", "queue_timeout", "expected_timeout"),
    [
        (15, 30, 15),
        (None, 30, 30),
        (None, None, 60),
    ],
    ids=["entry-override", "queue-default", "built-in-default"],
)
def test_event_lifetime_resolves_from_entry_then_queue_then_default(
    entry_timeout, queue_timeout, expected_timeout
):
    queue = MemoryEventQueue(queue_name="events")
    queue.timeout_seconds = queue_timeout

    entry_id = queue.enqueue("event", timeout_seconds=entry_timeout)

    assert queue.get_entry(entry_id).timeout_seconds == expected_timeout


def test_redis_event_queue_uses_event_semantics(redis_client):
    queue = RedisEventQueue(redis_client)

    assert isinstance(queue, EventQueue)


def test_redis_events_are_removed_only_by_the_worker_that_claimed_them(redis_client):
    queue = RedisEventQueue(redis_client)

    async def exercise():
        entry_id = await queue.aenqueue("event")
        owner = uuid4()
        assert (await queue._provider.aclaim_unexpired(owner)).id == entry_id
        assert not await queue._provider.aremove(entry_id, uuid4())
        assert await queue._provider.aremove(entry_id, owner)
        with pytest.raises(QueueEntryNotFoundError):
            await queue.aget_entry(entry_id)
        await queue.aclose()

    asyncio.run(exercise())


def test_directly_dequeuing_a_redis_event_removes_its_record(redis_client):
    queue = RedisEventQueue(redis_client)

    async def exercise():
        event_id = await queue.aenqueue("event")
        entry = await queue.adequeue_entry()
        assert entry.id == event_id
        with pytest.raises(QueueEntryNotFoundError):
            await queue.aget_entry(event_id)
        await queue.aclose()

    asyncio.run(exercise())


def test_redis_event_queue_restores_the_configured_entry_class(redis_client):
    queue = RedisEventQueue(redis_client, entry_class=CustomQueueEntry)

    assert queue._provider.entry_class is CustomQueueEntry

    async def exercise():
        entry_id = await queue.aenqueue("event")
        assert isinstance(await queue.aget_entry(entry_id), CustomQueueEntry)
        await queue.aclose()

    asyncio.run(exercise())


def test_redis_event_queue_prunes_an_expired_unconsumed_event(redis_client):
    queue = RedisEventQueue(redis_client)

    async def exercise():
        entry_id = await queue.aenqueue("event", timeout_seconds=0.001)
        await asyncio.sleep(0.01)
        assert not await queue.ahas_pending_entries()
        with pytest.raises(QueueEntryNotFoundError):
            await queue.aget_entry(entry_id)
        await queue.aclose()

    asyncio.run(exercise())


def test_memory_event_claim_rejects_an_event_that_expires_after_pruning(monkeypatch):
    clock = FixedClock()
    queue = MemoryEventQueue(queue_name="events", clock=clock)

    async def skip_pruning():
        return 0

    async def exercise():
        event_id = await queue.aenqueue("event", timeout_seconds=1)
        clock.timestamp = FIXED_CLOCK_TIME + 1
        monkeypatch.setattr(queue, "_aprune_expired", skip_pruning)

        with pytest.raises(QueueEntryExpiredError) as raised:
            await queue._provider.aclaim_unexpired(uuid4())

        assert raised.value.entry_id == event_id
        with pytest.raises(QueueEntryNotFoundError):
            await queue.aget_entry(event_id)

    asyncio.run(exercise())


def test_redis_event_claim_rejects_an_event_that_expires_after_pruning(
    redis_client, monkeypatch
):
    queue = RedisEventQueue(redis_client)

    async def skip_pruning():
        return 0

    async def exercise():
        event_id = await queue.aenqueue("event", timeout_seconds=0.001)
        await asyncio.sleep(0.01)
        monkeypatch.setattr(queue, "_aprune_expired", skip_pruning)

        with pytest.raises(QueueEntryExpiredError) as raised:
            await queue._provider.aclaim_unexpired(uuid4())

        assert raised.value.entry_id == event_id
        with pytest.raises(QueueEntryNotFoundError):
            await queue.aget_entry(event_id)
        await queue.aclose()

    asyncio.run(exercise())


def test_redis_expiry_does_not_remove_a_claimed_event(redis_client):
    queue = RedisEventQueue(redis_client)

    async def exercise():
        entry_id = await queue.aenqueue("event", timeout_seconds=1)
        owner = uuid4()
        assert (await queue._provider.aclaim_unexpired(owner)).id == entry_id
        await asyncio.sleep(0.01)

        assert await queue._aprune_expired() == 0
        assert (await queue.aget_entry(entry_id)).id == entry_id
        assert await queue._provider.aremove(entry_id, owner)
        await queue.aclose()

    asyncio.run(exercise())


def test_redis_event_queue_claims_prevent_a_second_worker_from_receiving_an_event(
    redis_client,
):
    queue_name = f"shared-events-{uuid4().hex}"
    first = RedisEventQueue(redis_client, queue_name=queue_name)
    second = RedisEventQueue(redis_client, queue_name=queue_name)

    async def exercise():
        entry_id = await first.aenqueue("event")
        claimed = await first._provider.aclaim_unexpired(uuid4())
        assert claimed.id == entry_id
        with pytest.raises(QueueEmptyException):
            await second._provider.aclaim_unexpired(uuid4())
        await first.aclose()
        await second.aclose()

    asyncio.run(exercise())


def test_released_event_waits_for_its_delay_before_it_can_be_claimed():
    clock = FixedClock()
    queue = MemoryEventQueue(queue_name="events", clock=clock)

    async def exercise():
        entry_id = await queue.aenqueue("event")
        worker_id = uuid7()
        assert (await queue._provider.aclaim_unexpired(worker_id)).id == entry_id
        assert await queue._provider.arelease(entry_id, worker_id, 10)
        with pytest.raises(QueueEmptyException):
            await queue._provider.aclaim_unexpired(uuid7())
        clock.timestamp = FIXED_CLOCK_TIME + 10
        assert (await queue._provider.aclaim_unexpired(uuid7())).id == entry_id

    asyncio.run(exercise())


def test_memory_event_claim_is_available_after_its_lease_expires():
    clock = FixedClock()
    queue = MemoryEventQueue(queue_name="events", clock=clock)

    async def exercise():
        entry_id = await queue.aenqueue("event")
        assert (
            await queue._provider.aclaim_unexpired(uuid7(), lease_seconds=10)
        ).id == entry_id
        clock.timestamp = FIXED_CLOCK_TIME + 10
        assert (await queue._provider.aclaim_unexpired(uuid7())).id == entry_id

    asyncio.run(exercise())


def test_expired_event_is_removed_without_a_terminal_result(caplog):
    clock = FixedClock()
    queue = MemoryEventQueue(queue_name="events", clock=clock)
    entry_id = queue.enqueue("event", timeout_seconds=10)
    clock.timestamp = FIXED_CLOCK_TIME + 10

    assert asyncio.run(queue.ahas_pending_entries()) is False
    with pytest.raises(QueueEntryNotFoundError):
        queue.get_entry(entry_id)
    assert "Discarded expired event" in caplog.text


def test_expiry_does_not_remove_a_claimed_memory_event():
    clock = FixedClock()
    queue = MemoryEventQueue(queue_name="events", clock=clock)

    async def exercise():
        entry_id = await queue.aenqueue("event", timeout_seconds=10)
        owner = uuid7()
        assert (await queue._provider.aclaim_unexpired(owner)).id == entry_id
        clock.timestamp = FIXED_CLOCK_TIME + 10

        assert await queue._aprune_expired() == 0
        assert (await queue.aget_entry(entry_id)).id == entry_id
        assert await queue._provider.aremove(entry_id, owner)

    asyncio.run(exercise())


def test_memory_event_queues_are_local_to_their_instances():
    first = MemoryEventQueue(queue_name="events")
    second = MemoryEventQueue(queue_name="events")

    async def exercise():
        await first.aenqueue("event")
        with pytest.raises(QueueEmptyException):
            await second._provider.aclaim_unexpired(uuid7())

    asyncio.run(exercise())


def test_memory_event_lifetime_pauses_while_claimed():
    clock = FixedClock()
    queue = MemoryEventQueue(queue_name="events", clock=clock)

    async def exercise():
        entry_id = await queue.aenqueue("event", timeout_seconds=10)
        owner = uuid7()
        assert (await queue._provider.aclaim_unexpired(owner)).id == entry_id

        clock.timestamp = FIXED_CLOCK_TIME + 100
        assert await queue._provider.arelease(entry_id, owner, delay_seconds=1)
        assert await queue._aprune_expired() == 0

        clock.timestamp = FIXED_CLOCK_TIME + 110
        assert await queue._aprune_expired() == 1
        with pytest.raises(QueueEntryNotFoundError):
            await queue.aget_entry(entry_id)

    asyncio.run(exercise())


def test_redis_event_lifetime_pauses_while_claimed(redis_client):
    queue = RedisEventQueue(redis_client)

    async def exercise():
        entry_id = await queue.aenqueue("event", timeout_seconds=0.1)
        owner = uuid4()
        assert (await queue._provider.aclaim_unexpired(owner)).id == entry_id

        await asyncio.sleep(0.15)
        assert await queue._provider.arelease(entry_id, owner, delay_seconds=0.001)
        assert (await queue.aget_entry(entry_id)).id == entry_id
        assert await queue._aprune_expired() == 0

        await asyncio.sleep(0.12)
        assert await queue._aprune_expired() == 1
        with pytest.raises(QueueEntryNotFoundError):
            await queue.aget_entry(entry_id)
        await queue.aclose()

    asyncio.run(exercise())


def test_clearing_a_memory_event_queue_removes_raw_items_and_event_entries():
    queue = MemoryEventQueue(queue_name="events")
    queue.add("raw item")
    entry_id = queue.enqueue("event")

    queue.clear()

    assert queue.size() == 0
    with pytest.raises(QueueEntryNotFoundError):
        queue.get_entry(entry_id)
