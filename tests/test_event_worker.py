import asyncio
import threading
from uuid import uuid7

import pytest

from django_queue.backends import (
    MemoryAsyncQueue,
    MemoryEventQueue,
)
from django_queue.backends.exceptions import QueueEmptyException
from django_queue.backends.memory import (
    MemoryEventQueueWorker as EventQueueWorker,
)
from django_queue.backends.redis import RedisEventQueue, RedisEventQueueWorker
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
        worker = EventQueueWorker(queue)
        await worker.adispatch_once()
        assert received == ["event"]
        assert not await queue.ahas_pending_entries()
        await queue.aclose()

    asyncio.run(exercise())
    reset_listeners()


def test_event_worker_uses_its_queue_owned_provider_for_delivery(monkeypatch):
    queue = MemoryEventQueue(queue_name="events")

    async def consume(_entry):
        return True

    monkeypatch.setattr(
        "django_queue.event_worker.listeners_for",
        lambda queue_name: (ListenerRegistration(consume),),
    )

    async def exercise():
        await queue.aenqueue("event")
        assert await EventQueueWorker(queue).adispatch_once()
        assert not hasattr(queue, "_aclaim")
        assert not hasattr(queue, "_aremove")

    asyncio.run(exercise())


def test_event_workers_use_the_queue_runtime_identity():
    queue = MemoryEventQueue(queue_name="events")

    first = EventQueueWorker(queue)
    second = EventQueueWorker(queue)

    assert first._worker_id == queue._worker_id_for_runtime()
    assert second._worker_id == queue._worker_id_for_runtime()


def test_event_queue_regenerates_its_runtime_identity_after_a_fork(monkeypatch):
    monkeypatch.setattr("django_queue.backends.base.os.getpid", lambda: 100)
    queue = MemoryEventQueue(queue_name="events")
    parent_id = queue._worker_id_for_runtime()

    monkeypatch.setattr("django_queue.backends.base.os.getpid", lambda: 200)
    child_id = queue._worker_id_for_runtime()

    assert child_id != parent_id
    assert queue._worker_id_for_runtime() == child_id


def test_event_worker_rejects_an_async_task_queue():
    with pytest.raises(TypeError, match="EventQueueWorker requires an EventQueue"):
        EventQueueWorker(MemoryAsyncQueue())


def test_event_worker_skips_an_event_that_expires_before_its_claim(monkeypatch):
    clock = FixedClock()
    queue = MemoryEventQueue(queue_name="events", clock=clock)
    received = []

    async def receive(entry):
        received.append(entry.payload)
        return True

    async def skip_pruning():
        return 0

    monkeypatch.setattr(
        "django_queue.event_worker.listeners_for",
        lambda queue_name: (ListenerRegistration(receive),),
    )
    monkeypatch.setattr(queue, "_aprune_expired", skip_pruning)

    async def exercise():
        await queue.aenqueue("event", timeout_seconds=1)
        clock.timestamp = FIXED_CLOCK_TIME + 1

        assert not await EventQueueWorker(queue).adispatch_once()
        assert received == []
        assert not await queue._provider.ahas_pending()

    asyncio.run(exercise())


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
        lambda queue_name: (
            ListenerRegistration(first),
            ListenerRegistration(second),
        ),
    )

    async def exercise():
        await queue.aenqueue("one")
        await queue.aenqueue("two")
        worker = EventQueueWorker(queue)
        assert await worker.adispatch_once()
        assert await worker.adispatch_once()
        assert received == [("first", "one"), ("second", "two")]

    asyncio.run(exercise())


def test_event_worker_skips_filtered_listeners_and_removes_rejections(
    monkeypatch, caplog
):
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
        worker = EventQueueWorker(queue)
        assert await worker.adispatch_once()
        assert visited == ["reject"]
        assert not await queue.ahas_pending_entries()

    asyncio.run(exercise())
    assert "Event listener rejected event" in caplog.text


def test_event_worker_releases_an_all_pass_event_after_the_fixed_delay(monkeypatch):
    clock = FixedClock()
    queue = MemoryEventQueue(queue_name="events", clock=clock)

    def pass_event(entry):
        return None

    monkeypatch.setattr(
        "django_queue.event_worker.listeners_for",
        lambda queue_name: (ListenerRegistration(pass_event),),
    )

    async def exercise():
        entry_id = await queue.aenqueue("event")
        worker = EventQueueWorker(queue)
        assert await worker.adispatch_once()
        with pytest.raises(QueueEmptyException):
            await queue._provider.aclaim_unexpired(uuid7())
        clock.timestamp = FIXED_CLOCK_TIME + worker.release_delay
        assert (await queue._provider.aclaim_unexpired(uuid7())).id == entry_id

    asyncio.run(exercise())


def test_event_worker_releases_an_event_when_its_listener_raises(monkeypatch, caplog):
    clock = FixedClock()
    queue = MemoryEventQueue(queue_name="events", clock=clock)

    def fail(entry):
        raise RuntimeError("listener error")

    monkeypatch.setattr(
        "django_queue.event_worker.listeners_for",
        lambda queue_name: (ListenerRegistration(fail),),
    )

    async def exercise():
        entry_id = await queue.aenqueue("event")
        worker = EventQueueWorker(queue)
        assert await worker.adispatch_once()
        clock.timestamp = FIXED_CLOCK_TIME + worker.release_delay
        assert (await queue._provider.aclaim_unexpired(uuid7())).id == entry_id

    asyncio.run(exercise())
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
        worker = EventQueueWorker(queue)
        loop_thread_id = threading.get_ident()
        assert await worker.adispatch_once()
        assert listener_thread_ids and listener_thread_ids != [loop_thread_id]

    asyncio.run(exercise())


def test_event_worker_logs_when_it_loses_its_claim_before_removal(monkeypatch, caplog):
    queue = MemoryEventQueue(queue_name="events")

    async def receive(entry):
        return True

    async def lose_claim(entry_id, worker_id):
        return False

    monkeypatch.setattr(
        "django_queue.event_worker.listeners_for",
        lambda queue_name: (ListenerRegistration(receive),),
    )
    monkeypatch.setattr(queue._provider, "aremove", lose_claim)

    async def exercise():
        await queue.aenqueue("event")
        assert await EventQueueWorker(queue).adispatch_once()

    asyncio.run(exercise())
    assert "Lost claim for event entry" in caplog.text


def test_event_worker_skips_a_missing_entry_and_keeps_dispatching(monkeypatch, caplog):
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
        missing_id = await queue.aenqueue("missing")
        # Simulate externally damaged state: the pending ID exists without its
        # corresponding record. Event workers must not die on this input.
        queue._provider._entries.pop(missing_id)
        worker = EventQueueWorker(queue)

        assert not await worker.adispatch_once()

        await queue.aenqueue("available")
        assert await worker.adispatch_once()
        assert received == ["available"]

    asyncio.run(exercise())
    assert "Skipping event entry that disappeared before dispatch" in caplog.text


def test_event_worker_discards_a_redis_claim_for_a_missing_entry(
    redis_client, monkeypatch, caplog
):
    queue = RedisEventQueue(redis_client)
    received = []

    async def receive(entry):
        received.append(entry.payload)
        return True

    monkeypatch.setattr(
        "django_queue.event_worker.listeners_for",
        lambda queue_name: (ListenerRegistration(receive),),
    )

    async def exercise():
        missing_id = await queue.aenqueue("missing")
        await queue._provider._async_redis().delete(
            queue._provider._entry_key(missing_id)
        )
        worker = RedisEventQueueWorker(queue)

        assert not await worker.adispatch_once()

        await queue.aenqueue("available")
        assert await worker.adispatch_once()
        assert received == ["available"]
        await queue.aclose()

    asyncio.run(exercise())
    assert "Discarding claim for missing event entry" in caplog.text


def test_event_worker_recovers_an_expired_redis_event_claim(
    redis_client, monkeypatch, caplog
):
    queue = RedisEventQueue(redis_client)
    queue.default_claim_lease_seconds = 0.001
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
        await queue._provider.aclaim_unexpired(uuid7(), lease_seconds=0.001)
        await asyncio.sleep(0.01)

        worker = RedisEventQueueWorker(queue)
        assert await worker.adispatch_once()
        assert received == ["event"]
        await queue.aclose()

    asyncio.run(exercise())
    assert "Recovered 1 expired event claim" in caplog.text


def test_event_worker_renews_a_redis_claim_while_a_listener_runs(
    redis_client, monkeypatch
):
    queue_name = f"events-{uuid7()}"
    queue = RedisEventQueue(redis_client, queue_name=queue_name)
    competing_queue = RedisEventQueue(redis_client, queue_name=queue_name)
    queue.default_claim_lease_seconds = 0.2
    listener_started = asyncio.Event()
    release_listener = asyncio.Event()

    async def receive(entry):
        listener_started.set()
        await release_listener.wait()
        return True

    monkeypatch.setattr(
        "django_queue.event_worker.listeners_for",
        lambda queue_name: (ListenerRegistration(receive),),
    )

    async def exercise():
        await queue.aenqueue("event")
        worker = RedisEventQueueWorker(queue)
        dispatch_task = asyncio.create_task(worker.adispatch_once())
        await listener_started.wait()
        await asyncio.sleep(0.15)

        assert await competing_queue._provider.arecover(
            competing_queue.recovery_batch_size
        ) == (0, 0)

        release_listener.set()
        assert await dispatch_task
        await queue.aclose()
        await competing_queue.aclose()

    asyncio.run(exercise())
