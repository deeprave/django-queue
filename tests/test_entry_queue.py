import pytest

from django_queue.backends import MemoryPriorityQueue, MemoryQueue, MemoryStack
from django_queue.backends.exceptions import QueueEmptyException
from django_queue.entries import QueueEntryStatus
from django_queue.signals import entry_enqueued
from tests.helpers import FIXED_CLOCK_TIME, CustomQueueEntry, FixedClock


# MemoryPriorityQueue takes its entry methods from MemoryQueue by class-level
# assignment rather than inheritance, so the borrow list is only as correct as
# the coverage that calls through it. Running the lifecycle against both classes
# catches a method bound to the wrong original, which the abstract base cannot.
@pytest.fixture(params=[MemoryQueue, MemoryPriorityQueue], ids=["fifo", "priority"])
def queue(request):
    return request.param(queue_name="requests", clock=FixedClock())


class TestMemoryQueueEntries:
    def test_enqueue_survives_a_failing_entry_observer(self, queue):
        def failing_receiver(sender, **kwargs):
            raise RuntimeError("observer failed")

        entry_enqueued.connect(failing_receiver, weak=False)
        try:
            entry_id = queue.enqueue("work")
        finally:
            entry_enqueued.disconnect(failing_receiver)

        assert queue.get_entry(entry_id).payload == "work"

    def test_enqueue_returns_an_id_and_persists_queued_entry(self, queue):
        entry_id = queue.enqueue({"request_id": 42})

        entry = queue.get_entry(entry_id)

        assert entry.id == entry_id
        assert entry.queue == "requests"
        assert entry.status is QueueEntryStatus.QUEUED
        assert entry.queued_at == FIXED_CLOCK_TIME
        assert entry.payload == {"request_id": 42}

    def test_dequeue_removes_the_entry_from_pending_work_but_retains_its_record(
        self, queue
    ):
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
        assert running.dispatched_at == FIXED_CLOCK_TIME
        assert completed.status is QueueEntryStatus.SUCCEEDED
        assert completed.result == {"ok": True}
        assert completed.finished_at == FIXED_CLOCK_TIME

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
        assert cancelled.finished_at == FIXED_CLOCK_TIME

    def test_records_a_timeout_as_a_terminal_outcome(self, queue):
        """Distinct from cancellation: the handler never answered."""
        entry_id = queue.enqueue("work")
        queue.mark_running(entry_id)

        timed_out = queue.mark_timed_out(entry_id)

        assert timed_out.status is QueueEntryStatus.TIMEOUT
        assert timed_out.finished_at == FIXED_CLOCK_TIME

    def test_refuses_to_move_a_timed_out_entry_on(self, queue):
        entry_id = queue.enqueue("work")
        queue.mark_running(entry_id)
        queue.mark_timed_out(entry_id)

        with pytest.raises(ValueError, match="timeout"):
            queue.mark_succeeded(entry_id, "too late")

    def test_persists_an_execution_budget_given_at_enqueue(self, queue):
        entry_id = queue.enqueue("work", timeout_seconds=2.5)

        assert queue.get_entry(entry_id).timeout_seconds == 2.5

    def test_carries_no_budget_when_none_was_given(self, queue):
        assert queue.get_entry(queue.enqueue("work")).timeout_seconds is None

    @pytest.mark.parametrize("budget", [0, -1, float("nan"), float("inf")])
    def test_refuses_to_enqueue_an_invalid_budget(self, queue, budget):
        """Rejected where it is supplied, not when a worker comes to apply it."""
        with pytest.raises(ValueError, match="Execution budget"):
            queue.enqueue("work", timeout_seconds=budget)

    def test_rejects_a_budget_on_the_item_oriented_api(self, queue):
        """`add` stores raw values and dispatches nothing, so a budget is meaningless."""
        with pytest.raises(TypeError, match="timeout_seconds"):
            queue.add("work", timeout_seconds=2.5)

    def test_uses_its_configured_entry_subclass_for_lifecycle_operations(self, queue):
        queue.entry_class = CustomQueueEntry

        entry_id = queue.enqueue("work")
        queued = queue.get_entry(entry_id)
        running = queue.mark_running(entry_id)
        completed = queue.mark_succeeded(entry_id, "done")

        assert isinstance(queued, CustomQueueEntry)
        assert isinstance(running, CustomQueueEntry)
        assert isinstance(completed, CustomQueueEntry)
        assert completed.kind == "task"


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
