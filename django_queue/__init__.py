from django.utils.connection import BaseConnectionHandler, ConnectionProxy
from django.utils.module_loading import import_string

from .backends import InvalidQueueBackendError
from .entries import QueueEntry, QueueEntryStatus
from .signals import queue_created
from .worker import AsyncQueueWorker

__all__ = ("AsyncQueueWorker", "QueueEntry", "QueueEntryStatus", "queue", "queues")


DEFAULT_QUEUE_ALIAS = "default"


class QueueHandler(BaseConnectionHandler):
    settings_name = "QUEUES"
    exception_class = InvalidQueueBackendError

    def create_connection(self, alias: str):
        params: dict = self.settings[alias].copy()
        params.setdefault("queue_name", alias)
        backend = params.pop("BACKEND")
        location = params.pop("LOCATION", "")
        try:
            backend_cls = import_string(backend)
        except ImportError as e:
            raise InvalidQueueBackendError(f"Could not find backend '{backend}': {e}") from e
        queue = backend_cls(location, params)
        queue_created.send(self, name=params.get("queue_name", alias), instance=queue)
        return queue


queues = QueueHandler()

queue = ConnectionProxy(queues, DEFAULT_QUEUE_ALIAS)


def close_queues(**kwargs):
    queues.close_all()
