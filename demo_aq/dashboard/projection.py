"""Observer-backed, process-local rows for the async queue dashboard."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator, Mapping
from typing import Any

from django_queue import QueueSubscription, queue_observer
from django_queue.entries import QueueEntry


class DashboardProjection:
    """Maintain JSON-ready entry rows from `demo` lifecycle snapshots."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._rows: dict[str, dict[str, Any]] = {}
        self._subscription: QueueSubscription | None = None
        self._version = 0

    def start(self) -> None:
        """Subscribe once, allowing the observer bootstrap to seed the rows."""
        with self._lock:
            if self._subscription is not None:
                return
            self._subscription = queue_observer("demo", self.update)

    def refresh(self) -> None:
        """Replace the observer subscription and its local projection."""
        with self._changed:
            if self._subscription is not None:
                self._subscription.unsubscribe()
                self._subscription = None
            self._rows.clear()
            self._version += 1
            self._changed.notify_all()
        self.start()

    def update(self, entry: QueueEntry) -> None:
        """Replace one retained row with its latest observer snapshot."""
        payload = entry.payload if isinstance(entry.payload, Mapping) else {}
        row = {
            "id": str(entry.id),
            "state": entry.status.value,
            "message": payload.get("message", ""),
            "metadata": entry.payload,
            "queued_at": entry.queued_at.to_timestamp(),
            "finished_at": (
                entry.finished_at.to_timestamp() if entry.finished_at else None
            ),
            "timeout_seconds": entry.timeout_seconds,
        }
        with self._changed:
            self._rows[row["id"]] = row
            self._version += 1
            self._changed.notify_all()

    def rows(self) -> list[dict[str, Any]]:
        """Return a sorted snapshot of the local presentation state."""
        with self._lock:
            return self._sorted_rows()

    def events(self) -> Iterator[str]:
        """Yield full observer projections as Server-Sent Event frames."""
        version = -1
        while True:
            with self._changed:
                changed = self._version != version
                if not changed:
                    self._changed.wait(timeout=15)
                    changed = self._version != version
                version = self._version
                rows = self._sorted_rows()
            if changed:
                yield f"data: {json.dumps({'entries': rows})}\n\n"
            else:
                yield ": keepalive\n\n"

    def _sorted_rows(self) -> list[dict[str, Any]]:
        return sorted(
            (row.copy() for row in self._rows.values()),
            key=lambda row: (row["queued_at"], row["id"]),
        )


projection = DashboardProjection()
