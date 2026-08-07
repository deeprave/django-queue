import asyncio
import logging
import threading
import time
from dataclasses import FrozenInstanceError
from datetime import UTC

import pytest

from django_queue import WorkerSnapshot
from django_queue.backends import MemoryQueue
from django_queue.entries import QueueEntryStatus
from django_queue.worker import (
    AsyncQueueWorker,
    QueuePersistenceError,
)


class TestAsyncQueueWorker:
    def test_exposes_an_immutable_initial_snapshot(self):
        worker = AsyncQueueWorker({}, {})

        snapshot = worker.snapshot

        assert isinstance(snapshot, WorkerSnapshot)
        assert snapshot.worker_id.version == 7
        assert snapshot.running is False
        assert snapshot.started_at is None
        assert snapshot.active_entry_id is None
        assert snapshot.active_queue_name is None
        assert snapshot.queue_names == ()
        assert snapshot.dispatch_count == 0
        assert snapshot.succeeded_count == 0
        assert snapshot.failed_count == 0
        assert snapshot.cancelled_count == 0
        with pytest.raises(FrozenInstanceError):
            snapshot.running = True
        with pytest.raises(AttributeError):
            worker.running = True

    def test_snapshot_lists_registered_queue_aliases_in_registration_order(self):
        first_queue = MemoryQueue(queue_name="first")
        second_queue = MemoryQueue(queue_name="second")

        async def handle(entry):
            return entry.payload

        worker = AsyncQueueWorker(
            {"first": first_queue, "second": second_queue},
            {"first": handle, "second": handle},
        )

        assert worker.snapshot.queue_names == ("first", "second")

    def test_snapshot_tracks_a_confirmed_successful_outcome(self):
        asyncio.run(self._snapshot_tracks_a_confirmed_successful_outcome())

    async def _snapshot_tracks_a_confirmed_successful_outcome(self):
        queue = MemoryQueue(queue_name="requests")
        entry_id = queue.enqueue("work")
        started = asyncio.Event()
        release = asyncio.Event()

        async def handle(entry):
            started.set()
            await release.wait()
            return entry.payload

        worker = AsyncQueueWorker(
            {"requests": queue}, {"requests": handle}, idle_delay=0.001
        )
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(started.wait(), timeout=1)

        snapshot = worker.snapshot
        assert snapshot.running is True
        assert snapshot.started_at is not None
        assert snapshot.started_at.tzinfo is UTC
        assert snapshot.active_entry_id == entry_id
        assert snapshot.active_queue_name == "requests"
        assert snapshot.queue_names == ("requests",)
        assert snapshot.dispatch_count == 1
        assert snapshot.succeeded_count == 0

        release.set()
        await self._wait_for_snapshot_count(worker, "succeeded_count", 1)

        snapshot = worker.snapshot
        assert snapshot.active_entry_id is None
        assert snapshot.active_queue_name is None
        assert snapshot.succeeded_count == 1
        assert snapshot.failed_count == 0
        assert snapshot.cancelled_count == 0

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert worker.snapshot.running is False

    def test_logs_snapshot_derived_lifecycle_records(self, caplog):
        asyncio.run(self._logs_snapshot_derived_lifecycle_records(caplog))

    async def _logs_snapshot_derived_lifecycle_records(self, caplog):
        caplog.set_level(logging.INFO, logger="django_queue.worker")
        queue = MemoryQueue(queue_name="requests")
        entry_id = queue.enqueue("work")
        handled = asyncio.Event()

        async def handle(entry):
            handled.set()
            return entry.payload

        worker = AsyncQueueWorker(
            {"requests": queue}, {"requests": handle}, idle_delay=0.001
        )
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(handled.wait(), timeout=1)
        await self._wait_for_snapshot_count(worker, "succeeded_count", 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        records = [
            record for record in caplog.records if hasattr(record, "queue_worker_event")
        ]
        assert [record.queue_worker_event for record in records] == [
            "started",
            "dispatch_started",
            "terminal_recorded",
            "stopped",
        ]
        assert [record.getMessage() for record in records] == [
            "Queue worker started",
            "Queue worker began dispatching an entry",
            "Queue worker recorded a terminal outcome",
            "Queue worker stopped",
        ]
        assert all(
            record.queue_worker_id == str(worker.snapshot.worker_id)
            for record in records
        )
        assert records[1].queue_worker_active_entry_id == str(entry_id)
        assert records[1].queue_worker_active_queue_name == "requests"
        assert records[1].queue_worker_queue_names == ("requests",)
        assert records[2].queue_worker_active_entry_id is None
        assert records[2].queue_worker_active_queue_name is None
        assert records[2].queue_worker_succeeded_count == 1
        assert records[-1].queue_worker_running is False

    def test_idle_worker_runs_until_cancelled_without_dispatching(self):
        asyncio.run(self._idle_worker_runs_until_cancelled_without_dispatching())

    async def _idle_worker_runs_until_cancelled_without_dispatching(self):
        queue = MemoryQueue(queue_name="requests")

        async def handle(entry):
            raise AssertionError("An empty queue must not dispatch a handler")

        worker = AsyncQueueWorker(
            {"requests": queue}, {"requests": handle}, idle_delay=0.001
        )
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.01)

        assert worker.running is True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert worker.running is False

    def test_slow_synchronous_dequeue_does_not_block_the_event_loop(self):
        asyncio.run(self._slow_synchronous_dequeue_does_not_block_the_event_loop())

    async def _slow_synchronous_dequeue_does_not_block_the_event_loop(self):
        queue = SlowEmptyQueue(queue_name="requests")

        async def handle(entry):
            raise AssertionError("The queue is empty")

        worker = AsyncQueueWorker(
            {"requests": queue}, {"requests": handle}, idle_delay=0.001
        )
        worker_task = asyncio.create_task(worker.run())
        await asyncio.wait_for(asyncio.to_thread(queue.dequeue_started.wait), timeout=1)
        progress = asyncio.Event()
        asyncio.get_running_loop().call_later(0.001, progress.set)

        await asyncio.wait_for(progress.wait(), timeout=0.02)
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    def test_dispatches_an_entry_and_stores_the_handler_result(self):
        asyncio.run(self._dispatches_an_entry_and_stores_the_handler_result())

    async def _dispatches_an_entry_and_stores_the_handler_result(self):
        queue = MemoryQueue(queue_name="requests")
        entry_id = queue.enqueue({"request_id": 42})
        handled = asyncio.Event()

        async def handle(entry):
            handled.set()
            return {"processed": entry.payload["request_id"]}

        worker = AsyncQueueWorker(
            {"requests": queue}, {"requests": handle}, idle_delay=0.001
        )
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(handled.wait(), timeout=1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        entry = queue.get_entry(entry_id)
        assert entry.status is QueueEntryStatus.SUCCEEDED
        assert entry.result == {"processed": 42}
        assert worker.running is False

    def test_records_a_safe_failure_and_keeps_the_worker_running(self):
        asyncio.run(self._records_a_safe_failure_and_keeps_the_worker_running())

    async def _records_a_safe_failure_and_keeps_the_worker_running(self):
        queue = MemoryQueue(queue_name="requests")
        failed_id = queue.enqueue("broken")
        succeeded_id = queue.enqueue("valid")
        handled = asyncio.Event()

        async def handle(entry):
            if entry.payload == "broken":
                raise ValueError("bad request")
            handled.set()
            return "done"

        worker = AsyncQueueWorker(
            {"requests": queue}, {"requests": handle}, idle_delay=0.001
        )
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(handled.wait(), timeout=1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert queue.get_entry(failed_id).error == {
            "type": "ValueError",
            "message": "bad request",
        }
        assert queue.get_entry(succeeded_id).result == "done"
        assert worker.snapshot.dispatch_count == 2
        assert worker.snapshot.succeeded_count == 1
        assert worker.snapshot.failed_count == 1
        assert worker.snapshot.cancelled_count == 0

    def test_cancellation_allows_an_active_handler_to_finish_within_its_grace_period(
        self,
    ):
        asyncio.run(
            self._cancellation_allows_an_active_handler_to_finish_within_its_grace_period()
        )

    async def _cancellation_allows_an_active_handler_to_finish_within_its_grace_period(
        self,
    ):
        queue = MemoryQueue(queue_name="requests")
        entry_id = queue.enqueue("work")
        started = asyncio.Event()
        release = asyncio.Event()

        async def handle(entry):
            started.set()
            await release.wait()
            return "completed during shutdown"

        worker = AsyncQueueWorker(
            {"requests": queue},
            {"requests": handle},
            idle_delay=0.001,
            cancellation_grace_period=1,
        )
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        release.set()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert queue.get_entry(entry_id).result == "completed during shutdown"
        assert worker.running is False

    def test_cancellation_marks_an_unfinished_handler_as_cancelled(self):
        asyncio.run(self._cancellation_marks_an_unfinished_handler_as_cancelled())

    async def _cancellation_marks_an_unfinished_handler_as_cancelled(self):
        queue = MemoryQueue(queue_name="requests")
        entry_id = queue.enqueue("work")
        started = asyncio.Event()

        async def handle(entry):
            started.set()
            await asyncio.Future()

        worker = AsyncQueueWorker(
            {"requests": queue},
            {"requests": handle},
            idle_delay=0.001,
            cancellation_grace_period=0.001,
        )
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert queue.get_entry(entry_id).status is QueueEntryStatus.CANCELLED
        assert worker.snapshot.running is False
        assert worker.snapshot.active_entry_id is None
        assert worker.snapshot.cancelled_count == 1

    def test_cancellation_completes_when_a_handler_ignores_cancellation(self):
        asyncio.run(self._cancellation_completes_when_a_handler_ignores_cancellation())

    async def _cancellation_completes_when_a_handler_ignores_cancellation(self):
        queue = MemoryQueue(queue_name="requests")
        entry_id = queue.enqueue("work")
        started = asyncio.Event()
        cancellation_ignored = asyncio.Event()
        release = asyncio.Event()
        handler_finished = asyncio.Event()

        async def handle(entry):
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancellation_ignored.set()
                await release.wait()
            finally:
                handler_finished.set()

        worker = AsyncQueueWorker(
            {"requests": queue},
            {"requests": handle},
            idle_delay=0.001,
            cancellation_grace_period=0.001,
        )
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=1)
            await asyncio.wait_for(cancellation_ignored.wait(), timeout=1)
        finally:
            release.set()
            await asyncio.wait_for(handler_finished.wait(), timeout=1)

        assert queue.get_entry(entry_id).status is QueueEntryStatus.CANCELLED

    def test_cancellation_records_a_handler_failure_within_its_grace_period(self):
        asyncio.run(
            self._cancellation_records_a_handler_failure_within_its_grace_period()
        )

    async def _cancellation_records_a_handler_failure_within_its_grace_period(self):
        queue = MemoryQueue(queue_name="requests")
        entry_id = queue.enqueue("work")
        started = asyncio.Event()
        release = asyncio.Event()

        async def handle(entry):
            started.set()
            await release.wait()
            raise ValueError("shutdown failure")

        worker = AsyncQueueWorker(
            {"requests": queue},
            {"requests": handle},
            idle_delay=0.001,
            cancellation_grace_period=1,
        )
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert queue.get_entry(entry_id).error == {
            "type": "ValueError",
            "message": "shutdown failure",
        }

    def test_cancellation_during_success_persistence_does_not_repeat_the_transition(
        self,
    ):
        asyncio.run(
            self._cancellation_during_success_persistence_does_not_repeat_the_transition()
        )

    async def _cancellation_during_success_persistence_does_not_repeat_the_transition(
        self,
    ):
        queue = BlockingSuccessQueue(queue_name="requests")
        entry_id = queue.enqueue("work")

        async def handle(entry):
            return "done"

        worker = AsyncQueueWorker(
            {"requests": queue}, {"requests": handle}, idle_delay=0.001
        )
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(
            asyncio.to_thread(queue.persistence_started.wait), timeout=1
        )
        task.cancel()
        queue.persistence_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(asyncio.to_thread(queue.persisted.wait), timeout=1)

        assert queue.get_entry(entry_id).status is QueueEntryStatus.SUCCEEDED

    def test_records_a_safe_failure_for_a_non_json_handler_result(self):
        asyncio.run(self._records_a_safe_failure_for_a_non_json_handler_result())

    async def _records_a_safe_failure_for_a_non_json_handler_result(self):
        queue = MemoryQueue(queue_name="requests")
        entry_id = queue.enqueue("work")

        async def handle(entry):
            return object()

        worker = AsyncQueueWorker(
            {"requests": queue}, {"requests": handle}, idle_delay=0.001
        )
        task = asyncio.create_task(worker.run())
        await self._wait_for_status(queue, entry_id, QueueEntryStatus.FAILED)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert queue.get_entry(entry_id).error == {
            "type": "TypeError",
            "message": "Queue entry values must be JSON-serialisable",
        }

    def test_logs_a_terminal_persistence_failure_and_continues(self, caplog):
        asyncio.run(self._logs_a_terminal_persistence_failure_and_continues(caplog))

    async def _logs_a_terminal_persistence_failure_and_continues(self, caplog):
        queue = FailingTerminalQueue(queue_name="requests")
        failed_id = queue.enqueue("first")
        queue.enqueue("second")
        worker = AsyncQueueWorker(
            {"requests": queue}, {"requests": lambda entry: self._complete(entry)}
        )
        task = asyncio.create_task(worker.run())
        # Wait on the worker's own counter, not the backend status: the status
        # flips inside the persistence thread before the worker resumes to
        # record the outcome, so cancelling on the status would race that.
        await asyncio.wait_for(
            self._wait_for_snapshot_count(worker, "succeeded_count", 1),
            timeout=1,
        )
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert queue.get_entry(failed_id).error == {
            "type": "QueuePersistenceError",
            "message": "Unable to persist terminal queue outcome",
        }
        assert worker.snapshot.dispatch_count == 2
        assert worker.snapshot.succeeded_count == 1
        assert worker.snapshot.failed_count == 1
        assert "Unable to record terminal queue outcome" in caplog.text

    def test_stops_when_a_terminal_persistence_failure_cannot_be_recorded(self, caplog):
        asyncio.run(
            self._stops_when_a_terminal_persistence_failure_cannot_be_recorded(caplog)
        )

    async def _stops_when_a_terminal_persistence_failure_cannot_be_recorded(
        self, caplog
    ):
        caplog.set_level(logging.INFO, logger="django_queue.worker")
        queue = UnrecoverableTerminalQueue(queue_name="requests")
        queue.enqueue("first")
        queue.enqueue("second")
        handled_payloads = []

        async def handle(entry):
            handled_payloads.append(entry.payload)
            return entry.payload

        worker = AsyncQueueWorker({"requests": queue}, {"requests": handle})

        with pytest.raises(
            QueuePersistenceError, match="Unable to persist terminal queue outcome"
        ):
            await worker.run()

        assert handled_payloads == ["first"]
        assert worker.snapshot.running is False
        assert worker.snapshot.active_entry_id is None
        assert worker.snapshot.dispatch_count == 1
        assert worker.snapshot.succeeded_count == 0
        assert worker.snapshot.failed_count == 0
        assert worker.snapshot.cancelled_count == 0
        assert [
            record.queue_worker_event
            for record in caplog.records
            if hasattr(record, "queue_worker_event")
        ] == ["started", "dispatch_started", "stopped"]

    async def _complete(self, entry):
        return entry.payload

    async def _wait_for_status(self, queue, entry_id, status):
        while queue.get_entry(entry_id).status is not status:
            await asyncio.sleep(0.001)

    async def _wait_for_snapshot_count(self, worker, name, expected):
        async with asyncio.timeout(1):
            while getattr(worker.snapshot, name) != expected:
                await asyncio.sleep(0.001)


class SlowEmptyQueue(MemoryQueue):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dequeue_started = threading.Event()

    def dequeue_entry(self):
        self.dequeue_started.set()
        time.sleep(0.05)
        return super().dequeue_entry()


class BlockingSuccessQueue(MemoryQueue):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.persistence_started = threading.Event()
        self.persistence_release = threading.Event()
        self.persisted = threading.Event()

    def mark_succeeded(self, entry_id, result):
        self.persistence_started.set()
        self.persistence_release.wait(timeout=1)
        try:
            return super().mark_succeeded(entry_id, result)
        finally:
            self.persisted.set()


class FailingTerminalQueue(MemoryQueue):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._fail_next_success = True

    def mark_succeeded(self, entry_id, result):
        if self._fail_next_success:
            self._fail_next_success = False
            raise ConnectionError("Redis is unavailable")
        return super().mark_succeeded(entry_id, result)


class UnrecoverableTerminalQueue(FailingTerminalQueue):
    def mark_failed(self, entry_id, error):
        raise ConnectionError("Redis is unavailable")
