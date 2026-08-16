import asyncio
import threading
from uuid import uuid4

import pytest

from django_queue.backends import MemoryAsyncQueue, MemoryEventQueue
from django_queue.backends.memory import MemoryEventQueueWorker
from django_queue.backends.redis import RedisEventQueue, RedisEventQueueWorker
from django_queue.entries import QueueEntry
from django_queue.listeners import ListenerRegistration, reset_listeners
from tests.helpers import FIXED_CLOCK_TIME, FixedClock


def test_event_worker_removes_a_consumed_event(monkeypatch):
    queue = MemoryEventQueue(queue_name="events")
    received = []

    async def receive(entry):
        received.append(entry.payload)
        return True

    monkeypatch.setattr(
        "django_queue.event_worker.listeners_for",
        lambda queue_name: (ListenerRegistration(receive),),
    )

    async def exercise():
        await queue.aenqueue("event")
        assert await MemoryEventQueueWorker(queue).adispatch_once()
        assert received == ["event"]
        assert not await queue.ahas_pending_entries()
        await queue.aclose()

    asyncio.run(exercise())
    reset_listeners()


def test_event_worker_rejects_an_async_queue():
    with pytest.raises(TypeError, match="EventQueueWorker requires an EventQueue"):
        MemoryEventQueueWorker(MemoryAsyncQueue())


def test_event_worker_rotates_after_the_listener_that_consumed_an_event(monkeypatch):
    queue = MemoryEventQueue(queue_name="events")
    received = []

    async def first(entry):
        received.append(("first", entry.payload))
        return True

    async def second(entry):
        received.append(("second", entry.payload))
        return True

    monkeypatch.setattr(
        "django_queue.event_worker.listeners_for",
        lambda queue_name: (ListenerRegistration(first), ListenerRegistration(second)),
    )

    async def exercise():
        await queue.aenqueue("one")
        await queue.aenqueue("two")
        worker = MemoryEventQueueWorker(queue)
        assert await worker.adispatch_once()
        assert await worker.adispatch_once()
        await queue.aclose()

    asyncio.run(exercise())

    assert received == [("first", "one"), ("second", "two")]


def test_event_worker_skips_filters_and_removes_rejections(monkeypatch, caplog):
    queue = MemoryEventQueue(queue_name="events")
    visited = []

    def filtered(entry):
        visited.append("filtered")
        return True

    def reject(entry):
        visited.append("reject")
        return False

    monkeypatch.setattr(
        "django_queue.event_worker.listeners_for",
        lambda queue_name: (
            ListenerRegistration(filtered, filter=lambda entry: False),
            ListenerRegistration(reject),
        ),
    )

    async def exercise():
        await queue.aenqueue("event")
        assert await MemoryEventQueueWorker(queue).adispatch_once()
        assert not await queue.ahas_pending_entries()
        await queue.aclose()

    asyncio.run(exercise())

    assert visited == ["reject"]
    assert "Event listener rejected event" in caplog.text


def test_event_worker_retries_an_all_pass_event_after_its_delay(monkeypatch):
    clock = FixedClock()
    queue = MemoryEventQueue(queue_name="events", clock=clock)
    calls = []

    def pass_event(entry):
        calls.append(entry.id)

    monkeypatch.setattr(
        "django_queue.event_worker.listeners_for",
        lambda queue_name: (ListenerRegistration(pass_event),),
    )

    async def exercise():
        await queue.aenqueue("event")
        worker = MemoryEventQueueWorker(queue)
        assert await worker.adispatch_once()
        assert not await worker.adispatch_once()
        clock.timestamp = FIXED_CLOCK_TIME + worker.release_delay
        assert await worker.adispatch_once()
        await queue.aclose()

    asyncio.run(exercise())

    assert len(calls) == 2


def test_event_worker_retries_a_listener_failure_after_its_delay(monkeypatch, caplog):
    clock = FixedClock()
    queue = MemoryEventQueue(queue_name="events", clock=clock)
    calls = []

    def fail(entry):
        calls.append(entry.id)
        raise RuntimeError("listener error")

    monkeypatch.setattr(
        "django_queue.event_worker.listeners_for",
        lambda queue_name: (ListenerRegistration(fail),),
    )

    async def exercise():
        await queue.aenqueue("event")
        worker = MemoryEventQueueWorker(queue)
        assert await worker.adispatch_once()
        clock.timestamp = FIXED_CLOCK_TIME + worker.release_delay
        assert await worker.adispatch_once()
        await queue.aclose()

    asyncio.run(exercise())

    assert len(calls) == 2
    assert "Event listener failed; releasing event for retry" in caplog.text


def test_event_worker_runs_sync_listeners_off_its_event_loop(monkeypatch):
    queue = MemoryEventQueue(queue_name="events")
    listener_thread_ids = []

    def receive(entry):
        listener_thread_ids.append(threading.get_ident())
        return True

    monkeypatch.setattr(
        "django_queue.event_worker.listeners_for",
        lambda queue_name: (ListenerRegistration(receive),),
    )

    async def exercise():
        await queue.aenqueue("event")
        loop_thread_id = threading.get_ident()
        assert await MemoryEventQueueWorker(queue).adispatch_once()
        await queue.aclose()
        return loop_thread_id

    loop_thread_id = asyncio.run(exercise())

    assert listener_thread_ids and listener_thread_ids != [loop_thread_id]


def test_event_worker_treats_a_failed_renewal_as_lost_ownership(monkeypatch, caplog):
    class FailingRenewalWorker(MemoryEventQueueWorker):
        def __init__(self, queue, entry: QueueEntry) -> None:
            super().__init__(queue)
            self.entry = entry

        async def _next_event(self) -> tuple[QueueEntry, float | None] | None:
            return self.entry, 1

        async def _renew_claim(self, entry: QueueEntry, lease_seconds: float) -> bool:
            raise RuntimeError("renewal unavailable")

    queue = MemoryEventQueue(queue_name="events")
    received = []

    async def receive(entry):
        await asyncio.sleep(0)
        received.append(entry.payload)
        return True

    monkeypatch.setattr(
        "django_queue.event_worker.listeners_for",
        lambda queue_name: (ListenerRegistration(receive),),
    )

    async def exercise():
        entry_id = await queue.aenqueue("event")
        entry = await queue.aget_entry(entry_id)
        assert await FailingRenewalWorker(queue, entry).adispatch_once()
        assert await queue.ahas_pending_entries()
        await queue.aclose()

    asyncio.run(exercise())

    assert received == ["event"]
    assert "Event claim renewal failed" in caplog.text


def test_redis_event_worker_dispatches_an_event(redis_client, monkeypatch):
    queue = RedisEventQueue(redis_client, queue_name=f"events-{uuid4().hex}")
    received = []

    async def receive(entry):
        received.append(entry.payload)
        return True

    monkeypatch.setattr(
        "django_queue.event_worker.listeners_for",
        lambda queue_name: (ListenerRegistration(receive),),
    )

    async def exercise():
        await queue.aenqueue("event")
        assert await RedisEventQueueWorker(queue).adispatch_once()
        assert not await queue.ahas_pending_entries()
        await queue.aclose()

    asyncio.run(exercise())

    assert received == ["event"]
