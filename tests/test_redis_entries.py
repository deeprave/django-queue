from uuid import uuid4

import pytest

from django_queue.backends import (
    RedisPriorityQueue,
    RedisQueue,
    RedisQueueJson,
    RedisStack,
)
from django_queue.backends.exceptions import QueueEmptyException
from django_queue.entries import QueueEntryStatus


@pytest.fixture
def redis_entry_queue(redis_client):
    return RedisQueue(redis_client, queue_name=f"entry-contract-{uuid4().hex}")


class TestRedisQueueEntries:
    def test_persists_an_identified_entry_separately_from_raw_queue_values(self, redis_entry_queue):
        redis_entry_queue.add("raw-value")

        entry_id = redis_entry_queue.enqueue({"request_id": 42})

        entry = redis_entry_queue.get_entry(entry_id)
        assert entry.id == entry_id
        assert entry.queue == redis_entry_queue._queue_name
        assert entry.status is QueueEntryStatus.QUEUED
        assert entry.payload == {"request_id": 42}
        assert redis_entry_queue.get() == "raw-value"

    def test_dequeues_an_entry_atomically_from_pending_work(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")

        entry = redis_entry_queue.dequeue_entry()

        assert entry.id == entry_id
        with pytest.raises(QueueEmptyException):
            redis_entry_queue.dequeue_entry()

    def test_dequeues_entries_in_fifo_order(self, redis_entry_queue):
        first_id = redis_entry_queue.enqueue("first")
        latest_id = redis_entry_queue.enqueue("latest")

        assert redis_entry_queue.dequeue_entry().id == first_id
        assert redis_entry_queue.dequeue_entry().id == latest_id

    def test_dequeues_entries_with_the_configured_encoding(self, redis_client):
        queue = RedisQueue(redis_client, queue_name=f"entry-encoding-{uuid4().hex}", encoding="utf-16")
        entry_id = queue.enqueue("work")

        assert queue.dequeue_entry().id == entry_id

    def test_records_redis_timed_lifecycle_outcomes(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")

        running = redis_entry_queue.mark_running(entry_id)
        completed = redis_entry_queue.mark_succeeded(entry_id, {"ok": True})

        assert running.status is QueueEntryStatus.RUNNING
        assert running.dispatched_at is not None
        assert completed.status is QueueEntryStatus.SUCCEEDED
        assert completed.finished_at is not None
        assert completed.result == {"ok": True}


@pytest.mark.parametrize(
    "queue_type",
    [RedisQueueJson, RedisStack, RedisPriorityQueue],
    ids=["json", "stack", "priority"],
)
def test_redis_queue_variants_support_identified_entries(redis_client, queue_type):
    queue = queue_type(redis_client, queue_name=f"entry-variant-{uuid4().hex}")

    entry_id = queue.enqueue({"value": "work"})
    entry = queue.dequeue_entry()
    queue.mark_running(entry_id)
    completed = queue.mark_succeeded(entry_id, {"ok": True})

    assert entry.id == entry_id
    assert completed.status is QueueEntryStatus.SUCCEEDED


@pytest.mark.parametrize(
    ("queue_type", "expected_first_payload"),
    [(RedisQueue, "first"), (RedisStack, "latest")],
    ids=["queue-fifo", "stack-lifo"],
)
def test_redis_entry_dispatch_order_matches_queue_variant(redis_client, queue_type, expected_first_payload):
    queue = queue_type(redis_client, queue_name=f"entry-order-{uuid4().hex}")
    queue.enqueue("first")
    queue.enqueue("latest")

    assert queue.dequeue_entry().payload == expected_first_payload
