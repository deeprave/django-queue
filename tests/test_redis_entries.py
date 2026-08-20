import asyncio
import threading
import time
from uuid import uuid4

import pytest

import django_queue
from django_queue import queue_observer
from django_queue.backends.exceptions import (
    QueueEmptyException,
    QueueEntryNotFoundError,
)
from django_queue.backends.redis import RedisAsyncQueue, RedisAsyncQueueWorker
from django_queue.entries import QueueEntryStatus
from django_queue.observers import _discard_observers_for
from django_queue.queue_runtime import queue_runtime
from tests.helpers import CustomQueueEntry


@pytest.fixture
def redis_entry_queue(redis_client):
    return RedisAsyncQueue(redis_client, queue_name=f"entries-{uuid4().hex}")


async def _run_until_terminal(queue, entry_id, handler):
    worker = RedisAsyncQueueWorker(
        {"requests": queue}, {"requests": handler}, idle_delay=0.001
    )
    task = asyncio.create_task(worker.run())
    try:
        while (await queue.afind(entry_id)).status not in {
            QueueEntryStatus.SUCCEEDED,
            QueueEntryStatus.FAILED,
        }:
            await asyncio.sleep(0.001)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_find_reports_a_missing_retained_record(redis_entry_queue):
    with pytest.raises(QueueEntryNotFoundError):
        redis_entry_queue.find(uuid4())


def test_prunes_a_terminal_entry(redis_entry_queue):
    async def exercise():
        entry_id = await redis_entry_queue.aenqueue("work")

        async def handle(entry):
            return entry.payload

        await _run_until_terminal(redis_entry_queue, entry_id, handle)
        await redis_entry_queue.aclose()
        return entry_id

    entry_id = asyncio.run(exercise())

    redis_entry_queue.prune(entry_id)

    with pytest.raises(QueueEntryNotFoundError):
        redis_entry_queue.find(entry_id)


def test_prune_refuses_a_non_terminal_entry(redis_entry_queue):
    entry_id = redis_entry_queue.enqueue("work")

    with pytest.raises(ValueError, match="terminal"):
        redis_entry_queue.prune(entry_id)

    assert redis_entry_queue.find(entry_id).status is QueueEntryStatus.QUEUED


def test_list_returns_retained_entry_snapshots(redis_client):
    queue = RedisAsyncQueue(redis_client, queue_name=f"list-{uuid4().hex}")

    async def exercise():
        completed_id = await queue.aenqueue("completed")
        queued_id = await queue.aenqueue("queued")

        async def handle(entry):
            return entry.payload

        await _run_until_terminal(queue, completed_id, handle)
        entries = await queue.alist()
        await queue.aclose()
        return completed_id, queued_id, entries

    completed_id, queued_id, entries = asyncio.run(exercise())

    assert {entry.id for entry in entries} == {completed_id, queued_id}
    assert {entry.status for entry in entries if entry.id == completed_id} == {
        QueueEntryStatus.SUCCEEDED
    }


def test_direct_dequeue_is_atomic_and_fifo(redis_entry_queue):
    first_id = redis_entry_queue.enqueue("first")
    second_id = redis_entry_queue.enqueue("second")

    assert redis_entry_queue.dequeue().id == first_id
    assert redis_entry_queue.dequeue().id == second_id
    with pytest.raises(QueueEmptyException):
        redis_entry_queue.dequeue()


def test_raw_values_and_retained_entries_are_independent(redis_entry_queue):
    redis_entry_queue.add("raw-value")
    entry_id = redis_entry_queue.enqueue({"request_id": 42})

    assert redis_entry_queue.get() == "raw-value"
    assert redis_entry_queue.find(entry_id).payload == {"request_id": 42}


def test_adelete_does_not_create_a_priority_sequence_key_for_a_plain_queue(
    redis_entry_queue,
):
    """adelete unconditionally calls adiscard_priority as one of its cleanup
    steps (RedisAsyncQueue never populates the priority store, but adelete's
    contract is to clean up every store an entry could be sitting in). That
    call must not create a stray, otherwise-unused sequence key on a queue
    type that never uses the priority path at all."""

    async def scenario():
        try:
            entry_id = await redis_entry_queue.aenqueue("work")
            await redis_entry_queue._provider.adelete(entry_id)
            client = redis_entry_queue._provider._async_redis()
            return await client.get(
                redis_entry_queue._provider._entry_pending_priority_sequence_name
            )
        finally:
            await redis_entry_queue.aclose()

    assert asyncio.run(scenario()) is None


def test_aenqueue_routes_through_the_atomic_store_and_push_path(redis_entry_queue):
    """aenqueue()'s astore()+apush() split was previously two separate
    round-trips -- a crash between them left a durably stored entry with no
    pending-store index pointing to it. RedisAsyncQueue._astore_and_push
    should route through the atomic astore_and_push() script instead,
    never calling the plain, non-atomic astore()/apush() individually.
    Monkeypatching astore()/apush() to raise proves aenqueue() never calls
    them for a Redis-backed queue."""
    provider = redis_entry_queue._provider

    async def _fail(*args, **kwargs):
        raise AssertionError("aenqueue() must not call the non-atomic astore/apush")

    provider.astore = _fail
    provider.apush = _fail

    entry_id = redis_entry_queue.enqueue("work")

    assert redis_entry_queue.find(entry_id).id == entry_id
    assert redis_entry_queue.dequeue().id == entry_id


def test_redis_queue_restores_the_configured_entry_class(redis_client):
    queue = RedisAsyncQueue(
        redis_client,
        queue_name=f"entry-class-{uuid4().hex}",
        entry_class=CustomQueueEntry,
    )

    async def exercise():
        entry_id = await queue.aenqueue("work")
        entry = await queue.afind(entry_id)
        await queue.aclose()
        return entry

    assert isinstance(asyncio.run(exercise()), CustomQueueEntry)


def test_redis_worker_records_success(redis_client):
    queue = RedisAsyncQueue(redis_client, queue_name=f"success-{uuid4().hex}")

    async def exercise():
        entry_id = await queue.aenqueue("work")

        async def handle(entry):
            return entry.payload

        await _run_until_terminal(queue, entry_id, handle)
        entry = await queue.afind(entry_id)
        await queue.aclose()
        return entry

    entry = asyncio.run(exercise())

    assert entry.status is QueueEntryStatus.SUCCEEDED
    assert entry.result == "work"


def test_redis_worker_records_failure(redis_client):
    queue = RedisAsyncQueue(redis_client, queue_name=f"failure-{uuid4().hex}")

    async def exercise():
        entry_id = await queue.aenqueue("work")

        async def handle(entry):
            raise ValueError(entry.payload)

        await _run_until_terminal(queue, entry_id, handle)
        entry = await queue.afind(entry_id)
        await queue.aclose()
        return entry

    entry = asyncio.run(exercise())

    assert entry.status is QueueEntryStatus.FAILED
    assert entry.error == {"type": "ValueError", "message": "work"}


def test_pruning_publishes_a_terminated_snapshot_to_an_observer(
    redis_client, monkeypatch
):
    queue_runtime.start_thread()
    handler = django_queue.QueueRegistry(
        {
            "requests": {
                "BACKEND": "django_queue.backends.redis.RedisAsyncQueue",
                "LOCATION": redis_client,
            }
        }
    )
    monkeypatch.setattr(django_queue, "queues", handler)
    queue = handler["requests"]
    terminated = threading.Event()
    snapshots = []
    subscription = queue_observer(
        "requests",
        lambda entry: (
            snapshots.append(entry),
            terminated.set() if entry.status is QueueEntryStatus.TERMINATED else None,
        ),
    )
    try:

        async def complete_entry():
            entry_id = await queue.aenqueue([])

            async def handle(entry):
                return "done"

            await _run_until_terminal(queue, entry_id, handle)
            await queue.aclose()
            return entry_id

        entry_id = asyncio.run(complete_entry())
        time.sleep(0.05)
        queue.prune(entry_id)

        assert terminated.wait(1)
        assert snapshots[-1].status is QueueEntryStatus.TERMINATED
        assert snapshots[-1].payload == []
    finally:
        subscription.unsubscribe()
        queue_runtime.stop_one("requests")
        _discard_observers_for("requests")
