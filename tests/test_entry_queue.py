import asyncio
import threading
from dataclasses import replace
from uuid import uuid4

import pytest

import django_queue
from django_queue import queue_observer
from django_queue.backends import MemoryPriorityQueue, MemoryQueue, MemoryStack
from django_queue.backends.base import AsyncQueue, BaseQueue, EventQueue
from django_queue.backends.exceptions import (
    QueueEmptyException,
    QueueEntryNotFoundError,
)
from django_queue.entries import QueueEntryStatus
from django_queue.observers import (
    _EVENT_QUEUE_SIZE,
    _observers_for,
    _order_snapshots,
    _Registration,
)
from django_queue.signals import entry_enqueued
from django_queue.worker import AsyncQueueWorker
from tests.helpers import FIXED_CLOCK_TIME, CustomQueueEntry, FixedClock


# MemoryPriorityQueue takes its entry methods from MemoryQueue by class-level
# assignment rather than inheritance, so the borrow list is only as correct as
# the coverage that calls through it. Running the lifecycle against both classes
# catches a method bound to the wrong original, which the abstract base cannot.
@pytest.fixture(
    params=[MemoryQueue, MemoryPriorityQueue, MemoryStack],
    ids=["fifo", "priority", "stack"],
)
def queue(request):
    return request.param(queue_name="requests", clock=FixedClock())


@pytest.fixture(
    params=[MemoryQueue, MemoryPriorityQueue, MemoryStack],
    ids=["fifo", "priority", "stack"],
)
def observer_queue(request, monkeypatch):
    handler = django_queue.QueueHandler(
        {
            "requests": {
                "BACKEND": f"django_queue.backends.{request.param.__name__}",
                "LOCATION": "",
            }
        }
    )
    monkeypatch.setattr(django_queue, "queues", handler)
    return handler["requests"]


async def _event_queue_noop(*args, **kwargs):
    return None


async def _process_one(queue, queue_name="requests"):
    async def handle(entry):
        return entry.payload

    worker = AsyncQueueWorker(
        {queue_name: queue}, {queue_name: handle}, idle_delay=0.001
    )
    entry_id = await queue.aenqueue("work")
    task = asyncio.create_task(worker.run())
    while (await queue.aget_entry(entry_id)).status is not QueueEntryStatus.SUCCEEDED:
        await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


class ObserverEventQueue(EventQueue):
    capacity = 0
    aadd = aget = apoll = apeek = asize = aenqueue = aget_entry = _event_queue_noop
    adequeue_entry = ahas_pending_entries = _event_queue_noop
    amark_running = amark_succeeded = amark_failed = _event_queue_noop
    amark_cancelled = amark_timed_out = _event_queue_noop

    def __init__(self, _: str, options: dict) -> None:
        self._queue_name = options.pop("queue_name", "events")


class TestMemoryQueueEntries:
    def test_observer_routes_live_snapshots_by_backend_queue_name(self, monkeypatch):
        handler = django_queue.QueueHandler(
            {
                "alias": {
                    "BACKEND": "django_queue.backends.MemoryQueue",
                    "LOCATION": "",
                    "queue_name": "physical",
                }
            }
        )
        monkeypatch.setattr(django_queue, "queues", handler)
        observed_queue = handler["alias"]
        delivered = threading.Event()

        subscription = queue_observer("alias", lambda entry: delivered.set())
        asyncio.run(_process_one(observed_queue, "alias"))

        assert delivered.wait(1)
        subscription.unsubscribe()

    def test_observers_are_sequential_and_isolate_callback_failure(
        self, observer_queue, caplog
    ):
        delivered = threading.Event()
        calls = []

        def fail(entry):
            calls.append(("fail", entry.status))
            raise RuntimeError("expected observer failure")

        def succeed(entry):
            calls.append(("succeed", entry.status))
            if entry.status is QueueEntryStatus.SUCCEEDED:
                delivered.set()

        first = queue_observer("requests", fail)
        second = queue_observer("requests", succeed)
        asyncio.run(_process_one(observer_queue))

        assert delivered.wait(1)
        first.unsubscribe()
        second.unsubscribe()
        assert calls == [
            ("fail", QueueEntryStatus.QUEUED),
            ("succeed", QueueEntryStatus.QUEUED),
            ("fail", QueueEntryStatus.RUNNING),
            ("succeed", QueueEntryStatus.RUNNING),
            ("fail", QueueEntryStatus.SUCCEEDED),
            ("succeed", QueueEntryStatus.SUCCEEDED),
        ]
        assert "Queue lifecycle observer failed" in caplog.text

    def test_observer_receives_worker_lifecycle(self, observer_queue):
        statuses = []
        completed = threading.Event()

        def callback(entry):
            statuses.append(entry.status)
            if entry.status is QueueEntryStatus.SUCCEEDED:
                completed.set()

        subscription = queue_observer("requests", callback)

        asyncio.run(_process_one(observer_queue))

        assert completed.wait(1)
        subscription.unsubscribe()
        assert statuses == [
            QueueEntryStatus.QUEUED,
            QueueEntryStatus.RUNNING,
            QueueEntryStatus.SUCCEEDED,
        ]

    def test_worker_publishes_a_first_seen_terminal_entry(self, observer_queue):
        observed = threading.Event()
        statuses = []

        def callback(entry):
            statuses.append(entry.status)
            if entry.status is QueueEntryStatus.FAILED:
                observed.set()

        subscription = queue_observer("requests", callback)
        entry_id = observer_queue.enqueue("work")
        observer_queue.mark_failed(entry_id, ValueError("dispatch unavailable"))

        async def exercise():
            worker = AsyncQueueWorker(
                {"requests": observer_queue},
                {"requests": lambda entry: asyncio.sleep(0)},
                idle_delay=0.001,
            )
            task = asyncio.create_task(worker.run())
            assert await asyncio.to_thread(observed.wait, 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        try:
            asyncio.run(exercise())
        finally:
            subscription.unsubscribe()

        assert statuses == [QueueEntryStatus.FAILED]

    def test_worker_publishes_every_entry_created_between_scans(self, queue):
        observed_ids = []

        async def exercise():
            worker = AsyncQueueWorker(
                {"requests": queue},
                {"requests": lambda entry: asyncio.sleep(0)},
            )

            async def publish(entry):
                observed_ids.append(entry.id)

            queue.apublish_lifecycle_snapshot = publish
            first_id = await queue.aenqueue("first")
            await worker._publish_first_seen_entries(queue)
            second_id = await queue.aenqueue("second")
            third_id = await queue.aenqueue("third")
            worker._last_first_seen_scan_at[queue] = float("-inf")
            await worker._publish_first_seen_entries(queue)
            return first_id, second_id, third_id

        first_id, second_id, third_id = asyncio.run(exercise())

        assert observed_ids == [first_id, second_id, third_id]

    def test_worker_throttles_first_seen_scans_per_queue(self, queue):
        async def exercise():
            worker = AsyncQueueWorker(
                {"requests": queue},
                {"requests": lambda entry: asyncio.sleep(0)},
            )
            scan_count = 0
            original_list_entries = queue._alist_entries

            async def list_entries():
                nonlocal scan_count
                scan_count += 1
                return await original_list_entries()

            queue._alist_entries = list_entries
            await worker._publish_first_seen_entries(queue)
            await worker._publish_first_seen_entries(queue)
            return scan_count

        assert asyncio.run(exercise()) == 1

    def test_worker_skips_a_pending_id_whose_entry_was_removed(self, queue):
        async def exercise():
            stale_id = await queue.aenqueue("stale")
            del queue._entries[stale_id]
            entry_id = await queue.aenqueue("work")

            async def handle(entry):
                return entry.payload

            worker = AsyncQueueWorker(
                {"requests": queue}, {"requests": handle}, idle_delay=0.001
            )
            task = asyncio.create_task(worker.run())
            while (
                await queue.aget_entry(entry_id)
            ).status is not QueueEntryStatus.SUCCEEDED:
                await asyncio.sleep(0.001)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(exercise())

    def test_observer_rejects_event_queue(self, monkeypatch):
        handler = django_queue.QueueHandler(
            {
                "events": {
                    "BACKEND": "tests.test_entry_queue.ObserverEventQueue",
                    "LOCATION": "",
                }
            }
        )
        monkeypatch.setattr(django_queue, "queues", handler)

        with pytest.raises(TypeError, match="AsyncQueue"):
            queue_observer("events", lambda entry: None)

    def test_observer_drops_snapshots_after_its_queue_delivery_queue_is_full(
        self, observer_queue, caplog
    ):
        observers = _observers_for(observer_queue)
        registration = _Registration(lambda entry: None, None, initialising=False)
        with observers.lock:
            observers.registrations.append(registration)
        entry = observer_queue.get_entry(observer_queue.enqueue("work"))

        for _ in range(_EVENT_QUEUE_SIZE + 2):
            observers.publish(entry)

        assert observers.events.qsize() == _EVENT_QUEUE_SIZE
        assert caplog.messages == [
            "Queue lifecycle observer delivery queue is full; dropping snapshots"
        ]

    def test_observer_receiver_clears_its_queue_registration_on_exit(
        self, observer_queue
    ):
        observers = _observers_for(observer_queue)
        observers.receiver = threading.current_thread()

        observers._run_receiver(lambda callback: None)

        assert observers.receiver is None

    def test_entry_queues_are_async_queue_variants(self, queue):
        assert isinstance(queue, AsyncQueue)

    def test_observer_bootstrap_lists_retained_entry_snapshots(self, queue):
        queued_id = queue.enqueue("queued")
        completed_id = queue.enqueue("completed")
        queue.mark_running(completed_id)
        queue.mark_succeeded(completed_id, "done")

        entries = queue._list_entries()

        assert {entry.id for entry in entries} == {queued_id, completed_id}
        assert {entry.status for entry in entries} == {
            QueueEntryStatus.QUEUED,
            QueueEntryStatus.SUCCEEDED,
        }

    def test_observer_orders_a_terminated_snapshot_during_bootstrap(self, queue):
        entry_id = queue.enqueue("completed")
        queued = queue.get_entry(entry_id)
        running = queue.mark_running(entry_id)
        succeeded = queue.mark_succeeded(entry_id, "done")
        terminated = replace(
            succeeded,
            status=QueueEntryStatus.TERMINATED,
        )

        assert _order_snapshots([terminated, succeeded, queued, running]) == [
            queued,
            running,
            succeeded,
            terminated,
        ]

    def test_synchronous_entry_api_uses_the_asynchronous_implementation(self, queue):
        entry_id = queue.enqueue({"request_id": 42})

        entry = queue.get_entry(entry_id)
        dequeued = queue.dequeue_entry()
        running = queue.mark_running(entry_id)
        completed = queue.mark_succeeded(entry_id, {"ok": True})

        assert entry.id == entry_id
        assert dequeued == entry
        assert running.status is QueueEntryStatus.RUNNING
        assert completed.status is QueueEntryStatus.SUCCEEDED

    def test_asynchronous_entry_api_matches_the_synchronous_surface(self, queue):
        async def exercise():
            entry_id = await queue.aenqueue({"request_id": 42})
            entry = await queue.aget_entry(entry_id)
            dequeued = await queue.adequeue_entry()
            running = await queue.amark_running(entry_id)
            completed = await queue.amark_succeeded(entry_id, {"ok": True})
            return entry_id, entry, dequeued, running, completed

        entry_id, entry, dequeued, running, completed = asyncio.run(exercise())

        assert entry.id == entry_id
        assert dequeued == entry
        assert running.status is QueueEntryStatus.RUNNING
        assert completed.status is QueueEntryStatus.SUCCEEDED

    def test_synchronous_entry_api_refuses_to_run_on_an_event_loop(self, queue):
        async def exercise():
            with pytest.raises(
                RuntimeError, match="just await the async function directly"
            ):
                queue.enqueue("work")

        asyncio.run(exercise())

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

    def test_records_a_pre_dispatch_failure_without_a_dispatch_timestamp(self, queue):
        entry_id = queue.enqueue("work")

        failed = queue.mark_failed(entry_id, ValueError("transport unavailable"))

        assert failed.status is QueueEntryStatus.FAILED
        assert failed.dispatched_at is None
        assert failed.finished_at == FIXED_CLOCK_TIME
        assert not queue.has_pending_entries()

    def test_get_entry_reports_a_missing_retained_entry(self, queue):
        with pytest.raises(QueueEntryNotFoundError):
            queue.get_entry(uuid4())

    def test_prune_removes_a_terminal_entry_and_notifies_observers(
        self, observer_queue
    ):
        terminated = threading.Event()
        snapshots = []
        subscription = queue_observer(
            "requests",
            lambda entry: (
                snapshots.append(entry),
                terminated.set()
                if entry.status is QueueEntryStatus.TERMINATED
                else None,
            ),
        )
        try:
            entry_id = observer_queue.enqueue("work")
            observer_queue.dequeue_entry()
            observer_queue.mark_running(entry_id)
            observer_queue.mark_succeeded(entry_id, "done")

            observer_queue.prune_entry(entry_id)

            assert terminated.wait(1)
            assert snapshots[-1].status is QueueEntryStatus.TERMINATED
            with pytest.raises(QueueEntryNotFoundError):
                observer_queue.get_entry(entry_id)
        finally:
            subscription.unsubscribe()

    def test_prune_refuses_a_non_terminal_entry(self, queue):
        entry_id = queue.enqueue("work")

        with pytest.raises(ValueError, match="terminal"):
            queue.prune_entry(entry_id)

        assert queue.get_entry(entry_id).status is QueueEntryStatus.QUEUED

    def test_prune_reports_an_absent_entry(self, queue):
        with pytest.raises(QueueEntryNotFoundError):
            queue.prune_entry(uuid4())

    def test_expired_terminal_entries_are_pruned(self):
        clock = FixedClock()
        queue = MemoryQueue(queue_name="requests", clock=clock)
        queue.retention_timeout = 10
        entry_id = queue.enqueue("work")
        queue.mark_running(entry_id)
        queue.mark_succeeded(entry_id, "done")
        clock.timestamp = FIXED_CLOCK_TIME + 10

        assert asyncio.run(queue._aprune_expired_entries()) == 1
        with pytest.raises(QueueEntryNotFoundError):
            queue.get_entry(entry_id)

    def test_expired_pre_dispatch_failures_are_pruned(self):
        clock = FixedClock()
        queue = MemoryQueue(queue_name="requests", clock=clock)
        queue.retention_timeout = 10
        entry_id = queue.enqueue("work")
        queue.mark_failed(entry_id, ValueError("transport unavailable"))
        clock.timestamp = FIXED_CLOCK_TIME + 10

        assert asyncio.run(queue._aprune_expired_entries()) == 1
        with pytest.raises(QueueEntryNotFoundError):
            queue.get_entry(entry_id)

    def test_worker_prunes_expired_terminal_entries(self, observer_queue):
        terminated = threading.Event()
        subscription = queue_observer(
            "requests",
            lambda entry: (
                terminated.set()
                if entry.status is QueueEntryStatus.TERMINATED
                else None
            ),
        )
        try:
            observer_queue.retention_timeout = 0
            entry_id = observer_queue.enqueue("work")
            observer_queue.mark_running(entry_id)
            observer_queue.mark_succeeded(entry_id, "done")

            async def handler(entry):
                return entry.payload

            async def exercise():
                worker = AsyncQueueWorker(
                    {"requests": observer_queue},
                    {"requests": handler},
                    idle_delay=0.001,
                )
                task = asyncio.create_task(worker.run())
                assert await asyncio.to_thread(terminated.wait, 1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

            asyncio.run(exercise())
            with pytest.raises(QueueEntryNotFoundError):
                observer_queue.get_entry(entry_id)
        finally:
            subscription.unsubscribe()

    def test_explicit_retention_opt_out_leaves_terminal_entries(self):
        queue = MemoryQueue(queue_name="requests", clock=FixedClock())
        queue.retention_timeout = None
        entry_id = queue.enqueue("work")
        queue.mark_running(entry_id)
        queue.mark_succeeded(entry_id, "done")

        assert asyncio.run(queue._aprune_expired_entries()) == 0
        assert queue.get_entry(entry_id).status is QueueEntryStatus.SUCCEEDED

    def test_only_async_queues_expose_entry_pruning(self):
        assert hasattr(AsyncQueue, "prune_entry")
        assert not hasattr(BaseQueue, "prune_entry")
        assert not hasattr(EventQueue, "prune_entry")

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
