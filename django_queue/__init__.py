import asyncio
import logging
import math
import threading
from collections.abc import Mapping

from django.conf import settings as django_settings
from django.utils.connection import BaseConnectionHandler, ConnectionProxy
from django.utils.module_loading import import_string

from .backends import InvalidQueueBackendError
from .backends.base import AsyncQueue, EventQueue
from .clock import ClockTime
from .entries import QueueEntry, QueueEntryStatus, validate_budget
from .event_worker import EventQueueWorker
from .listeners import queue_listener
from .observers import QueueSubscription, queue_observer
from .providers import QueueProvider
from .signals import queue_created
from .worker import AsyncQueueWorker, BaseQueueWorker, WorkerSnapshot, heartbeat

logger = logging.getLogger(__name__)

__all__ = (
    "AsyncQueueWorker",
    "BaseQueueWorker",
    "ClockTime",
    "EventQueueWorker",
    "QueueEntry",
    "QueueEntryStatus",
    "QueueProvider",
    "QueueSubscription",
    "WorkerSnapshot",
    "aclose_queues",
    "close_queues",
    "heartbeat",
    "initialise_queues",
    "queue",
    "queue_listener",
    "queue_observer",
    "queues",
)


DEFAULT_QUEUE_ALIAS = "default"
_FORBIDDEN_QUEUE_ALIAS_CHARACTERS = frozenset("*?[]")


class QueueHandler(BaseConnectionHandler):
    settings_name = "QUEUES"
    exception_class = InvalidQueueBackendError

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self._process_connections: dict[str, object] = {}
        self._process_connections_lock = threading.RLock()

    def configure_settings(self, settings):
        if settings is None:
            settings = getattr(django_settings, self.settings_name, {})
        if not isinstance(settings, Mapping):
            raise InvalidQueueBackendError(
                f"{self.settings_name} must be a mapping of queue aliases to configurations"
            )

        configured_queues = {}
        for alias, options in settings.items():
            if not isinstance(alias, str) or not alias:
                raise InvalidQueueBackendError(
                    f"Queue alias {alias!r} must be a non-empty string"
                )
            if any(
                character in alias for character in _FORBIDDEN_QUEUE_ALIAS_CHARACTERS
            ):
                raise InvalidQueueBackendError(
                    f"Queue alias '{alias}' cannot contain *, ?, [, or ]"
                )
            if not isinstance(options, Mapping):
                raise InvalidQueueBackendError(
                    f"Queue alias '{alias}' must use a mapping configuration"
                )
            backend = options.get("BACKEND")
            if not isinstance(backend, str) or not backend:
                raise InvalidQueueBackendError(
                    f"Queue alias '{alias}' must define a non-empty BACKEND string"
                )
            configured_options = dict(options)
            # Validate now so a bad entry class fails before any backend is built,
            # but leave the configured value in place so settings stay faithful.
            _resolve_extension_class(
                alias, "ENTRY_CLASS", configured_options.get("ENTRY_CLASS"), QueueEntry
            )
            if "TIMEOUT" in configured_options:
                _resolve_timeout(alias, configured_options["TIMEOUT"])
            if "RETENTION_TIMEOUT" in configured_options:
                _resolve_retention_timeout(
                    alias, configured_options["RETENTION_TIMEOUT"]
                )
            configured_queues[alias] = configured_options
        return configured_queues

    def create_connection(self, alias: str):
        params: dict = self.settings[alias].copy()
        # A configured queue is identified by its ``QUEUES`` alias.  Do not
        # allow a backend option to give it a second, divergent identity.
        params.pop("queue_name", None)
        params["queue_name"] = alias
        backend = params.pop("BACKEND")
        location = params.pop("LOCATION", "")
        params.pop("HANDLER", None)
        params.pop("WORKER", None)
        params.pop("TIMEOUT", None)
        params.pop("RETENTION_TIMEOUT", None)
        entry_class = _resolve_extension_class(
            alias, "ENTRY_CLASS", params.pop("ENTRY_CLASS", None), QueueEntry
        )
        try:
            backend_cls = import_string(backend)
        except ImportError as e:
            raise InvalidQueueBackendError(
                f"Queue alias '{alias}' could not find backend '{backend}': {e}"
            ) from e
        if getattr(backend_cls, "requires_entry_class_at_construction", False):
            params["entry_class"] = entry_class
        if getattr(backend_cls, "connection_scope", "thread") == "process":
            with self._process_connections_lock:
                if (queue := self._process_connections.get(alias)) is not None:
                    return queue
                queue = self._build_connection(
                    alias, backend_cls, location, params, entry_class
                )
                self._process_connections[alias] = queue
                return queue
        return self._build_connection(alias, backend_cls, location, params, entry_class)

    def _build_connection(
        self, alias: str, backend_cls, location: str, params: dict, entry_class
    ):
        try:
            queue = backend_cls(location, params)
        except (AttributeError, TypeError, ValueError) as exc:
            raise InvalidQueueBackendError(
                f"Queue alias '{alias}' has invalid backend options: {exc}"
            ) from exc
        handler = self.settings[alias].get("HANDLER")
        worker_class = self.settings[alias].get("WORKER")
        timeout_seconds = (
            _resolve_timeout(alias, self.settings[alias]["TIMEOUT"])
            if "TIMEOUT" in self.settings[alias]
            else None
        )
        retention_timeout = _resolve_retention_timeout(
            alias, self.settings[alias].get("RETENTION_TIMEOUT", 600)
        )
        if isinstance(queue, EventQueue) and handler is not None:
            raise InvalidQueueBackendError(
                f"Queue alias '{alias}' event queues use registered listeners and "
                "do not accept HANDLER metadata"
            )
        queue.entry_class = entry_class
        queue.timeout_seconds = timeout_seconds
        if isinstance(queue, AsyncQueue):
            queue.retention_timeout = retention_timeout
        if worker_class is not None:
            queue.worker_class = worker_class
        if isinstance(queue, AsyncQueue):
            queue.resolve_worker_class(alias)
        elif isinstance(queue, EventQueue):
            queue.resolve_event_worker_class(alias)
        queue_created.send(self, name=alias, instance=queue)
        return queue


queues = QueueHandler()

queue = ConnectionProxy(queues, DEFAULT_QUEUE_ALIAS)


def initialise_queues(queue_handler: QueueHandler | None = None) -> QueueHandler:
    """Validate and construct every queue configured for this Django process."""
    queue_handler = queues if queue_handler is None else queue_handler
    for alias in queue_handler:
        queue_handler[alias]
    return queue_handler


def close_queues(queue_handler: QueueHandler | None = None, **kwargs) -> None:
    """Synchronously release resources created through synchronous wrappers."""
    (queues if queue_handler is None else queue_handler).close_all()


async def aclose_queues(queue_handler: QueueHandler | None = None) -> None:
    """Dispose configured queues from the loop that acquired their resources."""
    queue_handler = queues if queue_handler is None else queue_handler
    results = await asyncio.gather(
        *(
            configured_queue.aclose()
            for configured_queue in queue_handler.all(initialized_only=True)
        ),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, Exception):
            logger.error("Unable to dispose queue resources", exc_info=result)


def _resolve_timeout(alias: str, value: object) -> float:
    """Validate an alias's execution budget, in seconds.

    Defers to the shared budget rule and re-raises as a configuration error:
    a settings fault is an `ImproperlyConfigured` whichever way the value is
    wrong, and an operator reading it wants the alias named, not the exception
    class distinguished.
    """
    try:
        return validate_budget(value)
    except (TypeError, ValueError) as exc:
        raise InvalidQueueBackendError(
            f"Queue alias '{alias}' TIMEOUT is invalid: {exc}"
        ) from exc


def _resolve_retention_timeout(alias: str, value: object) -> float | None:
    """Validate terminal-entry retention, allowing explicit opt-out."""
    if value is None:
        return None
    try:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError("must be a non-negative number of seconds or None")
        return float(value)
    except ValueError as exc:
        raise InvalidQueueBackendError(
            f"Queue alias '{alias}' RETENTION_TIMEOUT is invalid: {exc}"
        ) from exc


def _resolve_extension_class(
    alias: str, name: str, value: object, base_class: type
) -> type:
    if value is None:
        return base_class
    if isinstance(value, str):
        if not value:
            raise InvalidQueueBackendError(
                f"Queue alias '{alias}' {name} must be a class or non-empty dotted path"
            )
        try:
            value = import_string(value)
        except ImportError as exc:
            raise InvalidQueueBackendError(
                f"Queue alias '{alias}' {name} could not be imported: {exc}"
            ) from exc
    if not isinstance(value, type) or not issubclass(value, base_class):
        raise InvalidQueueBackendError(
            f"Queue alias '{alias}' {name} must be a {base_class.__name__} subclass"
        )
    return value
