"""ASGI integration for an opt-in, process-local queue worker."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping

from django_queue import queues as configured_queues
from django_queue.worker import AsyncQueueWorker, QueueLookup
from django_queue.worker import QueueHandler as QueueEntryHandler

logger = logging.getLogger(__name__)

ASGIMessage = dict[str, object]
ASGIReceive = Callable[[], Awaitable[ASGIMessage]]
ASGISend = Callable[[ASGIMessage], Awaitable[None]]
ASGIApplication = Callable[[ASGIMessage, ASGIReceive, ASGISend], Awaitable[None]]


def with_queue_worker(
    application: ASGIApplication,
    *,
    handlers: Mapping[str, QueueEntryHandler],
    queues: QueueLookup | None = None,
) -> ASGIApplication:
    """Wrap a Django ASGI application with an explicitly configured queue worker."""
    worker_queues = configured_queues if queues is None else queues

    async def wrapped(scope: ASGIMessage, receive: ASGIReceive, send: ASGISend) -> None:
        if scope["type"] != "lifespan":
            await application(scope, receive, send)
            return

        startup_message = await receive()
        if startup_message["type"] != "lifespan.startup":
            return
        try:
            worker = AsyncQueueWorker(worker_queues, handlers)
            worker_task = asyncio.create_task(worker.run())
        except Exception:
            logger.exception("Unable to start ASGI queue worker")
            await send(
                {
                    "type": "lifespan.startup.failed",
                    "message": "Unable to start queue worker",
                }
            )
            return

        worker_task.add_done_callback(_log_worker_failure)
        logger.warning(
            "The in-process ASGI queue worker is for local, single-process use and is not supported for production use; "
            "use an external worker with a shared backend such as Redis instead."
        )
        await send({"type": "lifespan.startup.complete"})

        try:
            while (await receive())["type"] != "lifespan.shutdown":
                pass
        finally:
            await _stop_worker(worker_task)
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


async def _stop_worker(worker_task: asyncio.Task[None]) -> None:
    if not worker_task.done():
        worker_task.cancel()
    await asyncio.gather(worker_task, return_exceptions=True)
