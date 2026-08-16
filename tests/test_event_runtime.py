import asyncio
import time
from typing import ClassVar
from uuid import UUID

import pytest

import django_queue
from django_queue.apps import DjangoQueueConfig, _start_event_runtime_on_request
from django_queue.backends import MemoryEventQueue
from django_queue.backends.exceptions import InvalidQueueBackendError
from django_queue.backends.memory import MemoryEventQueueWorker
from django_queue.event_runtime import EventRuntime
from django_queue.listeners import ListenerRegistration


class ClosingEventQueue(MemoryEventQueue):
    closed = 0

    async def aclose(self) -> None:
        type(self).closed += 1


class FlakyEventWorker(MemoryEventQueueWorker):
    runs = 0
    worker_ids: ClassVar[list[UUID]] = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        type(self).worker_ids.append(self._worker_id)

    async def run(self) -> None:
        type(self).runs += 1
        if type(self).runs == 1:
            raise OSError("temporary backend failure")
        await asyncio.Event().wait()


def test_first_request_adds_only_configured_event_queues_to_the_shared_runtime(
    monkeypatch,
):
    configured = django_queue.QueueRegistry(
        {
            "events": {
                "BACKEND": "django_queue.backends.MemoryEventQueue",
                "LOCATION": "",
            },
            "tasks": {
                "BACKEND": "django_queue.backends.MemoryAsyncQueue",
                "LOCATION": "",
            },
        }
    )
    started = []
    initialisations = []

    monkeypatch.setattr(
        "django_queue.initialise_queues",
        lambda: initialisations.append(configured) or configured,
    )
    monkeypatch.setattr(
        "django_queue.event_runtime.event_runtime.start",
        lambda queues: started.append(queues),
    )

    DjangoQueueConfig("django_queue", django_queue).ready()

    assert initialisations == [configured]
    assert started == []
    _start_event_runtime_on_request()
    assert initialisations == [configured, configured]
    assert started == [configured]


def test_task_only_configuration_does_not_start_an_event_loop():
    runtime = EventRuntime()
    configured = django_queue.QueueRegistry(
        {
            "tasks": {
                "BACKEND": "django_queue.backends.MemoryAsyncQueue",
                "LOCATION": "",
            }
        }
    )

    runtime.start(configured)

    assert runtime._thread is None


def test_runtime_dispatches_an_event_on_its_single_background_loop(monkeypatch):
    configured = django_queue.QueueRegistry(
        {
            "events": {
                "BACKEND": "django_queue.backends.MemoryEventQueue",
                "LOCATION": "",
            }
        }
    )
    received = []

    async def receive(entry):
        received.append(entry.payload)
        return True

    monkeypatch.setattr(
        "django_queue.event_worker.listeners_for",
        lambda queue_name: (ListenerRegistration(receive),),
    )
    runtime = EventRuntime()
    try:
        runtime.start(configured)
        configured["events"].enqueue("event")
        deadline = time.monotonic() + 1
        while not received and time.monotonic() < deadline:
            time.sleep(0.01)
        assert received == ["event"]
    finally:
        runtime.shutdown()


def test_runtime_looks_up_listeners_by_configured_alias(monkeypatch):
    configured = django_queue.QueueRegistry(
        {
            "events": {
                "BACKEND": "django_queue.backends.MemoryEventQueue",
                "LOCATION": "",
            }
        }
    )
    received = []

    async def receive(entry):
        received.append(entry.payload)
        return True

    monkeypatch.setattr(
        "django_queue.event_worker.listeners_for",
        lambda alias: (ListenerRegistration(receive),) if alias == "events" else (),
    )
    runtime = EventRuntime()
    try:
        runtime.start(configured)
        configured["events"].enqueue("event")
        deadline = time.monotonic() + 1
        while not received and time.monotonic() < deadline:
            time.sleep(0.01)
        assert received == ["event"]
    finally:
        runtime.shutdown()


def test_runtime_reuses_one_loop_and_starts_one_worker_per_event_queue():
    runtime = EventRuntime()
    configured = django_queue.QueueRegistry(
        {
            "first": {
                "BACKEND": "django_queue.backends.MemoryEventQueue",
                "LOCATION": "",
            },
            "second": {
                "BACKEND": "django_queue.backends.MemoryEventQueue",
                "LOCATION": "",
            },
        }
    )
    try:
        runtime.start(configured)
        first_thread = runtime._thread
        runtime.start(configured)
        deadline = time.monotonic() + 1
        while len(runtime._workers) != 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert runtime._thread is first_thread
        assert set(runtime._workers) == {"first", "second"}
    finally:
        runtime.shutdown()


def test_event_queue_rejects_task_handler_metadata():
    configured = django_queue.QueueRegistry(
        {
            "events": {
                "BACKEND": "django_queue.backends.MemoryEventQueue",
                "LOCATION": "",
                "HANDLER": "tests.test_event_runtime.receive",
            }
        }
    )

    with pytest.raises(InvalidQueueBackendError, match="event queues"):
        configured["events"]


def test_event_queue_uses_its_configured_event_worker_class():
    created = []

    class TrackingEventWorker(MemoryEventQueueWorker):
        def __init__(self, *args, **kwargs):
            created.append(self)
            super().__init__(*args, **kwargs)

    runtime = EventRuntime()
    configured = django_queue.QueueRegistry(
        {
            "events": {
                "BACKEND": "django_queue.backends.MemoryEventQueue",
                "LOCATION": "",
                "WORKER": TrackingEventWorker,
            }
        }
    )
    try:
        runtime.start(configured)
        deadline = time.monotonic() + 1
        while not created and time.monotonic() < deadline:
            time.sleep(0.01)
        assert isinstance(created[0], TrackingEventWorker)
    finally:
        runtime.shutdown()


def test_runtime_closes_event_queue_resources_on_shutdown():
    ClosingEventQueue.closed = 0
    runtime = EventRuntime()
    configured = django_queue.QueueRegistry(
        {
            "events": {
                "BACKEND": "tests.test_event_runtime.ClosingEventQueue",
                "LOCATION": "",
            }
        }
    )
    try:
        runtime.start(configured)
        deadline = time.monotonic() + 1
        while not runtime._workers and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        runtime.shutdown()

    assert ClosingEventQueue.closed == 1


def test_runtime_restarts_a_worker_after_an_infrastructure_failure(caplog):
    FlakyEventWorker.runs = 0
    FlakyEventWorker.worker_ids = []
    runtime = EventRuntime()
    runtime.restart_initial_delay = 0.001
    runtime.restart_max_delay = 0.001
    configured = django_queue.QueueRegistry(
        {
            "events": {
                "BACKEND": "django_queue.backends.MemoryEventQueue",
                "LOCATION": "",
                "WORKER": FlakyEventWorker,
            }
        }
    )
    try:
        runtime.start(configured)
        deadline = time.monotonic() + 1
        while FlakyEventWorker.runs < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert FlakyEventWorker.runs == 2
        assert len(FlakyEventWorker.worker_ids) == 2
        assert FlakyEventWorker.worker_ids[0] == FlakyEventWorker.worker_ids[1]
    finally:
        runtime.shutdown()

    assert "Event worker stopped unexpectedly" in caplog.text


def test_event_queue_rejects_a_task_worker_class():
    configured = django_queue.QueueRegistry(
        {
            "events": {
                "BACKEND": "django_queue.backends.MemoryEventQueue",
                "LOCATION": "",
                "WORKER": "django_queue.worker.AsyncQueueWorker",
            }
        }
    )

    with pytest.raises(InvalidQueueBackendError, match="EventQueueWorker"):
        configured["events"]
