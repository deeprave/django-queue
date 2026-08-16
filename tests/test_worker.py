import asyncio
import logging
import threading
from dataclasses import FrozenInstanceError

import pytest

from django_queue import ClockTime, WorkerSnapshot
from django_queue.backends import MemoryAsyncQueue
from django_queue.clock import LocalQueueClock
from django_queue.entries import QueueEntryStatus
from django_queue.worker import (
    DEFAULT_TIMEOUT_SECONDS,
    AsyncQueueWorker,
    QueuePersistenceError,
    heartbeat,
)
from tests.helpers import FixedClock

# Twenty-five years from local time: one basis or neither.
SKEWED = ClockTime(1_000_000_000)


class TestAsyncQueueWorker:
    @pytest.mark.parametrize(
        "cancellation_grace_period",
        [-0.001, float("inf"), float("nan"), "one"],
    )
    def test_rejects_an_invalid_cancellation_grace_period(
        self, cancellation_grace_period
    ):
        with pytest.raises(ValueError, match="Cancellation grace period"):
            AsyncQueueWorker(
                {}, {}, cancellation_grace_period=cancellation_grace_period
            )

    def test_allows_a_zero_cancellation_grace_period(self):
        AsyncQueueWorker({}, {}, cancellation_grace_period=0)

    def test_complete_dispatch_stays_on_the_event_loop_thread(self):
        asyncio.run(self._complete_dispatch_stays_on_the_event_loop_thread())

    async def _complete_dispatch_stays_on_the_event_loop_thread(self):
        queue = ThreadRecordingDispatchQueue(queue_name="requests")
        entry_id = await queue.aenqueue("work")
        loop_thread = threading.get_ident()

        async def handle(entry):
            queue.thread_ids.append(threading.get_ident())
            return entry.payload

        worker = AsyncQueueWorker({"requests": queue}, {"requests": handle})
        task = asyncio.create_task(worker.run())
        await self._wait_for_snapshot_count(worker, "succeeded_count", 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert (await queue.afind(entry_id)).status is QueueEntryStatus.SUCCEEDED
        assert queue.thread_ids
        assert set(queue.thread_ids) == {loop_thread}

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
        assert snapshot.timed_out_count == 0
        with pytest.raises(FrozenInstanceError):
            snapshot.running = True
        with pytest.raises(AttributeError):
            worker.running = True

    def test_snapshot_lists_registered_queue_aliases_in_registration_order(self):
        first_queue = MemoryAsyncQueue(queue_name="first")
        second_queue = MemoryAsyncQueue(queue_name="second")

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
        queue = MemoryAsyncQueue(queue_name="requests")
        entry_id = await queue.aenqueue("work")
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
        assert isinstance(snapshot.started_at, ClockTime)
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
        queue = MemoryAsyncQueue(queue_name="requests")
        entry_id = await queue.aenqueue("work")
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
        assert isinstance(records[0].queue_worker_started_at, float)
        assert all(
            isinstance(record.queue_worker_running_for, float) for record in records
        )
        assert records[2].queue_worker_entry_queued_for >= 0
        assert records[2].queue_worker_entry_ran_for >= 0
        assert records[0].queue_worker_entry_queued_for is None
        assert records[0].queue_worker_entry_ran_for is None
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
        queue = MemoryAsyncQueue(queue_name="requests")

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

    def test_slow_asynchronous_dequeue_does_not_block_the_event_loop(self):
        asyncio.run(self._slow_asynchronous_dequeue_does_not_block_the_event_loop())

    async def _slow_asynchronous_dequeue_does_not_block_the_event_loop(self):
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
        queue = MemoryAsyncQueue(queue_name="requests")
        entry_id = await queue.aenqueue({"request_id": 42})
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

        entry = await queue.afind(entry_id)
        assert entry.status is QueueEntryStatus.SUCCEEDED
        assert entry.result == {"processed": 42}
        assert worker.running is False

    def test_records_a_safe_failure_and_keeps_the_worker_running(self):
        asyncio.run(self._records_a_safe_failure_and_keeps_the_worker_running())

    async def _records_a_safe_failure_and_keeps_the_worker_running(self):
        queue = MemoryAsyncQueue(queue_name="requests")
        failed_id = await queue.aenqueue("broken")
        succeeded_id = await queue.aenqueue("valid")
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

        assert (await queue.afind(failed_id)).error == {
            "type": "ValueError",
            "message": "bad request",
        }
        assert (await queue.afind(succeeded_id)).result == "done"
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
        queue = MemoryAsyncQueue(queue_name="requests")
        entry_id = await queue.aenqueue("work")
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

        entry = await queue.afind(entry_id)
        assert entry.result == "completed during shutdown"
        # A shutdown in progress does not overwrite an outcome the handler
        # actually reached: it finished, so it is recorded by what it returned.
        assert entry.status is QueueEntryStatus.SUCCEEDED
        assert worker.snapshot.cancelled_count == 0
        assert worker.running is False

    def test_cancellation_marks_an_unfinished_handler_as_timed_out(self):
        asyncio.run(self._cancellation_marks_an_unfinished_handler_as_timed_out())

    async def _cancellation_marks_an_unfinished_handler_as_timed_out(self):
        queue = MemoryAsyncQueue(queue_name="requests")
        entry_id = await queue.aenqueue("work")
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

        assert (await queue.afind(entry_id)).status is QueueEntryStatus.TIMEOUT
        assert worker.snapshot.running is False
        assert worker.snapshot.active_entry_id is None
        assert worker.snapshot.timed_out_count == 1
        assert worker.snapshot.cancelled_count == 0

    def test_cancellation_completes_when_a_handler_ignores_cancellation(self):
        asyncio.run(self._cancellation_completes_when_a_handler_ignores_cancellation())

    async def _cancellation_completes_when_a_handler_ignores_cancellation(self):
        queue = MemoryAsyncQueue(queue_name="requests")
        entry_id = await queue.aenqueue("work")
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

        assert (await queue.afind(entry_id)).status is QueueEntryStatus.TIMEOUT

    def test_cancellation_records_a_handler_failure_within_its_grace_period(self):
        asyncio.run(
            self._cancellation_records_a_handler_failure_within_its_grace_period()
        )

    async def _cancellation_records_a_handler_failure_within_its_grace_period(self):
        queue = MemoryAsyncQueue(queue_name="requests")
        entry_id = await queue.aenqueue("work")
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

        assert (await queue.afind(entry_id)).error == {
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
        entry_id = await queue.aenqueue("work")

        async def handle(entry):
            return "done"

        worker = AsyncQueueWorker(
            {"requests": queue}, {"requests": handle}, idle_delay=0.001
        )
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(queue.persistence_started.wait(), timeout=1)
        task.cancel()
        queue.persistence_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(queue.persisted.wait(), timeout=1)

        assert (await queue.afind(entry_id)).status is QueueEntryStatus.SUCCEEDED

    def test_records_a_safe_failure_for_a_non_json_handler_result(self):
        asyncio.run(self._records_a_safe_failure_for_a_non_json_handler_result())

    async def _records_a_safe_failure_for_a_non_json_handler_result(self):
        queue = MemoryAsyncQueue(queue_name="requests")
        entry_id = await queue.aenqueue("work")

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

        assert (await queue.afind(entry_id)).error == {
            "type": "TypeError",
            "message": "Queue entry values must be JSON-serialisable",
        }

    def test_logs_a_terminal_persistence_failure_and_continues(self, caplog):
        asyncio.run(self._logs_a_terminal_persistence_failure_and_continues(caplog))

    async def _logs_a_terminal_persistence_failure_and_continues(self, caplog):
        queue = FailingTerminalQueue(queue_name="requests")
        failed_id = await queue.aenqueue("first")
        await queue.aenqueue("second")
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

        assert (await queue.afind(failed_id)).error == {
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
        await queue.aenqueue("first")
        await queue.aenqueue("second")
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
        while (await queue.afind(entry_id)).status is not status:
            await asyncio.sleep(0.001)

    async def _wait_for_snapshot_count(self, worker, name, expected):
        async with asyncio.timeout(1):
            while getattr(worker.snapshot, name) != expected:
                await asyncio.sleep(0.001)


class ThreadRecordingDispatchQueue(MemoryAsyncQueue):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.thread_ids: list[int] = []

    async def adequeue(self):
        self.thread_ids.append(threading.get_ident())
        return await super().adequeue()

    async def _amark_running(self, entry_id):
        self.thread_ids.append(threading.get_ident())
        return await super()._amark_running(entry_id)

    async def _amark_succeeded(self, entry_id, result):
        self.thread_ids.append(threading.get_ident())
        return await super()._amark_succeeded(entry_id, result)


class SlowEmptyQueue(MemoryAsyncQueue):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dequeue_started = threading.Event()

    async def adequeue(self):
        self.dequeue_started.set()
        await asyncio.sleep(0.05)
        return await super().adequeue()


class BlockingSuccessQueue(MemoryAsyncQueue):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.persistence_started = asyncio.Event()
        self.persistence_release = asyncio.Event()
        self.persisted = asyncio.Event()

    async def _amark_succeeded(self, entry_id, result):
        self.persistence_started.set()
        try:
            await self.persistence_release.wait()
            return await super()._amark_succeeded(entry_id, result)
        finally:
            self.persisted.set()


class FailingTerminalQueue(MemoryAsyncQueue):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._fail_next_success = True

    async def _amark_succeeded(self, entry_id, result):
        if self._fail_next_success:
            self._fail_next_success = False
            raise ConnectionError("Redis is unavailable")
        return await super()._amark_succeeded(entry_id, result)


class UnrecoverableTerminalQueue(FailingTerminalQueue):
    async def _amark_failed(self, entry_id, error):
        raise ConnectionError("Redis is unavailable")


class TestWorkerClock:
    def test_takes_its_queue_clock_when_the_queue_creates_it(self):
        """A worker and the entries it dispatches must share one time basis."""
        queue = MemoryAsyncQueue(queue_name="requests", clock=FixedClock())

        async def handle(entry):
            return entry.payload

        worker = queue.create_worker("requests", handle)

        assert worker.clock is queue.clock

    def test_defaults_to_local_time_when_constructed_directly(self):
        worker = AsyncQueueWorker({}, {})

        assert isinstance(worker.clock, LocalQueueClock)

    def test_run_start_time_never_follows_a_dispatch_it_made(self):
        asyncio.run(self._run_start_time_never_follows_a_dispatch_it_made())

    async def _run_start_time_never_follows_a_dispatch_it_made(self):
        """The queue clock is skewed far from local time; one basis or neither."""
        queue = MemoryAsyncQueue(queue_name="requests", clock=FixedClock(SKEWED))
        entry_id = await queue.aenqueue("work")
        dispatched = asyncio.Event()

        async def handle(entry):
            dispatched.set()
            return entry.payload

        worker = queue.create_worker("requests", handle)
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(dispatched.wait(), timeout=1)
        started_at = worker.snapshot.started_at
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        entry = await queue.afind(entry_id)
        assert started_at == SKEWED
        assert entry.dispatched_at == SKEWED
        assert started_at <= entry.dispatched_at


class TestWorkerDuration:
    def test_reports_how_long_it_has_been_running(self):
        asyncio.run(self._reports_how_long_it_has_been_running())

    async def _reports_how_long_it_has_been_running(self):
        clock = FixedClock(SKEWED)
        queue = MemoryAsyncQueue(queue_name="requests", clock=clock)
        dispatched = asyncio.Event()

        async def handle(entry):
            dispatched.set()
            return entry.payload

        await queue.aenqueue("work")
        worker = queue.create_worker("requests", handle)
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(dispatched.wait(), timeout=1)
        clock.timestamp = SKEWED + 12
        running_for = worker.snapshot.running_for

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert running_for == pytest.approx(12)

    def test_reports_no_duration_when_the_clock_moves_backwards(self):
        """A recalibrated Redis clock can read earlier than it did before."""
        clock = FixedClock(SKEWED)
        worker = AsyncQueueWorker({}, {}, clock=clock)
        worker_started = asyncio.Event()

        async def start_and_read():
            task = asyncio.create_task(worker.run())
            await asyncio.sleep(0)
            worker_started.set()
            clock.timestamp = SKEWED - 30
            running_for = worker.snapshot.running_for
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return running_for

        assert asyncio.run(start_and_read()) is None

    def test_reports_no_duration_before_it_starts(self):
        assert AsyncQueueWorker({}, {}).snapshot.running_for is None

    def test_a_stopped_worker_reports_how_long_it_ran(self):
        asyncio.run(self._a_stopped_worker_reports_how_long_it_ran())

    async def _a_stopped_worker_reports_how_long_it_ran(self):
        """Otherwise a stopped worker's runtime grows forever."""
        clock = FixedClock(SKEWED)
        queue = MemoryAsyncQueue(queue_name="requests", clock=clock)

        async def handle(entry):
            return entry.payload

        worker = queue.create_worker("requests", handle)
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0)
        clock.timestamp = SKEWED + 5

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        stopped = worker.snapshot.running_for
        clock.timestamp = SKEWED + 500

        assert stopped == pytest.approx(5)
        assert worker.snapshot.running_for == stopped


class TestBudgetResolution:
    """Worker override, then the entry's own budget, then the queue, then 600s."""

    def _entry(self, queue, budget=None):
        return queue.find(queue.enqueue("work", timeout_seconds=budget))

    def test_falls_back_to_the_default_when_nothing_is_set(self):
        queue = MemoryAsyncQueue(queue_name="requests")
        worker = AsyncQueueWorker({"requests": queue}, {"requests": handle_nothing})

        assert worker.budget_for(queue, self._entry(queue)) == DEFAULT_TIMEOUT_SECONDS

    def test_a_queue_default_beats_the_fallback(self):
        queue = MemoryAsyncQueue(queue_name="requests")
        queue.timeout_seconds = 45
        worker = AsyncQueueWorker({"requests": queue}, {"requests": handle_nothing})

        assert worker.budget_for(queue, self._entry(queue)) == 45

    def test_an_entry_budget_beats_the_queue_default(self):
        queue = MemoryAsyncQueue(queue_name="requests")
        queue.timeout_seconds = 45
        worker = AsyncQueueWorker({"requests": queue}, {"requests": handle_nothing})

        assert worker.budget_for(queue, self._entry(queue, 5)) == 5

    def test_a_worker_override_beats_everything(self):
        """The worker knows the runtime it is actually operating in."""
        queue = MemoryAsyncQueue(queue_name="requests")
        queue.timeout_seconds = 45
        worker = AsyncQueueWorker(
            {"requests": queue}, {"requests": handle_nothing}, timeout_seconds=2
        )

        assert worker.budget_for(queue, self._entry(queue, 5)) == 2

    @pytest.mark.parametrize("budget", ["30", True, object()])
    def test_rejects_a_worker_override_that_is_not_a_number(self, budget):
        with pytest.raises(TypeError, match="Execution budget"):
            AsyncQueueWorker({}, {}, timeout_seconds=budget)

    @pytest.mark.parametrize("budget", [0, -1, float("nan"), float("inf")])
    def test_rejects_a_worker_override_that_is_not_finite_and_positive(self, budget):
        with pytest.raises(ValueError, match="Execution budget"):
            AsyncQueueWorker({}, {}, timeout_seconds=budget)


async def handle_nothing(entry):
    return entry.payload


class TestBudgetEnforcement:
    def test_nested_repeated_heartbeats_extend_the_budget_without_changing_entry_time(
        self,
    ):
        asyncio.run(
            self._nested_repeated_heartbeats_extend_the_budget_without_changing_entry_time()
        )

    async def _nested_repeated_heartbeats_extend_the_budget_without_changing_entry_time(
        self,
    ):
        queue = MemoryAsyncQueue(queue_name="requests")
        entry_id = await queue.aenqueue("work", timeout_seconds=0.05)

        async def report_progress():
            heartbeat()

        async def handle(entry):
            for _ in range(3):
                await asyncio.sleep(0.03)
                await report_progress()
            return entry.payload

        worker = AsyncQueueWorker({"requests": queue}, {"requests": handle})
        task = asyncio.create_task(worker.run())
        await self._wait_for_snapshot_count(worker, "succeeded_count", 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        completed = await queue.afind(entry_id)
        assert completed.status is QueueEntryStatus.SUCCEEDED
        assert completed.ran_for is not None

    def test_heartbeat_extends_the_active_handler_budget(self):
        asyncio.run(self._heartbeat_extends_the_active_handler_budget())

    async def _heartbeat_extends_the_active_handler_budget(self):
        queue = MemoryAsyncQueue(queue_name="requests")
        entry_id = await queue.aenqueue("work", timeout_seconds=0.02)

        async def handle(entry):
            await asyncio.sleep(0.01)
            heartbeat()
            await asyncio.sleep(0.01)
            return entry.payload

        worker = AsyncQueueWorker({"requests": queue}, {"requests": handle})
        task = asyncio.create_task(worker.run())
        await self._wait_for_snapshot_count(worker, "succeeded_count", 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert (await queue.afind(entry_id)).status is QueueEntryStatus.SUCCEEDED

    def test_heartbeat_outside_a_handler_raises(self):
        with pytest.raises(RuntimeError, match="active handler dispatch"):
            heartbeat()

    def test_heartbeat_from_a_handler_child_after_dispatch_raises(self):
        asyncio.run(self._heartbeat_from_a_handler_child_after_dispatch_raises())

    async def _heartbeat_from_a_handler_child_after_dispatch_raises(self):
        queue = MemoryAsyncQueue(queue_name="requests")
        await queue.aenqueue("work")
        child_ready = asyncio.Event()
        release_child = asyncio.Event()
        child_failure = asyncio.Future()

        async def background():
            child_ready.set()
            await release_child.wait()
            with pytest.raises(RuntimeError, match="active handler dispatch"):
                heartbeat()
            child_failure.set_result(None)

        async def handle(entry):
            asyncio.create_task(background())
            await child_ready.wait()
            return entry.payload

        worker = AsyncQueueWorker({"requests": queue}, {"requests": handle})
        worker_task = asyncio.create_task(worker.run())
        await self._wait_for_snapshot_count(worker, "succeeded_count", 1)
        release_child.set()
        await child_failure
        worker_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker_task

    def test_abandons_a_handler_that_exceeds_its_budget(self):
        asyncio.run(self._abandons_a_handler_that_exceeds_its_budget())

    async def _abandons_a_handler_that_exceeds_its_budget(self):
        """The alias must not be starved by one handler that never returns."""
        queue = MemoryAsyncQueue(queue_name="requests")
        hung_id = await queue.aenqueue("hangs")
        started = asyncio.Event()

        async def handle(entry):
            if entry.payload == "hangs":
                started.set()
                await asyncio.Event().wait()
            return entry.payload

        worker = AsyncQueueWorker(
            {"requests": queue},
            {"requests": handle},
            idle_delay=0.001,
            timeout_seconds=0.05,
        )
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(started.wait(), timeout=1)
        await self._wait_for_snapshot_count(worker, "timed_out_count", 1)

        # the worker moved on rather than stalling on the hung entry
        next_id = await queue.aenqueue("next")
        await self._wait_for_snapshot_count(worker, "succeeded_count", 1)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert (await queue.afind(hung_id)).status is QueueEntryStatus.TIMEOUT
        assert (await queue.afind(hung_id)).finished_at is not None
        assert (await queue.afind(next_id)).status is QueueEntryStatus.SUCCEEDED

    def test_enforces_a_budget_carried_on_the_entry(self):
        asyncio.run(self._enforces_a_budget_carried_on_the_entry())

    async def _enforces_a_budget_carried_on_the_entry(self):
        """The budget a producer set at enqueue, with no worker override."""
        queue = MemoryAsyncQueue(queue_name="requests")
        entry_id = await queue.aenqueue("work", timeout_seconds=0.05)

        async def handle(entry):
            await asyncio.Event().wait()

        worker = AsyncQueueWorker(
            {"requests": queue}, {"requests": handle}, idle_delay=0.001
        )
        task = asyncio.create_task(worker.run())
        await self._wait_for_snapshot_count(worker, "timed_out_count", 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert (await queue.afind(entry_id)).status is QueueEntryStatus.TIMEOUT

    def test_enforces_the_queues_configured_budget(self):
        asyncio.run(self._enforces_the_queues_configured_budget())

    async def _enforces_the_queues_configured_budget(self):
        """The alias TIMEOUT setting, with neither entry nor worker overriding."""
        queue = MemoryAsyncQueue(queue_name="requests")
        queue.timeout_seconds = 0.05
        entry_id = await queue.aenqueue("work")

        async def handle(entry):
            await asyncio.Event().wait()

        worker = AsyncQueueWorker(
            {"requests": queue}, {"requests": handle}, idle_delay=0.001
        )
        task = asyncio.create_task(worker.run())
        await self._wait_for_snapshot_count(worker, "timed_out_count", 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert (await queue.afind(entry_id)).status is QueueEntryStatus.TIMEOUT

    def test_a_handler_raising_timeout_error_is_a_failure_not_an_expiry(self):
        asyncio.run(self._a_handler_raising_timeout_error_is_a_failure_not_an_expiry())

    async def _a_handler_raising_timeout_error_is_a_failure_not_an_expiry(self):
        """TimeoutError is asyncio.TimeoutError, so the two must be told apart.

        A handler wrapping its own I/O in a deadline raises the same class the
        budget expires with. Only the budget actually running out means the
        handler never answered.
        """
        queue = MemoryAsyncQueue(queue_name="requests")
        entry_id = await queue.aenqueue("work")

        async def handle(entry):
            raise TimeoutError("upstream did not answer")

        worker = AsyncQueueWorker(
            {"requests": queue},
            {"requests": handle},
            idle_delay=0.001,
            timeout_seconds=30,
        )
        task = asyncio.create_task(worker.run())
        await self._wait_for_snapshot_count(worker, "failed_count", 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        entry = await queue.afind(entry_id)
        assert entry.status is QueueEntryStatus.FAILED
        # the handler's own error survives rather than being discarded
        assert entry.error == {
            "type": "TimeoutError",
            "message": "upstream did not answer",
        }
        assert worker.snapshot.timed_out_count == 0

    def test_a_handler_raising_timeout_error_during_shutdown_is_a_failure(self):
        asyncio.run(
            self._a_handler_raising_timeout_error_during_shutdown_is_a_failure()
        )

    async def _a_handler_raising_timeout_error_during_shutdown_is_a_failure(self):
        """The grace period expires as TimeoutError too, and is told apart."""
        queue = MemoryAsyncQueue(queue_name="requests")
        entry_id = await queue.aenqueue("work")
        started = asyncio.Event()
        release = asyncio.Event()

        async def handle(entry):
            started.set()
            await release.wait()
            raise TimeoutError("upstream did not answer")

        worker = AsyncQueueWorker(
            {"requests": queue},
            {"requests": handle},
            idle_delay=0.001,
            cancellation_grace_period=5,
        )
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        entry = await queue.afind(entry_id)
        assert entry.status is QueueEntryStatus.FAILED
        assert entry.error["type"] == "TimeoutError"
        assert worker.snapshot.timed_out_count == 0

    def test_leaves_a_handler_inside_its_budget_alone(self):
        asyncio.run(self._leaves_a_handler_inside_its_budget_alone())

    async def _leaves_a_handler_inside_its_budget_alone(self):
        queue = MemoryAsyncQueue(queue_name="requests")
        entry_id = await queue.aenqueue("work")

        async def handle(entry):
            await asyncio.sleep(0.01)
            return entry.payload

        worker = AsyncQueueWorker(
            {"requests": queue},
            {"requests": handle},
            idle_delay=0.001,
            timeout_seconds=5,
        )
        task = asyncio.create_task(worker.run())
        await self._wait_for_snapshot_count(worker, "succeeded_count", 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert (await queue.afind(entry_id)).status is QueueEntryStatus.SUCCEEDED
        assert worker.snapshot.timed_out_count == 0

    def test_a_timeout_leaves_the_cancelled_count_alone(self):
        asyncio.run(self._a_timeout_leaves_the_cancelled_count_alone())

    async def _a_timeout_leaves_the_cancelled_count_alone(self):
        """Abandoned-on-budget and stopped-on-shutdown are different outcomes."""
        queue = MemoryAsyncQueue(queue_name="requests")
        await queue.aenqueue("work")

        async def handle(entry):
            await asyncio.Event().wait()

        worker = AsyncQueueWorker(
            {"requests": queue},
            {"requests": handle},
            idle_delay=0.001,
            timeout_seconds=0.05,
        )
        task = asyncio.create_task(worker.run())
        await self._wait_for_snapshot_count(worker, "timed_out_count", 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        snapshot = worker.snapshot
        assert snapshot.timed_out_count == 1
        assert snapshot.cancelled_count == 0

    async def _wait_for_snapshot_count(self, worker, name, expected):
        for _ in range(500):
            if getattr(worker.snapshot, name) >= expected:
                return
            await asyncio.sleep(0.005)
        raise AssertionError(f"{name} never reached {expected}")

    def test_the_terminal_record_carries_the_timed_out_count(self, caplog):
        asyncio.run(self._the_terminal_record_carries_the_timed_out_count(caplog))

    async def _the_terminal_record_carries_the_timed_out_count(self, caplog):
        queue = MemoryAsyncQueue(queue_name="requests")
        await queue.aenqueue("work")

        async def handle(entry):
            await asyncio.Event().wait()

        worker = AsyncQueueWorker(
            {"requests": queue},
            {"requests": handle},
            idle_delay=0.001,
            timeout_seconds=0.05,
        )
        with caplog.at_level(logging.INFO, logger="django_queue.worker"):
            task = asyncio.create_task(worker.run())
            await self._wait_for_snapshot_count(worker, "timed_out_count", 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        terminal = [
            record
            for record in caplog.records
            if getattr(record, "queue_worker_event", None) == "terminal_recorded"
        ]
        assert terminal, "no terminal record was emitted"
        assert terminal[-1].queue_worker_timed_out_count == 1
        assert terminal[-1].queue_worker_cancelled_count == 0


class TestBudgetAndEntryClocksStaySeparate:
    def test_a_timed_out_entry_measures_its_own_wall_clock(self):
        asyncio.run(self._a_timed_out_entry_measures_its_own_wall_clock())

    async def _a_timed_out_entry_measures_its_own_wall_clock(self):
        """The budget decides when to stop; the entry records what happened.

        The queue's clock is set decades from now and deliberately advances far
        faster than the budget it is nothing to do with. If `ran_for` were
        derived from the budget -- or the budget read from this clock -- the two
        would agree. They must not: one is a monotonic loop deadline, the other
        a wall-clock reading taken from the backend.
        """
        clock = FixedClock(SKEWED)
        queue = MemoryAsyncQueue(queue_name="requests", clock=clock)
        entry_id = await queue.aenqueue("work")
        budget = 0.05

        async def handle(entry):
            clock.timestamp = ClockTime(SKEWED.seconds + 3600)
            await asyncio.Event().wait()

        worker = AsyncQueueWorker(
            {"requests": queue},
            {"requests": handle},
            idle_delay=0.001,
            timeout_seconds=budget,
        )
        task = asyncio.create_task(worker.run())
        await self._wait_for_snapshot_count(worker, "timed_out_count", 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        entry = await queue.afind(entry_id)
        assert entry.status is QueueEntryStatus.TIMEOUT
        # An hour on the queue's clock, from a budget of a twentieth of a second.
        assert entry.ran_for == 3600.0
        assert entry.finished_at == ClockTime(SKEWED.seconds + 3600)

    async def _wait_for_snapshot_count(self, worker, name, expected):
        for _ in range(500):
            if getattr(worker.snapshot, name) >= expected:
                return
            await asyncio.sleep(0.005)
        raise AssertionError(f"{name} never reached {expected}")
