import asyncio
import logging
import signal
import threading
from collections.abc import Callable
from io import StringIO

import pytest
from django.core.management.base import CommandError

import django_queue
from django_queue.backends import MemoryQueue
from django_queue.management.commands.runqueues import Command
from django_queue.worker import AsyncQueueWorker


async def handle_entry(entry):
    return {"handled": entry.payload}


class AsynchronousCallable:
    async def __call__(self, entry):
        return {"handled": entry.payload}


asynchronous_callable = AsynchronousCallable()


def synchronous_handler(entry):
    return entry.payload


class TestRunQueuesCommand:
    def test_exits_successfully_when_no_queue_handlers_are_configured(
        self, monkeypatch
    ):
        monkeypatch.setattr(django_queue, "queues", django_queue.QueueHandler({}))
        output = StringIO()

        Command(stdout=output).handle()

        assert output.getvalue() == "No queue handlers configured.\n"

    def test_starts_one_worker_for_each_configured_handler(self, monkeypatch):
        queues = django_queue.QueueHandler(
            {
                "first": {
                    "BACKEND": "django_queue.backends.MemoryQueue",
                    "HANDLER": "tests.test_runqueues.handle_entry",
                    "LOCATION": "",
                },
                "second": {
                    "BACKEND": "django_queue.backends.MemoryQueue",
                    "HANDLER": "tests.test_runqueues.handle_entry",
                    "LOCATION": "",
                },
            }
        )
        monkeypatch.setattr(django_queue, "queues", queues)
        first_id = queues["first"].enqueue("first")
        second_id = queues["second"].enqueue("second")
        output = StringIO()
        command = Command(stdout=output)

        async def run_workers(workers):
            tasks = [asyncio.create_task(worker.run()) for _, worker in workers]
            while any(
                queues[name].get_entry(entry_id).result is None
                for name, entry_id in zip(queues, (first_id, second_id), strict=True)
            ):
                await asyncio.sleep(0.001)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        monkeypatch.setattr(command, "_run_workers", run_workers)
        command.handle()

        assert queues["first"].get_entry(first_id).result == {"handled": "first"}
        assert queues["second"].get_entry(second_id).result == {"handled": "second"}
        assert output.getvalue() == "Starting 2 queue handlers.\n"

    def test_reports_each_queue_alias_as_its_worker_starts(self, monkeypatch):
        asyncio.run(self._reports_each_queue_alias_as_its_worker_starts(monkeypatch))

    async def _reports_each_queue_alias_as_its_worker_starts(self, monkeypatch):
        queues = django_queue.QueueHandler(
            {
                "first": {
                    "BACKEND": "django_queue.backends.MemoryQueue",
                    "HANDLER": "tests.test_runqueues.handle_entry",
                    "LOCATION": "",
                },
                "second": {
                    "BACKEND": "django_queue.backends.MemoryQueue",
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
            command._run_workers(command._create_workers(), shutdown)
        )
        await asyncio.sleep(0)
        shutdown.set()
        await asyncio.wait_for(task, timeout=1)

        assert output.getvalue() == (
            "Started queue handler for first.\nStarted queue handler for second.\n"
        )

    def test_rejects_an_invalid_handler_path_before_starting_workers(self, monkeypatch):
        queues = django_queue.QueueHandler(
            {
                "default": {
                    "BACKEND": "django_queue.backends.MemoryQueue",
                    "HANDLER": "tests.test_runqueues.not_a_handler",
                    "LOCATION": "",
                }
            }
        )
        monkeypatch.setattr(django_queue, "queues", queues)

        with pytest.raises(CommandError, match="default.*HANDLER"):
            Command().handle()

    def test_rejects_a_non_asynchronous_handler_before_starting_workers(
        self, monkeypatch
    ):
        queues = django_queue.QueueHandler(
            {
                "default": {
                    "BACKEND": "django_queue.backends.MemoryQueue",
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
                    "BACKEND": "django_queue.backends.MemoryQueue",
                    "HANDLER": "tests.test_runqueues.asynchronous_callable",
                    "LOCATION": "",
                }
            }
        )
        monkeypatch.setattr(django_queue, "queues", queues)

        workers = Command()._create_workers()

        assert len(workers) == 1

    def test_continues_healthy_workers_after_another_worker_fails(self, caplog):
        asyncio.run(self._continues_healthy_workers_after_another_worker_fails(caplog))

    async def _continues_healthy_workers_after_another_worker_fails(self, caplog):
        shutdown = asyncio.Event()
        healthy_worker = AsyncQueueWorker(
            {"healthy": MemoryQueue(queue_name="healthy")},
            {"healthy": handle_entry},
        )
        failed_queue = ExplodingQueue(queue_name="failed")
        failed_worker = AsyncQueueWorker(
            {"failed": failed_queue},
            {"failed": handle_entry},
        )
        caplog.set_level(
            logging.ERROR, logger="django_queue.management.commands.runqueues"
        )
        task = asyncio.create_task(
            Command()._run_workers(
                [("healthy", healthy_worker), ("failed", failed_worker)], shutdown
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
        assert healthy_worker.running is True
        assert "Queue worker for failed stopped unexpectedly" in caplog.text

        shutdown.set()
        await asyncio.wait_for(task, timeout=1)
        assert healthy_worker.running is False

    @staticmethod
    async def _wait_until(condition: Callable[[], bool]) -> None:
        while not condition():
            await asyncio.sleep(0)

    def test_exits_when_the_last_active_worker_fails(self):
        asyncio.run(self._exits_when_the_last_active_worker_fails())

    async def _exits_when_the_last_active_worker_fails(self):
        first_worker = AsyncQueueWorker(
            {"first": ExplodingQueue(queue_name="first")},
            {"first": handle_entry},
        )
        second_worker = AsyncQueueWorker(
            {"second": ExplodingQueue(queue_name="second")},
            {"second": handle_entry},
        )

        with pytest.raises(RuntimeError, match="backend failed"):
            await Command()._run_workers(
                [("first", first_worker), ("second", second_worker)]
            )

    def test_shutdown_request_cancels_and_awaits_workers(self):
        asyncio.run(self._shutdown_request_cancels_and_awaits_workers())

    async def _shutdown_request_cancels_and_awaits_workers(self):
        queue = MemoryQueue(queue_name="default")
        started = asyncio.Event()
        release = asyncio.Event()
        shutdown = asyncio.Event()

        async def handler(entry):
            started.set()
            await release.wait()
            return "done"

        queue.enqueue("work")
        worker = AsyncQueueWorker({"default": queue}, {"default": handler})
        task = asyncio.create_task(
            Command()._run_workers([("default", worker)], shutdown)
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        shutdown.set()
        await asyncio.sleep(0)

        assert task.done() is False
        release.set()
        await asyncio.wait_for(task, timeout=1)
        assert worker.running is False

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

        await Command()._run_workers([], shutdown)

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

        await Command()._run_workers([], shutdown)

        assert installed == [signal.SIGINT]
        assert removed == [signal.SIGINT]


class ExplodingQueue(MemoryQueue):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dequeue_started = threading.Event()

    def dequeue_entry(self):
        self.dequeue_started.set()
        raise RuntimeError("backend failed")
