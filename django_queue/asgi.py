"""ASGI integration for opt-in, process-local queue workers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine, Iterable, Mapping
from typing import Any

import django_queue
from django_queue.signals import entry_enqueued
from django_queue.worker import QueueHandler as QueueEntryHandler
from django_queue.worker import QueueLookup

logger = logging.getLogger(__name__)

ASGIMessage = dict[str, object]
ASGIReceive = Callable[[], Coroutine[Any, Any, ASGIMessage]]
ASGISend = Callable[[ASGIMessage], Coroutine[Any, Any, None]]
ASGIApplication = Callable[
    [ASGIMessage, ASGIReceive, ASGISend], Coroutine[Any, Any, None]
]


def with_queue_worker(
    application: ASGIApplication,
    *,
    handlers: Mapping[str, QueueEntryHandler],
    queues: QueueLookup | None = None,
) -> ASGIApplication:
    """Wrap an ASGI application with lazy, process-local queue workers.

    Enqueue observation is local to this process. Use ``runqueues`` for shared
    backends in production.
    """
    worker_queues = django_queue.queues if queues is None else queues

    async def wrapped(scope: ASGIMessage, receive: ASGIReceive, send: ASGISend) -> None:
        if scope["type"] != "lifespan":
            await application(scope, receive, send)
            return

        startup_message = await receive()
        if startup_message["type"] != "lifespan.startup":
            await send(
                {
                    "type": "lifespan.startup.failed",
                    "message": "Unable to start queue worker",
                }
            )
            return
        try:
            configured_queues = {alias: worker_queues[alias] for alias in handlers}
        except Exception:
            logger.exception("Unable to start ASGI queue worker")
            await send(
                {
                    "type": "lifespan.startup.failed",
                    "message": "Unable to start queue worker",
                }
            )
            return

        worker_tasks: dict[str, asyncio.Task[None]] = {}
        loop = asyncio.get_running_loop()
        closing = False
        observing = False
        fatal_error: asyncio.Future[None] = loop.create_future()
        aliases_by_queue_name = {
            queue.queue_name or alias: alias
            for alias, queue in configured_queues.items()
        }

        def start_worker(alias: str) -> None:
            if closing or alias in worker_tasks:
                return
            try:
                worker = configured_queues[alias].create_worker(alias, handlers[alias])
            except Exception as exc:
                if not observing:
                    raise
                logger.exception("Unable to start ASGI queue worker for %s", alias)
                if not fatal_error.done():
                    fatal_error.set_exception(exc)
                return
            worker_task = asyncio.create_task(worker.run(), name=f"asgi-queue:{alias}")
            worker_tasks[alias] = worker_task
            worker_task.add_done_callback(_log_worker_failure)

        def observe_enqueue(sender, *, queue_name: str, **kwargs) -> None:
            if alias := aliases_by_queue_name.get(queue_name):
                loop.call_soon_threadsafe(start_worker, alias)

        try:
            for alias, queue in configured_queues.items():
                if await asyncio.to_thread(queue.has_pending_entries):
                    start_worker(alias)
        except Exception:
            closing = True
            await _stop_workers(worker_tasks.values())
            logger.exception("Unable to start ASGI queue worker")
            await send(
                {
                    "type": "lifespan.startup.failed",
                    "message": "Unable to start queue worker",
                }
            )
            return

        # Observe only once the startup sweep is done, so a worker can never be
        # constructed from a signal before the lifespan is ready to report failure.
        entry_enqueued.connect(observe_enqueue, weak=False)
        observing = True

        logger.warning(
            "The in-process ASGI queue worker is for local, single-process use and is not supported for production use; "
            "use an external worker with a shared backend such as Redis instead."
        )
        await send({"type": "lifespan.startup.complete"})

        receive_task = asyncio.create_task(receive())
        try:
            while True:
                done, _ = await asyncio.wait(
                    (receive_task, fatal_error), return_when=asyncio.FIRST_COMPLETED
                )
                if fatal_error in done:
                    fatal_error.result()
                if receive_task.result()["type"] == "lifespan.shutdown":
                    break
                receive_task = asyncio.create_task(receive())
        finally:
            closing = True
            entry_enqueued.disconnect(observe_enqueue)
            if not receive_task.done():
                receive_task.cancel()
            await asyncio.gather(receive_task, return_exceptions=True)
            await _stop_workers(worker_tasks.values())
        await send({"type": "lifespan.shutdown.complete"})

    return wrapped


def _log_worker_failure(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.error(
            "ASGI queue worker stopped unexpectedly and will not be restarted",
            exc_info=(type(exc), exc, exc.__traceback__),
        )


async def _stop_workers(worker_tasks: Iterable[asyncio.Task[None]]) -> None:
    tasks = tuple(worker_tasks)
    for worker_task in tasks:
        if not worker_task.done():
            worker_task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
