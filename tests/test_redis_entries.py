from uuid import uuid4

import pytest

from django_queue.backends import (
    RedisPriorityQueue,
    RedisQueue,
    RedisQueueJson,
    RedisStack,
)
from django_queue.backends.exceptions import QueueEmptyException
from django_queue.clock import ClockTime
from django_queue.entries import QueueEntryStatus
from tests.helpers import CustomQueueEntry


@pytest.fixture
def redis_entry_queue(redis_client):
    return RedisQueue(redis_client, queue_name=f"entry-contract-{uuid4().hex}")


class TestRedisQueueEntries:
    def test_persists_an_identified_entry_separately_from_raw_queue_values(
        self, redis_entry_queue
    ):
        redis_entry_queue.add("raw-value")

        entry_id = redis_entry_queue.enqueue({"request_id": 42})

        entry = redis_entry_queue.get_entry(entry_id)
        assert entry.id == entry_id
        assert entry.queue == redis_entry_queue.queue_name
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
        queue = RedisQueue(
            redis_client, queue_name=f"entry-encoding-{uuid4().hex}", encoding="utf-16"
        )
        entry_id = queue.enqueue("work")

        assert queue.dequeue_entry().id == entry_id

    def test_records_redis_timed_lifecycle_outcomes(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")

        running = redis_entry_queue.mark_running(entry_id)
        completed = redis_entry_queue.mark_succeeded(entry_id, {"ok": True})

        assert running.status is QueueEntryStatus.RUNNING
        assert isinstance(running.dispatched_at, ClockTime)
        assert completed.status is QueueEntryStatus.SUCCEEDED
        assert isinstance(completed.finished_at, ClockTime)
        assert completed.result == {"ok": True}

    def test_records_a_redis_timeout_as_a_terminal_outcome(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")
        redis_entry_queue.mark_running(entry_id)

        timed_out = redis_entry_queue.mark_timed_out(entry_id)

        assert timed_out.status is QueueEntryStatus.TIMEOUT
        assert isinstance(timed_out.finished_at, ClockTime)

    def test_persists_a_redis_execution_budget(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work", timeout_seconds=2.5)

        assert redis_entry_queue.get_entry(entry_id).timeout_seconds == 2.5

    def test_restores_redis_lifecycle_timestamps_to_equal_instants(
        self, redis_entry_queue
    ):
        """Round-trips through the durable form, not just through memory."""
        entry_id = redis_entry_queue.enqueue("work")
        running = redis_entry_queue.mark_running(entry_id)

        restored = redis_entry_queue.get_entry(entry_id)

        assert restored.queued_at == running.queued_at
        assert restored.dispatched_at == running.dispatched_at

    def test_uses_its_configured_entry_subclass_for_lifecycle_operations(
        self, redis_entry_queue
    ):
        redis_entry_queue.entry_class = CustomQueueEntry

        entry_id = redis_entry_queue.enqueue("work")
        queued = redis_entry_queue.get_entry(entry_id)
        running = redis_entry_queue.mark_running(entry_id)
        completed = redis_entry_queue.mark_succeeded(entry_id, "done")

        assert isinstance(queued, CustomQueueEntry)
        assert isinstance(running, CustomQueueEntry)
        assert isinstance(completed, CustomQueueEntry)
        assert completed.kind == "task"


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
def test_redis_entry_dispatch_order_matches_queue_variant(
    redis_client, queue_type, expected_first_payload
):
    queue = queue_type(redis_client, queue_name=f"entry-order-{uuid4().hex}")
    queue.enqueue("first")
    queue.enqueue("latest")

    assert queue.dequeue_entry().payload == expected_first_payload
