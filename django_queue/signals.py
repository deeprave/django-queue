import logging

from django.dispatch import Signal

queue_created = Signal()
queue_closed = Signal()
entry_enqueued = Signal()

logger = logging.getLogger(__name__)


def send_entry_enqueued(sender, *, entry) -> None:
    """Notify observers without allowing them to break an enqueue."""
    for receiver, response in entry_enqueued.send_robust(
        sender, entry=entry, queue_name=entry.queue
    ):
        if isinstance(response, Exception):
            logger.error(
                "Queue entry enqueue observer failed: %r",
                receiver,
                exc_info=(type(response), response, response.__traceback__),
            )
