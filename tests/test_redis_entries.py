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
        while (await queue.aget_entry(entry_id)).status not in {
            QueueEntryStatus.SUCCEEDED,
            QueueEntryStatus.FAILED,
        }:
            await asyncio.sleep(0.001)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_get_entry_reports_a_missing_retained_entry(redis_entry_queue):
    with pytest.raises(QueueEntryNotFoundError):
        redis_entry_queue.get_entry(uuid4())


def test_prunes_a_terminal_entry(redis_entry_queue):
    async def exercise():
        entry_id = await redis_entry_queue.aenqueue("work")

        async def handle(entry):
            return entry.payload

        await _run_until_terminal(redis_entry_queue, entry_id, handle)
        await redis_entry_queue.aclose()
        return entry_id

    entry_id = asyncio.run(exercise())

    redis_entry_queue.prune_entry(entry_id)

    with pytest.raises(QueueEntryNotFoundError):
        redis_entry_queue.get_entry(entry_id)


def test_prune_refuses_a_non_terminal_entry(redis_entry_queue):
    entry_id = redis_entry_queue.enqueue("work")

    with pytest.raises(ValueError, match="terminal"):
        redis_entry_queue.prune_entry(entry_id)

    assert redis_entry_queue.get_entry(entry_id).status is QueueEntryStatus.QUEUED


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

    assert redis_entry_queue.dequeue_entry().id == first_id
    assert redis_entry_queue.dequeue_entry().id == second_id
    with pytest.raises(QueueEmptyException):
        redis_entry_queue.dequeue_entry()


def test_raw_values_and_retained_entries_are_independent(redis_entry_queue):
    redis_entry_queue.add("raw-value")
    entry_id = redis_entry_queue.enqueue({"request_id": 42})

    assert redis_entry_queue.get() == "raw-value"
    assert redis_entry_queue.get_entry(entry_id).payload == {"request_id": 42}


def test_redis_queue_restores_the_configured_entry_class(redis_client):
    queue = RedisAsyncQueue(
        redis_client,
        queue_name=f"entry-class-{uuid4().hex}",
        entry_class=CustomQueueEntry,
    )

    async def exercise():
        entry_id = await queue.aenqueue("work")
        entry = await queue.aget_entry(entry_id)
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
        entry = await queue.aget_entry(entry_id)
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
        entry = await queue.aget_entry(entry_id)
        await queue.aclose()
        return entry

    entry = asyncio.run(exercise())

    assert entry.status is QueueEntryStatus.FAILED
    assert entry.error == {"type": "ValueError", "message": "work"}


def test_pruning_publishes_a_terminated_snapshot_to_an_observer(
    redis_client, monkeypatch
):
    handler = django_queue.QueueHandler(
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
        queue.prune_entry(entry_id)

        assert terminated.wait(1)
        assert snapshots[-1].status is QueueEntryStatus.TERMINATED
        assert snapshots[-1].payload == []
    finally:
        subscription.unsubscribe()
