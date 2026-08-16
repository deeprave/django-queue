import asyncio
import logging
import signal
import threading
from collections.abc import Awaitable, Callable
from io import StringIO

import pytest
from django.core.management.base import CommandError

import django_queue
from django_queue.backends import MemoryAsyncQueue
from django_queue.backends.memory import MemoryAsyncQueueWorker
from django_queue.management.commands.runqueues import (
    Command,
    ConfiguredWorkerActivation,
)
from django_queue.worker import AsyncQueueWorker


async def handle_entry(entry):
    return {"handled": entry.payload}


class AsynchronousCallable:
    async def __call__(self, entry):
        return {"handled": entry.payload}


asynchronous_callable = AsynchronousCallable()


def synchronous_handler(entry):
    return entry.payload


class TrackingWorker(AsyncQueueWorker):
    instances = 0

    def __init__(self, *args, **kwargs):
        type(self).instances += 1
        super().__init__(*args, **kwargs)


@pytest.fixture(autouse=True)
def reset_tracking_worker_instances():
    TrackingWorker.instances = 0
    yield
    TrackingWorker.instances = 0


class TestRunQueuesCommand:
    def test_exits_successfully_when_no_queue_handlers_are_configured(
        self, monkeypatch
    ):
        monkeypatch.setattr(django_queue, "queues", django_queue.QueueHandler({}))
        output = StringIO()

        Command(stdout=output).handle()

        assert output.getvalue() == "No queue handlers configured.\n"

    def test_starts_workers_only_when_configured_queues_have_entries(self, monkeypatch):
        asyncio.run(
            self._starts_workers_only_when_configured_queues_have_entries(monkeypatch)
        )

    async def _starts_workers_only_when_configured_queues_have_entries(
        self, monkeypatch
    ):
        queues = django_queue.QueueHandler(
            {
                "first": {
                    "BACKEND": "django_queue.backends.MemoryAsyncQueue",
                    "HANDLER": "tests.test_runqueues.handle_entry",
                    "LOCATION": "",
                    "WORKER": "tests.test_runqueues.TrackingWorker",
                },
                "second": {
                    "BACKEND": "django_queue.backends.MemoryAsyncQueue",
                    "HANDLER": "tests.test_runqueues.handle_entry",
                    "LOCATION": "",
                    "WORKER": "tests.test_runqueues.TrackingWorker",
                },
            }
        )
        monkeypatch.setattr(django_queue, "queues", queues)
        output = StringIO()
        command = Command(stdout=output)
        shutdown = asyncio.Event()

        activations = command._create_workers()
        assert TrackingWorker.instances == 0

        task = asyncio.create_task(
            command._run_configured_workers(activations, shutdown)
        )
        await asyncio.sleep(0)
        assert TrackingWorker.instances == 0

        first_id = await queues["first"].aenqueue("first")
        second_id = await queues["second"].aenqueue("second")

        async def entries_completed():
            first = await queues["first"].aget_entry(first_id)
            second = await queues["second"].aget_entry(second_id)
            return first.result is not None and second.result is not None

        await asyncio.wait_for(
            self._wait_until(entries_completed),
            timeout=1,
        )
        shutdown.set()
        await asyncio.wait_for(task, timeout=1)

        assert (await queues["first"].aget_entry(first_id)).result == {
            "handled": "first"
        }
        assert (await queues["second"].aget_entry(second_id)).result == {
            "handled": "second"
        }
        assert TrackingWorker.instances == 2
        assert set(output.getvalue().splitlines()) == {
            "Started queue handler for first.",
            "Started queue handler for second.",
        }

    def test_reports_each_queue_alias_as_its_worker_starts(self, monkeypatch):
        asyncio.run(self._reports_each_queue_alias_as_its_worker_starts(monkeypatch))

    async def _reports_each_queue_alias_as_its_worker_starts(self, monkeypatch):
        queues = django_queue.QueueHandler(
            {
                "first": {
                    "BACKEND": "django_queue.backends.MemoryAsyncQueue",
                    "HANDLER": "tests.test_runqueues.handle_entry",
                    "LOCATION": "",
                },
                "second": {
                    "BACKEND": "django_queue.backends.MemoryAsyncQueue",
                    "HANDLER": "tests.test_runqueues.handle_entry",
                    "LOCATION": "",
                },
            }
        )
        monkeypatch.setattr(django_queue, "queues", queues)
        output = StringIO()
        command = Command(stdout=output)
        shutdown = asyncio.Event()

        task = asyncio.create_task(
            command._run_configured_workers(command._create_workers(), shutdown)
        )
        await queues["first"].aenqueue("first")
        await asyncio.wait_for(
            self._wait_until(
                lambda: output.getvalue() == "Started queue handler for first.\n"
            ),
            timeout=1,
        )
        shutdown.set()
        await asyncio.wait_for(task, timeout=1)

        assert output.getvalue() == "Started queue handler for first.\n"

    def test_rejects_an_invalid_handler_path_before_starting_workers(self, monkeypatch):
        queues = django_queue.QueueHandler(
            {
                "default": {
                    "BACKEND": "django_queue.backends.MemoryAsyncQueue",
                    "HANDLER": "tests.test_runqueues.not_a_handler",
                    "LOCATION": "",
                }
            }
        )
        monkeypatch.setattr(django_queue, "queues", queues)

        with pytest.raises(CommandError, match="default.*HANDLER"):
            Command().handle()

    def test_rejects_an_invalid_worker_before_starting_workers(self, monkeypatch):
        queues = django_queue.QueueHandler(
            {
                "default": {
                    "BACKEND": "django_queue.backends.MemoryAsyncQueue",
                    "HANDLER": "tests.test_runqueues.handle_entry",
                    "WORKER": "tests.test_runqueues.handle_entry",
                    "LOCATION": "",
                }
            }
        )
        monkeypatch.setattr(django_queue, "queues", queues)

        with pytest.raises(CommandError, match="default.*WORKER"):
            Command().handle()

    def test_rejects_an_invalid_entry_class_before_starting_workers(self, monkeypatch):
        queues = django_queue.QueueHandler(
            {
                "default": {
                    "BACKEND": "django_queue.backends.MemoryAsyncQueue",
                    "HANDLER": "tests.test_runqueues.handle_entry",
                    "ENTRY_CLASS": "tests.test_runqueues.handle_entry",
                    "LOCATION": "",
                }
            }
        )
        monkeypatch.setattr(django_queue, "queues", queues)

        with pytest.raises(CommandError, match="default.*ENTRY_CLASS"):
            Command().handle()

    def test_does_not_construct_a_worker_while_validating_configuration(
        self, monkeypatch
    ):
        queues = django_queue.QueueHandler(
            {
                "default": {
                    "BACKEND": "django_queue.backends.MemoryAsyncQueue",
                    "HANDLER": "tests.test_runqueues.handle_entry",
                    "WORKER": "tests.test_runqueues.TrackingWorker",
                    "LOCATION": "",
                }
            }
        )
        monkeypatch.setattr(django_queue, "queues", queues)

        activations = Command()._create_workers()

        assert len(activations) == 1
        assert TrackingWorker.instances == 0

    def test_rejects_a_non_asynchronous_handler_before_starting_workers(
        self, monkeypatch
    ):
        queues = django_queue.QueueHandler(
            {
                "default": {
                    "BACKEND": "django_queue.backends.MemoryAsyncQueue",
                    "HANDLER": "tests.test_runqueues.synchronous_handler",
                    "LOCATION": "",
                }
            }
        )
        monkeypatch.setattr(django_queue, "queues", queues)

        with pytest.raises(CommandError, match="default.*asynchronous"):
            Command().handle()

    def test_accepts_an_asynchronous_callable_object(self, monkeypatch):
        queues = django_queue.QueueHandler(
            {
                "default": {
                    "BACKEND": "django_queue.backends.MemoryAsyncQueue",
                    "HANDLER": "tests.test_runqueues.asynchronous_callable",
                    "LOCATION": "",
                }
            }
        )
        monkeypatch.setattr(django_queue, "queues", queues)

        activations = Command()._create_workers()

        assert len(activations) == 1
        assert (
            activations[0].queue.resolve_worker_class("default")
            is MemoryAsyncQueueWorker
        )

    def test_activates_each_worker_on_its_own_queue_clock(self, monkeypatch):
        """The shared time basis has to hold where workers are really built."""
        queues = django_queue.QueueHandler(
            {
                "default": {
                    "BACKEND": "django_queue.backends.MemoryAsyncQueue",
                    "HANDLER": "tests.test_runqueues.handle_entry",
                    "LOCATION": "",
                }
            }
        )
        monkeypatch.setattr(django_queue, "queues", queues)
        activation = Command()._create_workers()[0]

        worker = activation.queue.create_worker(activation.alias, activation.handler)

        assert worker.clock is activation.queue.clock

    def test_continues_healthy_workers_after_another_worker_fails(self, caplog):
        asyncio.run(self._continues_healthy_workers_after_another_worker_fails(caplog))

    async def _continues_healthy_workers_after_another_worker_fails(self, caplog):
        shutdown = asyncio.Event()
        healthy_queue = MemoryAsyncQueue(queue_name="healthy")
        await healthy_queue.aenqueue("healthy")
        failed_queue = ExplodingQueue(queue_name="failed")
        await failed_queue.aenqueue("failed")
        caplog.set_level(
            logging.ERROR, logger="django_queue.management.commands.runqueues"
        )
        task = asyncio.create_task(
            Command()._run_configured_workers(
                [
                    ConfiguredWorkerActivation("healthy", healthy_queue, handle_entry),
                    ConfiguredWorkerActivation("failed", failed_queue, handle_entry),
                ],
                shutdown,
            )
        )

        await asyncio.wait_for(
            asyncio.to_thread(failed_queue.dequeue_started.wait), timeout=1
        )
        await asyncio.wait_for(
            self._wait_until(
                lambda: "Queue worker for failed stopped unexpectedly" in caplog.text
            ),
            timeout=1,
        )

        assert task.done() is False
        assert "Queue worker for failed stopped unexpectedly" in caplog.text

        shutdown.set()
        await asyncio.wait_for(task, timeout=1)

    def test_keeps_watching_an_idle_queue_after_another_worker_fails(self, caplog):
        asyncio.run(
            self._keeps_watching_an_idle_queue_after_another_worker_fails(caplog)
        )

    async def _keeps_watching_an_idle_queue_after_another_worker_fails(self, caplog):
        shutdown = asyncio.Event()
        idle_queue = MemoryAsyncQueue(queue_name="idle")
        failed_queue = ExplodingQueue(queue_name="failed")
        await failed_queue.aenqueue("failed")
        caplog.set_level(
            logging.ERROR, logger="django_queue.management.commands.runqueues"
        )
        task = asyncio.create_task(
            Command()._run_configured_workers(
                [
                    ConfiguredWorkerActivation("idle", idle_queue, handle_entry),
                    ConfiguredWorkerActivation("failed", failed_queue, handle_entry),
                ],
                shutdown,
            )
        )

        await asyncio.wait_for(
            self._wait_until(
                lambda: "Queue worker for failed stopped unexpectedly" in caplog.text
            ),
            timeout=1,
        )

        # The idle queue never activated, so it has no worker - but it is still
        # configured and still being watched, and must not be torn down.
        assert task.done() is False

        idle_entry_id = await idle_queue.aenqueue("idle work")

        async def idle_entry_completed():
            entry = await idle_queue.aget_entry(idle_entry_id)
            return entry.result is not None

        await asyncio.wait_for(
            self._wait_until(idle_entry_completed),
            timeout=1,
        )

        shutdown.set()
        await asyncio.wait_for(task, timeout=1)

    @staticmethod
    async def _wait_until(condition: Callable[[], Awaitable[bool] | bool]) -> None:
        while True:
            result = condition()
            if result if isinstance(result, bool) else await result:
                return
            await asyncio.sleep(0)

    def test_exits_when_the_last_active_worker_fails(self):
        asyncio.run(self._exits_when_the_last_active_worker_fails())

    async def _exits_when_the_last_active_worker_fails(self):
        queue = ExplodingQueue(queue_name="default")
        await queue.aenqueue("work")

        with pytest.raises(RuntimeError, match="backend failed"):
            await Command()._run_configured_workers(
                [ConfiguredWorkerActivation("default", queue, handle_entry)]
            )

    def test_shutdown_request_cancels_and_awaits_workers(self):
        asyncio.run(self._shutdown_request_cancels_and_awaits_workers())

    async def _shutdown_request_cancels_and_awaits_workers(self):
        queue = MemoryAsyncQueue(queue_name="default")
        started = asyncio.Event()
        release = asyncio.Event()
        shutdown = asyncio.Event()

        async def handler(entry):
            started.set()
            await release.wait()
            return "done"

        await queue.aenqueue("work")
        task = asyncio.create_task(
            Command()._run_configured_workers(
                [ConfiguredWorkerActivation("default", queue, handler)],
                shutdown,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        shutdown.set()
        await asyncio.sleep(0)

        assert task.done() is False
        release.set()
        await asyncio.wait_for(task, timeout=1)

    def test_runs_when_the_event_loop_does_not_support_signal_handlers(
        self, monkeypatch, caplog
    ):
        asyncio.run(
            self._runs_when_the_event_loop_does_not_support_signal_handlers(
                monkeypatch, caplog
            )
        )

    async def _runs_when_the_event_loop_does_not_support_signal_handlers(
        self, monkeypatch, caplog
    ):
        loop = asyncio.get_running_loop()
        shutdown = asyncio.Event()
        shutdown.set()

        def unsupported_signal_handler(*args):
            raise NotImplementedError

        monkeypatch.setattr(loop, "add_signal_handler", unsupported_signal_handler)
        caplog.set_level(
            logging.WARNING, logger="django_queue.management.commands.runqueues"
        )

        await Command()._run_configured_workers([], shutdown)

        assert "Signal handler support is unavailable" in caplog.text

    def test_removes_only_the_signal_handlers_that_were_installed(self, monkeypatch):
        asyncio.run(
            self._removes_only_the_signal_handlers_that_were_installed(monkeypatch)
        )

    async def _removes_only_the_signal_handlers_that_were_installed(self, monkeypatch):
        loop = asyncio.get_running_loop()
        shutdown = asyncio.Event()
        shutdown.set()
        installed = []
        removed = []

        def add_signal_handler(event_signal, callback):
            if event_signal is signal.SIGTERM:
                raise NotImplementedError
            installed.append(event_signal)

        def remove_signal_handler(event_signal):
            removed.append(event_signal)
            return True

        monkeypatch.setattr(loop, "add_signal_handler", add_signal_handler)
        monkeypatch.setattr(loop, "remove_signal_handler", remove_signal_handler)

        await Command()._run_configured_workers([], shutdown)

        assert installed == [signal.SIGINT]
        assert removed == [signal.SIGINT]


class ExplodingQueue(MemoryAsyncQueue):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dequeue_started = threading.Event()

    async def adequeue_entry(self):
        self.dequeue_started.set()
        raise RuntimeError("backend failed")
