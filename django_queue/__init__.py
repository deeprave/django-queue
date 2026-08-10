from collections.abc import Mapping

from django.conf import settings as django_settings
from django.utils.connection import BaseConnectionHandler, ConnectionProxy
from django.utils.module_loading import import_string

from .backends import InvalidQueueBackendError
from .clock import ClockTime
from .entries import QueueEntry, QueueEntryStatus
from .signals import queue_created
from .worker import AsyncQueueWorker, WorkerSnapshot

__all__ = (
    "AsyncQueueWorker",
    "ClockTime",
    "QueueEntry",
    "QueueEntryStatus",
    "WorkerSnapshot",
    "initialise_queues",
    "queue",
    "queues",
)


DEFAULT_QUEUE_ALIAS = "default"


class QueueHandler(BaseConnectionHandler):
    settings_name = "QUEUES"
    exception_class = InvalidQueueBackendError

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
            configured_queues[alias] = configured_options
        return configured_queues

    def create_connection(self, alias: str):
        params: dict = self.settings[alias].copy()
        params.setdefault("queue_name", alias)
        backend = params.pop("BACKEND")
        location = params.pop("LOCATION", "")
        params.pop("HANDLER", None)
        worker_class = params.pop("WORKER", None)
        timeout_seconds = (
            _resolve_timeout(alias, params.pop("TIMEOUT"))
            if "TIMEOUT" in params
            else None
        )
        entry_class = _resolve_extension_class(
            alias, "ENTRY_CLASS", params.pop("ENTRY_CLASS", None), QueueEntry
        )
        try:
            backend_cls = import_string(backend)
        except ImportError as e:
            raise InvalidQueueBackendError(
                f"Queue alias '{alias}' could not find backend '{backend}': {e}"
            ) from e
        try:
            queue = backend_cls(location, params)
        except (AttributeError, TypeError, ValueError) as exc:
            raise InvalidQueueBackendError(
                f"Queue alias '{alias}' has invalid backend options: {exc}"
            ) from exc
        queue.entry_class = entry_class
        queue.timeout_seconds = timeout_seconds
        if worker_class is not None:
            queue.worker_class = worker_class
        queue_created.send(self, name=params.get("queue_name", alias), instance=queue)
        return queue


queues = QueueHandler()

queue = ConnectionProxy(queues, DEFAULT_QUEUE_ALIAS)


def initialise_queues(queue_handler: QueueHandler | None = None) -> QueueHandler:
    """Validate and construct every queue configured for this Django process."""
    queue_handler = queues if queue_handler is None else queue_handler
    for alias in queue_handler:
        queue_handler[alias]
    return queue_handler


def close_queues(**kwargs):
    queues.close_all()


def _resolve_timeout(alias: str, value: object) -> float:
    """Validate an alias's execution budget, in seconds.

    A budget is a positive count of seconds. There is no value meaning
    unlimited: an unbounded handler is the defect the budget exists to remove,
    so a queue that wants no ceiling omits the setting and takes the default.
    """
    # isinstance so the checker narrows, then bool excluded explicitly: a flag
    # is an int in Python but not a count of seconds.
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        raise InvalidQueueBackendError(
            f"Queue alias '{alias}' TIMEOUT must be a positive number of seconds"
        )
    return value


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
