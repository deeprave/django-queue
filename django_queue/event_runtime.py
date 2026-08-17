"""One process-local asyncio runtime for configured event queues."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Iterator
from typing import Protocol

from django_queue.backends.base import EventQueue

logger = logging.getLogger(__name__)


class QueueLookup(Protocol):
    """Configured queue aliases and their lazily-built queue instances."""

    def __iter__(self) -> Iterator[str]: ...

    def __getitem__(self, alias: str) -> object: ...


class EventRuntime:
    """Own one background loop and one worker task for each event queue."""

    restart_initial_delay = 1.0
    restart_max_delay = 30.0

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._queues: dict[str, EventQueue] = {}
        self._closed = False

    def start(self, queues: QueueLookup) -> None:
        """Start or reuse dispatchers for every configured event queue."""
        with self._lock:
            if self._closed:
                return
        event_queues = [
            (alias, queue)
            for alias in queues
            if isinstance(queue := queues[alias], EventQueue)
        ]
        if not event_queues:
            return
        if not self._start():
            return
        with self._lock:
            if self._closed:
                return
            loop = self._loop
            assert loop is not None
            for alias, queue in event_queues:
                loop.call_soon_threadsafe(self._start_worker, alias, queue)

    def _start(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            if self._thread is None:
                self._ready.clear()
                self._thread = threading.Thread(
                    target=self._run_loop,
                    name="django-queues-events",
                    daemon=True,
                )
                self._thread.start()
        self._ready.wait()
        with self._lock:
            loop = self._loop
            if not self._closed:
                return True
        assert loop is not None
        loop.call_soon_threadsafe(loop.stop)
        return False

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            self._loop = loop
            self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            results = loop.run_until_complete(
                asyncio.gather(
                    *(queue.aclose() for queue in self._queues.values()),
                    return_exceptions=True,
                )
            )
            for result in results:
                if isinstance(result, Exception):
                    logger.error("Unable to close event queue", exc_info=result)
            loop.close()
            with self._lock:
                self._workers.clear()
                self._queues.clear()
                self._loop = None
                self._thread = None

    def _start_worker(self, alias: str, queue: EventQueue) -> None:
        with self._lock:
            if self._closed or alias in self._workers:
                return
            self._queues[alias] = queue
            task = asyncio.create_task(
                self._run_worker(alias, queue), name=f"event:{alias}"
            )
            self._workers[alias] = task
        task.add_done_callback(lambda task: self._worker_done(alias, task))

    async def _run_worker(self, alias: str, queue: EventQueue) -> None:
        """Keep one queue dispatcher available across transient failures."""
        delay = self.restart_initial_delay
        while True:
            try:
                await queue.create_worker(alias).run()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Event worker stopped unexpectedly", extra={"queue": alias}
                )
            else:
                logger.error(
                    "Event worker stopped unexpectedly", extra={"queue": alias}
                )
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.restart_max_delay)

    def _worker_done(self, alias: str, task: asyncio.Task[None]) -> None:
        self._workers.pop(alias, None)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            logger.exception("Event worker supervisor failed", extra={"queue": alias})

    def shutdown(self) -> None:
        """Stop all event workers; primarily useful to controlled test hosts."""
        with self._lock:
            self._closed = True
            loop = self._loop
            thread = self._thread
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join()


event_runtime = EventRuntime()
