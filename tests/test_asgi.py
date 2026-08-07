import asyncio
import logging
import threading
from typing import ClassVar

import pytest

import django_queue
from django_queue.apps import DjangoQueueConfig
from django_queue.asgi import with_queue_worker
from django_queue.backends import MemoryPriorityQueue, MemoryQueue
from django_queue.entries import QueueEntryStatus
from django_queue.worker import AsyncQueueWorker


class TrackingWorker(AsyncQueueWorker):
    instances = 0

    def __init__(self, *args, **kwargs):
        type(self).instances += 1
        super().__init__(*args, **kwargs)


class FailingConstructionWorker(AsyncQueueWorker):
    def __init__(self, *args, **kwargs):
        raise RuntimeError("worker construction failed")


async def no_op_handler(entry):
    return None


@pytest.fixture(autouse=True)
def reset_tracking_worker_instances():
    TrackingWorker.instances = 0
    yield
    TrackingWorker.instances = 0


class ThreadSharedQueue(MemoryQueue):
    _entries: ClassVar[dict | None] = None
    _pending_entries: ClassVar[object | None] = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if type(self)._entries is None:
            type(self)._entries = self._entries
        if type(self)._pending_entries is None:
            type(self)._pending_entries = self._pending_entries
        self._entries = type(self)._entries
        self._pending_entries = type(self)._pending_entries


class ThreadRecordingQueue(MemoryQueue):
    pending_check_thread_id: int | None = None

    def has_pending_entries(self) -> bool:
        type(self).pending_check_thread_id = threading.get_ident()
        return False


class TestQueueWorkerASGI:
    def test_starts_a_worker_for_an_entry_enqueued_from_another_thread(self):
        asyncio.run(self._starts_a_worker_for_an_entry_enqueued_from_another_thread())

    async def _starts_a_worker_for_an_entry_enqueued_from_another_thread(self):
        ThreadSharedQueue._entries = None
        ThreadSharedQueue._pending_entries = None
        queue = ThreadSharedQueue(queue_name="requests")
        producer = ThreadSharedQueue(queue_name="requests")
        handled = asyncio.Event()
        startup_complete = asyncio.Event()
        shutdown = asyncio.Event()

        async def handle(entry):
            handled.set()
            return entry.payload

        async def application(scope, receive, send):
            raise AssertionError(f"Unexpected scope: {scope['type']}")

        async def receive():
            if not startup_complete.is_set():
                return {"type": "lifespan.startup"}
            await shutdown.wait()
            return {"type": "lifespan.shutdown"}

        async def send(message):
            if message["type"] == "lifespan.startup.complete":
                startup_complete.set()

        task = asyncio.create_task(
            with_queue_worker(
                application, handlers={"requests": handle}, queues={"requests": queue}
            )({"type": "lifespan"}, receive, send)
        )
        await asyncio.wait_for(startup_complete.wait(), timeout=1)
        await asyncio.to_thread(producer.enqueue, "work")
        await asyncio.wait_for(handled.wait(), timeout=1)

        shutdown.set()
        await asyncio.wait_for(task, timeout=1)

    def test_does_not_start_a_worker_after_lifespan_shutdown(self):
        asyncio.run(self._does_not_start_a_worker_after_lifespan_shutdown())

    async def _does_not_start_a_worker_after_lifespan_shutdown(self):
        queue = MemoryQueue(queue_name="requests")
        handled = asyncio.Event()
        received = iter(({"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}))

        async def handle(entry):
            handled.set()
            return entry.payload

        async def application(scope, receive, send):
            raise AssertionError(f"Unexpected scope: {scope['type']}")

        async def receive():
            return next(received)

        async def send(message):
            if message["type"] == "lifespan.startup.complete":
                queue.enqueue("work")

        await with_queue_worker(
            application, handlers={"requests": handle}, queues={"requests": queue}
        )({"type": "lifespan"}, receive, send)
        await asyncio.sleep(0.05)

        entry = queue.get_entry(next(iter(queue._entries)))
        assert handled.is_set() is False
        assert entry.status.value == "queued"

    def test_checks_pending_entries_off_the_event_loop(self):
        asyncio.run(self._checks_pending_entries_off_the_event_loop())

    async def _checks_pending_entries_off_the_event_loop(self):
        queue = ThreadRecordingQueue(queue_name="requests")
        ThreadRecordingQueue.pending_check_thread_id = None
        loop_thread_id = threading.get_ident()
        received = iter(({"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}))

        async def application(scope, receive, send):
            raise AssertionError(f"Unexpected scope: {scope['type']}")

        async def receive():
            return next(received)

        async def send(message):
            return None

        await with_queue_worker(
            application,
            handlers={"requests": no_op_handler},
            queues={"requests": queue},
        )({"type": "lifespan"}, receive, send)

        assert ThreadRecordingQueue.pending_check_thread_id != loop_thread_id

    def test_creates_the_configured_worker_only_after_local_enqueue(self, monkeypatch):
        asyncio.run(
            self._creates_the_configured_worker_only_after_local_enqueue(monkeypatch)
        )

    async def _creates_the_configured_worker_only_after_local_enqueue(
        self, monkeypatch
    ):
        configured_queues = django_queue.QueueHandler(
            {
                "requests": {
                    "BACKEND": "django_queue.backends.MemoryQueue",
                    "LOCATION": "",
                    "WORKER": "tests.test_asgi.TrackingWorker",
                }
            }
        )
        monkeypatch.setattr(django_queue, "queues", configured_queues)
        queue = configured_queues["requests"]
        startup_complete = asyncio.Event()
        handled = asyncio.Event()
        shutdown = asyncio.Event()
        messages = []

        async def handle(entry):
            handled.set()
            return entry.payload

        async def application(scope, receive, send):
            raise AssertionError(f"Unexpected scope: {scope['type']}")

        async def receive():
            if not startup_complete.is_set():
                return {"type": "lifespan.startup"}
            await shutdown.wait()
            return {"type": "lifespan.shutdown"}

        async def send(message):
            messages.append(message)
            if message["type"] == "lifespan.startup.complete":
                startup_complete.set()

        task = asyncio.create_task(
            with_queue_worker(application, handlers={"requests": handle})(
                {"type": "lifespan"}, receive, send
            )
        )
        await asyncio.wait_for(startup_complete.wait(), timeout=1)
        assert TrackingWorker.instances == 0

        entry_id = queue.enqueue("work")
        await asyncio.wait_for(handled.wait(), timeout=1)
        assert TrackingWorker.instances == 1
        await asyncio.wait_for(
            _wait_until(lambda: queue.get_entry(entry_id).result == "work"), timeout=1
        )
        assert queue.get_entry(entry_id).result == "work"

        shutdown.set()
        await asyncio.wait_for(task, timeout=1)
        assert messages[-1] == {"type": "lifespan.shutdown.complete"}

    def test_starts_a_priority_queue_worker_for_its_public_queue_name(self):
        asyncio.run(self._starts_a_priority_queue_worker_for_its_public_queue_name())

    async def _starts_a_priority_queue_worker_for_its_public_queue_name(self):
        queue = MemoryPriorityQueue(queue_name="priority")
        startup_complete = asyncio.Event()
        handled = asyncio.Event()
        shutdown = asyncio.Event()

        async def handle(entry):
            handled.set()
            return entry.payload

        async def application(scope, receive, send):
            raise AssertionError(f"Unexpected scope: {scope['type']}")

        async def receive():
            if not startup_complete.is_set():
                return {"type": "lifespan.startup"}
            await shutdown.wait()
            return {"type": "lifespan.shutdown"}

        async def send(message):
            if message["type"] == "lifespan.startup.complete":
                startup_complete.set()

        task = asyncio.create_task(
            with_queue_worker(
                application, handlers={"requests": handle}, queues={"requests": queue}
            )({"type": "lifespan"}, receive, send)
        )
        await asyncio.wait_for(startup_complete.wait(), timeout=1)
        queue.enqueue("work")
        await asyncio.wait_for(handled.wait(), timeout=1)

        shutdown.set()
        await asyncio.wait_for(task, timeout=1)

    def test_exits_lifespan_when_lazy_worker_construction_fails(self):
        asyncio.run(self._exits_lifespan_when_lazy_worker_construction_fails())

    async def _exits_lifespan_when_lazy_worker_construction_fails(self):
        queue = MemoryQueue(queue_name="requests")
        queue.worker_class = FailingConstructionWorker
        startup_complete = asyncio.Event()

        async def application(scope, receive, send):
            raise AssertionError(f"Unexpected scope: {scope['type']}")

        async def receive():
            if not startup_complete.is_set():
                return {"type": "lifespan.startup"}
            await asyncio.Future()

        async def send(message):
            if message["type"] == "lifespan.startup.complete":
                startup_complete.set()

        task = asyncio.create_task(
            with_queue_worker(
                application,
                handlers={"requests": no_op_handler},
                queues={"requests": queue},
            )({"type": "lifespan"}, receive, send)
        )
        await asyncio.wait_for(startup_complete.wait(), timeout=1)
        queue.enqueue("work")

        with pytest.raises(RuntimeError, match="worker construction failed"):
            await asyncio.wait_for(task, timeout=1)

    def test_starts_a_worker_and_stops_it_during_lifespan_shutdown(
        self, caplog, monkeypatch
    ):
        asyncio.run(
            self._starts_a_worker_and_stops_it_during_lifespan_shutdown(
                caplog, monkeypatch
            )
        )

    async def _starts_a_worker_and_stops_it_during_lifespan_shutdown(
        self, caplog, monkeypatch
    ):
        configured_queues = django_queue.QueueHandler(
            {
                "requests": {
                    "BACKEND": "django_queue.backends.MemoryQueue",
                    "LOCATION": "",
                }
            }
        )
        monkeypatch.setattr(django_queue, "queues", configured_queues)
        DjangoQueueConfig("django_queue", django_queue).ready()
        queue = configured_queues["requests"]
        entry_id = queue.enqueue({"request_id": 42})
        shutdown = asyncio.Event()
        messages = []

        async def handle(entry):
            return {"processed": entry.payload["request_id"]}

        async def application(scope, receive, send):
            raise AssertionError(
                f"Wrapped application received unexpected scope: {scope['type']}"
            )

        async def receive():
            if not messages:
                return {"type": "lifespan.startup"}
            await shutdown.wait()
            return {"type": "lifespan.shutdown"}

        async def send(message):
            messages.append(message)

        caplog.set_level(logging.WARNING, logger="django_queue.asgi")
        task = asyncio.create_task(
            with_queue_worker(application, handlers={"requests": handle})(
                {"type": "lifespan"}, receive, send
            )
        )
        # Shut down only once the worker has recorded the entry's outcome: a
        # shutdown may interrupt an in-flight terminal write, so triggering it
        # mid-dispatch would race the very result this asserts.
        await asyncio.wait_for(
            _wait_until(
                lambda: queue.get_entry(entry_id).status is QueueEntryStatus.SUCCEEDED
            ),
            timeout=1,
        )
        shutdown.set()
        await asyncio.wait_for(task, timeout=1)

        assert messages == [
            {"type": "lifespan.startup.complete"},
            {"type": "lifespan.shutdown.complete"},
        ]
        assert queue.get_entry(entry_id).result == {"processed": 42}
        assert "not supported for production use" in caplog.text

    def test_shutdown_waits_for_an_active_handler_to_finish(self):
        asyncio.run(self._shutdown_waits_for_an_active_handler_to_finish())

    async def _shutdown_waits_for_an_active_handler_to_finish(self):
        queue = MemoryQueue(queue_name="requests")
        queue.enqueue("work")
        started = asyncio.Event()
        release = asyncio.Event()
        shutdown = asyncio.Event()
        messages = []

        async def handle(entry):
            started.set()
            await release.wait()
            return "completed during shutdown"

        async def application(scope, receive, send):
            raise AssertionError(
                f"Wrapped application received unexpected scope: {scope['type']}"
            )

        async def receive():
            if not messages:
                return {"type": "lifespan.startup"}
            await shutdown.wait()
            return {"type": "lifespan.shutdown"}

        async def send(message):
            messages.append(message)

        task = asyncio.create_task(
            with_queue_worker(
                application, handlers={"requests": handle}, queues={"requests": queue}
            )({"type": "lifespan"}, receive, send)
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        shutdown.set()
        await asyncio.sleep(0)

        assert task.done() is False
        release.set()
        await asyncio.wait_for(task, timeout=1)
        assert messages[-1] == {"type": "lifespan.shutdown.complete"}

    def test_reports_safe_startup_failure_for_an_invalid_default_queue_registration(
        self, caplog, monkeypatch
    ):
        asyncio.run(
            self._reports_safe_startup_failure_for_an_invalid_default_queue_registration(
                caplog, monkeypatch
            )
        )

    async def _reports_safe_startup_failure_for_an_invalid_default_queue_registration(
        self, caplog, monkeypatch
    ):
        messages = []

        async def application(scope, receive, send):
            raise AssertionError(
                f"Wrapped application received unexpected scope: {scope['type']}"
            )

        async def receive():
            return {"type": "lifespan.startup"}

        async def send(message):
            messages.append(message)

        caplog.set_level(logging.ERROR, logger="django_queue.asgi")
        monkeypatch.setattr(django_queue, "queues", django_queue.QueueHandler({}))
        await with_queue_worker(application, handlers={"missing": no_op_handler})(
            {"type": "lifespan"}, receive, send
        )

        assert messages == [
            {
                "type": "lifespan.startup.failed",
                "message": "Unable to start queue worker",
            }
        ]
        assert "missing" in caplog.text

    def test_reports_safe_startup_failure_for_an_unexpected_initial_message(self):
        asyncio.run(
            self._reports_safe_startup_failure_for_an_unexpected_initial_message()
        )

    async def _reports_safe_startup_failure_for_an_unexpected_initial_message(self):
        messages = []

        async def application(scope, receive, send):
            raise AssertionError(
                f"Wrapped application received unexpected scope: {scope['type']}"
            )

        async def receive():
            return {"type": "lifespan.unknown"}

        async def send(message):
            messages.append(message)

        await with_queue_worker(application, handlers={}, queues={})(
            {"type": "lifespan"}, receive, send
        )

        assert messages == [
            {
                "type": "lifespan.startup.failed",
                "message": "Unable to start queue worker",
            }
        ]

    def test_reports_safe_startup_failure_when_queue_lookup_raises(self, caplog):
        asyncio.run(self._reports_safe_startup_failure_when_queue_lookup_raises(caplog))

    async def _reports_safe_startup_failure_when_queue_lookup_raises(self, caplog):
        messages = []

        async def application(scope, receive, send):
            raise AssertionError(
                f"Wrapped application received unexpected scope: {scope['type']}"
            )

        async def handle(entry):
            return None

        async def receive():
            return {"type": "lifespan.startup"}

        async def send(message):
            messages.append(message)

        caplog.set_level(logging.ERROR, logger="django_queue.asgi")
        await with_queue_worker(
            application,
            handlers={"requests": handle},
            queues=FailingQueueLookup(),
        )({"type": "lifespan"}, receive, send)

        assert messages == [
            {
                "type": "lifespan.startup.failed",
                "message": "Unable to start queue worker",
            }
        ]
        assert "backend constructor failed" in caplog.text

    def test_cancelling_lifespan_stops_the_worker(self):
        asyncio.run(self._cancelling_lifespan_stops_the_worker())

    async def _cancelling_lifespan_stops_the_worker(self):
        queue = MemoryQueue(queue_name="requests")
        startup_complete = asyncio.Event()
        handled = asyncio.Event()

        async def handle(entry):
            handled.set()
            return "done"

        async def application(scope, receive, send):
            raise AssertionError(
                f"Wrapped application received unexpected scope: {scope['type']}"
            )

        async def receive():
            if not startup_complete.is_set():
                return {"type": "lifespan.startup"}
            await asyncio.Future()

        async def send(message):
            if message["type"] == "lifespan.startup.complete":
                startup_complete.set()

        task = asyncio.create_task(
            with_queue_worker(
                application, handlers={"requests": handle}, queues={"requests": queue}
            )({"type": "lifespan"}, receive, send)
        )
        await asyncio.wait_for(startup_complete.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        entry_id = queue.enqueue("work after cancellation")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(handled.wait(), timeout=0.15)
        assert queue.get_entry(entry_id).status.value == "queued"

    def test_ignores_unexpected_lifespan_messages_until_shutdown(self):
        asyncio.run(self._ignores_unexpected_lifespan_messages_until_shutdown())

    async def _ignores_unexpected_lifespan_messages_until_shutdown(self):
        messages = []
        received = iter(
            [
                {"type": "lifespan.startup"},
                {"type": "lifespan.unknown"},
                {"type": "lifespan.shutdown"},
            ]
        )

        async def application(scope, receive, send):
            raise AssertionError(
                f"Wrapped application received unexpected scope: {scope['type']}"
            )

        async def receive():
            return next(received)

        async def send(message):
            messages.append(message)

        await with_queue_worker(application, handlers={}, queues={})(
            {"type": "lifespan"}, receive, send
        )

        assert messages == [
            {"type": "lifespan.startup.complete"},
            {"type": "lifespan.shutdown.complete"},
        ]

    def test_logs_an_unexpected_worker_failure_without_restarting_it(self, caplog):
        asyncio.run(
            self._logs_an_unexpected_worker_failure_without_restarting_it(caplog)
        )

    async def _logs_an_unexpected_worker_failure_without_restarting_it(self, caplog):
        shutdown = asyncio.Event()
        messages = []

        async def handle(entry):
            raise AssertionError("The exploding queue must not dispatch an entry")

        async def application(scope, receive, send):
            raise AssertionError(
                f"Wrapped application received unexpected scope: {scope['type']}"
            )

        async def receive():
            if not messages:
                return {"type": "lifespan.startup"}
            await shutdown.wait()
            return {"type": "lifespan.shutdown"}

        async def send(message):
            messages.append(message)

        caplog.set_level(logging.ERROR, logger="django_queue.asgi")
        queue = ExplodingQueue(queue_name="requests")
        queue.enqueue("pending")
        task = asyncio.create_task(
            with_queue_worker(
                application,
                handlers={"requests": handle},
                queues={"requests": queue},
            )({"type": "lifespan"}, receive, send)
        )
        await asyncio.sleep(0.01)

        assert "will not be restarted" in caplog.text
        shutdown.set()
        await asyncio.wait_for(task, timeout=1)
        assert "failed before shutdown" not in caplog.text

    def test_delegates_non_lifespan_scopes_unchanged(self):
        asyncio.run(self._delegates_non_lifespan_scopes_unchanged())

    async def _delegates_non_lifespan_scopes_unchanged(self):
        received_scopes = []

        async def application(scope, receive, send):
            received_scopes.append(scope)

        async def receive():
            return {"type": "http.request"}

        async def send(message):
            raise AssertionError(
                f"Wrapped application sent unexpected message: {message}"
            )

        scope = {"type": "http", "path": "/"}
        await with_queue_worker(application, handlers={})(scope, receive, send)

        assert received_scopes == [scope]

    def test_django_app_configuration_leaves_pending_entries_undispatched(
        self, monkeypatch
    ):
        handler = django_queue.QueueHandler(
            {
                "default": {
                    "BACKEND": "django_queue.backends.MemoryQueue",
                    "LOCATION": "",
                }
            }
        )
        monkeypatch.setattr(django_queue, "queues", handler)
        entry_id = handler["default"].enqueue("pending")

        DjangoQueueConfig("django_queue", django_queue).ready()

        assert handler["default"].get_entry(entry_id).status.value == "queued"


class ExplodingQueue(MemoryQueue):
    def dequeue_entry(self):
        raise RuntimeError("backend failed")


class FailingQueueLookup:
    def __getitem__(self, alias):
        raise RuntimeError("backend constructor failed")


async def _wait_until(condition) -> None:
    while not condition():
        await asyncio.sleep(0)
