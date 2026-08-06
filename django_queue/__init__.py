from collections.abc import Mapping

from django.conf import settings as django_settings
from django.utils.connection import BaseConnectionHandler, ConnectionProxy
from django.utils.module_loading import import_string

from .backends import InvalidQueueBackendError
from .entries import QueueEntry, QueueEntryStatus
from .signals import queue_created
from .worker import AsyncQueueWorker

__all__ = (
    "AsyncQueueWorker",
    "QueueEntry",
    "QueueEntryStatus",
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
            raise InvalidQueueBackendError(f"{self.settings_name} must be a mapping of queue aliases to configurations")

        configured_queues = {}
        for alias, options in settings.items():
            if not isinstance(alias, str) or not alias:
                raise InvalidQueueBackendError(f"Queue alias {alias!r} must be a non-empty string")
            if not isinstance(options, Mapping):
                raise InvalidQueueBackendError(f"Queue alias '{alias}' must use a mapping configuration")
            backend = options.get("BACKEND")
            if not isinstance(backend, str) or not backend:
                raise InvalidQueueBackendError(f"Queue alias '{alias}' must define a non-empty BACKEND string")
            configured_queues[alias] = dict(options)
        return configured_queues

    def create_connection(self, alias: str):
        params: dict = self.settings[alias].copy()
        params.setdefault("queue_name", alias)
        backend = params.pop("BACKEND")
        location = params.pop("LOCATION", "")
        try:
            backend_cls = import_string(backend)
        except ImportError as e:
            raise InvalidQueueBackendError(f"Queue alias '{alias}' could not find backend '{backend}': {e}") from e
        try:
            queue = backend_cls(location, params)
        except (AttributeError, TypeError, ValueError) as exc:
            raise InvalidQueueBackendError(f"Queue alias '{alias}' has invalid backend options: {exc}") from exc
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
