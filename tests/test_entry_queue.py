from datetime import UTC, datetime

import pytest

from django_queue.backends import MemoryPriorityQueue, MemoryQueue, MemoryStack
from django_queue.backends.exceptions import QueueEmptyException
from django_queue.entries import QueueEntryStatus
from tests.helpers import FixedClock


@pytest.fixture
def queue():
    return MemoryQueue(queue_name="requests", clock=FixedClock())


class TestMemoryQueueEntries:
    def test_enqueue_returns_an_id_and_persists_queued_entry(self, queue):
        entry_id = queue.enqueue({"request_id": 42})

        entry = queue.get_entry(entry_id)

        assert entry.id == entry_id
        assert entry.queue == "requests"
        assert entry.status is QueueEntryStatus.QUEUED
        assert entry.queued_at == datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
        assert entry.payload == {"request_id": 42}

    def test_dequeue_removes_the_entry_from_pending_work_but_retains_its_record(self, queue):
        entry_id = queue.enqueue("work")

        entry = queue.dequeue_entry()

        assert entry.id == entry_id
        assert queue.get_entry(entry_id) == entry
        with pytest.raises(QueueEmptyException):
            queue.dequeue_entry()

    def test_records_lifecycle_transitions(self, queue):
        entry_id = queue.enqueue("work")
        running = queue.mark_running(entry_id)
        completed = queue.mark_succeeded(entry_id, {"ok": True})

        assert running.status is QueueEntryStatus.RUNNING
        assert running.dispatched_at == datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
        assert completed.status is QueueEntryStatus.SUCCEEDED
        assert completed.result == {"ok": True}
        assert completed.finished_at == datetime(2026, 8, 6, 12, 0, tzinfo=UTC)

    def test_records_a_safe_failure_without_a_traceback(self, queue):
        entry_id = queue.enqueue("work")
        queue.mark_running(entry_id)

        failed = queue.mark_failed(entry_id, ValueError("invalid request"))

        assert failed.status is QueueEntryStatus.FAILED
        assert failed.error == {"type": "ValueError", "message": "invalid request"}

    def test_rejects_invalid_lifecycle_transitions(self, queue):
        entry_id = queue.enqueue("work")

        with pytest.raises(ValueError, match="queued.*succeeded"):
            queue.mark_succeeded(entry_id, "done")

        queue.mark_running(entry_id)
        queue.mark_succeeded(entry_id, "done")
        with pytest.raises(ValueError, match="succeeded.*failed"):
            queue.mark_failed(entry_id, ValueError("too late"))

    def test_records_cancellation_as_a_terminal_outcome(self, queue):
        entry_id = queue.enqueue("work")
        queue.mark_running(entry_id)

        cancelled = queue.mark_cancelled(entry_id)

        assert cancelled.status is QueueEntryStatus.CANCELLED
        assert cancelled.finished_at == datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def test_memory_priority_queue_supports_identified_entries():
    queue = MemoryPriorityQueue(queue_name="priority")

    entry_id = queue.enqueue({"value": "work"})
    entry = queue.dequeue_entry()

    assert entry.id == entry_id


def test_memory_stack_dequeues_newest_entry_first():
    queue = MemoryStack(queue_name="stack")

    first_id = queue.enqueue("first")
    latest_id = queue.enqueue("latest")

    assert queue.dequeue_entry().id == latest_id
    assert queue.dequeue_entry().id == first_id
