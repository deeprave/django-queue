"""Event listener registration for the dashboard's movement projection."""

from django_queue import queue_listener
from django_queue.entries import QueueEntry

from .projection import projection


@queue_listener("movement")
def receive_movement(entry: QueueEntry) -> bool:
    """Project a delivered movement event and consume invalid events safely."""
    return projection.update(entry.payload)
