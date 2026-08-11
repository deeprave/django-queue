"""Run configured generic queue workers as a Django management command."""

from __future__ import annotations

import asyncio
import inspect
import logging
import signal
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import cast

from django.core.management.base import BaseCommand, CommandError
from django.utils.module_loading import import_string

import django_queue
from django_queue.backends.base import BaseQueue
from django_queue.backends.exceptions import InvalidQueueBackendError
from django_queue.worker import QueueHandler as QueueEntryHandler

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConfiguredWorkerActivation:
    alias: str
    queue: BaseQueue
    handler: QueueEntryHandler


class Command(BaseCommand):
    """Run one asynchronous worker for every configured queue handler."""

    help = "Run workers for queues configured with HANDLER."

    def handle(self, *args, **options) -> None:
        activations = self._create_workers()
        if not activations:
            self.stdout.write("No queue handlers configured.")
            return

        self.stdout.write(f"Starting {len(activations)} queue handlers.")
        asyncio.run(self._run_configured_workers(activations))

    def _create_workers(self) -> list[ConfiguredWorkerActivation]:
        try:
            queues = django_queue.initialise_queues()
            configured_handlers = [
                (alias, options["HANDLER"])
                for alias, options in queues.settings.items()
                if "HANDLER" in options
            ]
            activations = [
                ConfiguredWorkerActivation(
                    alias,
                    queues[alias],
                    self._load_handler(alias, handler_path),
                )
                for alias, handler_path in configured_handlers
            ]
            # Resolve every worker class up front so a misconfigured alias fails
            # here rather than when its queue first receives work.
            for activation in activations:
                activation.queue.resolve_worker_class(activation.alias)
        except InvalidQueueBackendError as exc:
            raise CommandError(str(exc)) from exc
        return activations

    @staticmethod
    def _load_handler(alias: str, handler_path: object) -> QueueEntryHandler:
        if not isinstance(handler_path, str) or not handler_path:
            raise CommandError(
                f"Queue '{alias}' HANDLER must be a non-empty dotted path."
            )
        try:
            handler = import_string(handler_path)
        except ImportError as exc:
            raise CommandError(
                f"Queue '{alias}' HANDLER could not be imported: {handler_path}"
            ) from exc
        if not callable(handler) or not _is_async_handler(handler):
            raise CommandError(
                f"Queue '{alias}' HANDLER must be an asynchronous callable."
            )
        return cast(QueueEntryHandler, handler)

    async def _run_configured_workers(
        self,
        activations: Sequence[ConfiguredWorkerActivation],
        shutdown_event: asyncio.Event | None = None,
    ) -> None:
        tasks = {
            asyncio.create_task(
                self._activate_worker(activation),
                name=f"runqueues:activate:{activation.alias}",
            ): activation.alias
            for activation in activations
        }
        await self._supervise_workers(
            tasks, shutdown_event, tuple(activation.queue for activation in activations)
        )

    async def _activate_worker(self, activation: ConfiguredWorkerActivation) -> None:
        while not await activation.queue.ahas_pending_entries():
            await asyncio.sleep(0.1)
        worker = activation.queue.create_worker(activation.alias, activation.handler)
        self.stdout.write(f"Started queue handler for {activation.alias}.")
        await worker.run()

    async def _supervise_workers(
        self,
        tasks: dict[asyncio.Task[None], str],
        shutdown_event: asyncio.Event | None,
        queues: Sequence[BaseQueue],
    ) -> None:
        shutdown_event = asyncio.Event() if shutdown_event is None else shutdown_event
        loop = asyncio.get_running_loop()
        signals = (signal.SIGINT, signal.SIGTERM)
        installed_signals = []
        for event_signal in signals:
            try:
                loop.add_signal_handler(event_signal, shutdown_event.set)
            except NotImplementedError, RuntimeError:
                logger.warning(
                    "Signal handler support is unavailable; worker shutdown relies on task cancellation."
                )
                break
            else:
                installed_signals.append(event_signal)

        shutdown_task = asyncio.create_task(shutdown_event.wait())
        try:
            while tasks:
                done, _ = await asyncio.wait(
                    [shutdown_task, *tasks], return_when=asyncio.FIRST_COMPLETED
                )
                if shutdown_task in done:
                    return
                # Select from the supervised workers rather than iterating
                # `done`, which also carries the shutdown task. Copied because
                # the loop mutates `tasks`.
                for task in [worker for worker in tasks if worker in done]:
                    alias = tasks.pop(task)
                    error = _task_error(task, alias)
                    if not tasks:
                        raise error
                    logger.error(
                        "Queue worker for %s stopped unexpectedly; remaining workers continue",
                        alias,
                        exc_info=(type(error), error, error.__traceback__),
                    )
        finally:
            for event_signal in installed_signals:
                loop.remove_signal_handler(event_signal)
            shutdown_task.cancel()
            for task in tasks:
                task.cancel()
            await asyncio.gather(shutdown_task, *tasks, return_exceptions=True)
            results = await asyncio.gather(
                *(queue.aclose() for queue in set(queues)), return_exceptions=True
            )
            for result in results:
                if isinstance(result, Exception):
                    logger.error("Unable to dispose queue resources", exc_info=result)


def _is_async_handler(handler: Callable[..., object]) -> bool:
    return inspect.iscoroutinefunction(handler) or inspect.iscoroutinefunction(
        type(handler).__call__
    )


def _task_error(task: asyncio.Task[None], alias: str) -> BaseException:
    if task.cancelled():
        return asyncio.CancelledError(
            f"Queue worker for {alias} was cancelled unexpectedly"
        )
    return task.exception() or RuntimeError(
        f"Queue worker for {alias} stopped unexpectedly"
    )
