"""A small, plausible road-trip simulation for the event queue demo."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

_DIRECTIONS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
_MAX_SPEED = 150.0
_ORIGIN = (0.0, 0.0)
_ORIGIN_STOP_SECONDS = 15

type Coordinate = tuple[float, float]


@dataclass
class MovementGenerator:
    """Generate repeating road trips through randomly located towns."""

    identifier: int = 0
    speed: float = 40.0
    heading: float = 0.0
    x: float = 0.0
    y: float = 0.0
    _random: random.Random = field(default_factory=random.Random)
    _towns: list[Coordinate] = field(default_factory=list, init=False)
    _destinations: list[Coordinate] = field(default_factory=list, init=False)
    _road: list[Coordinate] = field(default_factory=list, init=False)
    _stopped_for: int = field(default=0, init=False)
    _resume_speed: float = field(default=40.0, init=False)
    _resume_pending: bool = field(default=False, init=False)
    _returning_home: bool = field(default=False, init=False)
    _light_after: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._begin_journey()

    def next(self) -> dict[str, float | int | str]:
        """Advance one second and return its JSON-compatible movement event."""
        if self._stopped_for:
            self._publish_stop()
        else:
            self._move()
        self.identifier += 1
        event = {
            "id": self.identifier,
            "speed": round(self.speed, 2),
            "direction": self._direction(),
            "distance": round(math.hypot(self.x, self.y), 2),
        }
        if self._resume_pending:
            self._resume()
        return event

    def _begin_journey(self) -> None:
        self._towns = self._generate_towns()
        self._destinations = [*self._towns, _ORIGIN]
        self._returning_home = False
        self._next_leg()
        self._schedule_light()

    def _generate_towns(self) -> list[Coordinate]:
        towns: list[Coordinate] = []
        town_count = self._random.randint(3, 5)
        while len(towns) < town_count:
            heading = math.radians(self._random.uniform(0, 360))
            distance = self._random.uniform(700, 1_700)
            town = (distance * math.sin(heading), distance * math.cos(heading))
            if all(math.dist(town, other) >= 500 for other in towns):
                towns.append(town)
        return towns

    def _next_leg(self) -> None:
        destination = self._destinations.pop(0)
        self._returning_home = not self._destinations
        self._road = self._build_road(destination)

    def _build_road(self, destination: Coordinate) -> list[Coordinate]:
        start = (self.x, self.y)
        length = math.dist(start, destination)
        segments = max(2, math.ceil(length / 300))
        dx = destination[0] - start[0]
        dy = destination[1] - start[1]
        perpendicular = (-dy / length, dx / length)
        offset_limit = min(140.0, length * 0.12)
        road: list[Coordinate] = []
        for step in range(1, segments + 1):
            ratio = step / segments
            offset = (
                0.0
                if step == segments
                else self._random.uniform(-offset_limit, offset_limit)
            )
            road.append(
                (
                    start[0] + dx * ratio + perpendicular[0] * offset,
                    start[1] + dy * ratio + perpendicular[1] * offset,
                )
            )
        return road

    def _move(self) -> None:
        self._light_after -= 1
        if self._light_after <= 0:
            self._stop_for(self._random.randint(5, 10), include_current=True)
            return

        target = self._road[0]
        self._steer_toward(target)
        self.speed = self._next_speed()
        radians = math.radians(self.heading)
        self.x += self.speed * math.sin(radians)
        self.y += self.speed * math.cos(radians)
        self._complete_waypoints()

    def _complete_waypoints(self) -> None:
        while self._road and math.dist((self.x, self.y), self._road[0]) <= max(
            25.0, self.speed * 0.75
        ):
            self.x, self.y = self._road.pop(0)
        if not self._road:
            if self._returning_home:
                self.x, self.y = _ORIGIN
                self._stop_for(_ORIGIN_STOP_SECONDS, include_current=True)
            else:
                self._next_leg()

    def _steer_toward(self, target: Coordinate) -> None:
        desired = math.degrees(math.atan2(target[0] - self.x, target[1] - self.y)) % 360
        difference = (desired - self.heading + 180) % 360 - 180
        limit = self._turn_limit()
        sway = self._random.uniform(-min(5.0, limit / 3), min(5.0, limit / 3))
        turn = max(-limit, min(limit, difference + sway))
        self.heading = (self.heading + turn) % 360

    def _stop_for(self, seconds: int, *, include_current: bool = False) -> None:
        self._resume_speed = self.speed
        self.speed = 0.0
        self._stopped_for = seconds - int(include_current)

    def _publish_stop(self) -> None:
        self.speed = 0.0
        if self._stopped_for > 1:
            self._stopped_for -= 1
            return
        self._stopped_for = 0
        self._resume_pending = True

    def _resume(self) -> None:
        self._resume_pending = False
        if self._returning_home and (self.x, self.y) == _ORIGIN:
            self.speed = self._resume_speed
            self._begin_journey()
        else:
            self.speed = self._resume_speed
            self._schedule_light()

    def _schedule_light(self) -> None:
        self._light_after = self._random.randint(20, 45)

    def _next_speed(self) -> float:
        if self.speed >= _MAX_SPEED:
            return _MAX_SPEED - self._change_limit()
        change = self._random.uniform(0, self._change_limit())
        direction = self._random.choice((-1, 1))
        return max(1.0, min(_MAX_SPEED, self.speed + direction * change))

    def _change_limit(self) -> float:
        return max(1.0, self.speed * 0.1)

    def _turn_limit(self) -> float:
        return (
            90.0
            if self.speed <= 5
            else 10.0
            if self.speed >= 100
            else 90.0 - (self.speed - 5) * 80 / 95
        )

    def _direction(self) -> str:
        return _DIRECTIONS[round(self.heading / 45) % len(_DIRECTIONS)]
