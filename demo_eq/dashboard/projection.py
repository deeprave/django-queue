"""Listener-owned, process-local movement projection for the dashboard."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Iterator, Mapping
from typing import Any

_DIRECTIONS = frozenset({"N", "S", "E", "W", "NE", "NW", "SE", "SW"})


class MovementProjection:
    """Maintain a bounded, JSON-ready history of delivered movement events."""

    max_points = 90

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._points: deque[dict[str, Any]] = deque()
        self._seen_ids: set[int] = set()
        self._version = 0

    def update(self, payload: object) -> bool:
        """Record one valid event and report whether it can be consumed."""
        point = self._point(payload)
        if point is None:
            return False
        with self._changed:
            if point["id"] in self._seen_ids:
                return True
            self._points.append(point)
            self._seen_ids.add(point["id"])
            while len(self._points) > self.max_points:
                self._seen_ids.discard(self._points.popleft()["id"])
            self._version += 1
            self._changed.notify_all()
        return True

    def events(self) -> Iterator[str]:
        """Yield complete projection snapshots as Server-Sent Event frames."""
        version = -1
        while True:
            with self._changed:
                changed = self._version != version
                if not changed:
                    self._changed.wait(timeout=15)
                    changed = self._version != version
                version = self._version
                snapshot = self._snapshot()
            if changed:
                yield f"data: {json.dumps(snapshot)}\n\n"
            else:
                yield ": keepalive\n\n"

    def _point(self, payload: object) -> dict[str, Any] | None:
        if not isinstance(payload, Mapping):
            return None
        identifier = payload.get("id")
        speed = payload.get("speed")
        direction = payload.get("direction")
        distance = payload.get("distance")
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or isinstance(speed, bool)
            or not isinstance(speed, int | float)
            or isinstance(distance, bool)
            or not isinstance(distance, int | float)
            or not isinstance(direction, str)
            or direction not in _DIRECTIONS
        ):
            return None
        return {
            "id": identifier,
            "speed": speed,
            "direction": direction,
            "distance": distance,
            "received_at": time.time(),
        }

    def _snapshot(self) -> dict[str, Any]:
        points = [point.copy() for point in self._points]
        return {"points": points, "latest": points[-1] if points else None}


projection = MovementProjection()
