## Context

See proposal.md for motivation. `demo_aq` demonstrates retained async-queue
lifecycle records; the new `demo_eq` must instead make the event queue's
transient listener delivery model tangible. It must remain a deliberately
small Django example rather than a production telemetry system.

## Goals / Non-Goals

**Goals:**

- Demonstrate a JSON `EventQueue`, continuous event publication, registered
  listener delivery, and a listener-owned browser projection.
- Make generated motion feel plausible while retaining an explainable and
  deterministic payload contract.
- Reuse the project's existing demo/Redis development conventions where useful.

**Non-Goals:**

- Persisting movement history, replaying missed events, or treating the event
  queue as a queryable record store.
- Providing a reusable geospatial simulation or a production dashboard.
- Adding WebSockets, a front-end build step, or broad demo tests.

## Decisions

### Use a stateful one-event-per-second producer

The management command owns one movement generator and invokes one asyncio run
for its continuous publishing loop. It keeps the next identifier, speed,
continuous heading, and Cartesian position in memory. Every second it emits a
plain JSON-compatible object:

```json
{
  "id": 42,
  "speed": 73.2,
  "direction": "NE",
  "distance": 281.4
}
```

Speed changes by a randomly selected signed amount no greater than ten percent
of the previous speed (with a one-unit minimum around zero), clamped to
0–150. Increase and decrease are equally likely except when a boundary forces
the next change inward. This produces smooth acceleration/deceleration while
avoiding a zero-speed deadlock.

Alternative: independently randomise speed on every event. This is simpler but
visibly implausible and does not demonstrate a meaningful stream.

### Keep a continuous heading and publish an eight-point compass label

The generator represents heading in degrees, alters it around the current
heading, and converts it to the nearest of the eight requested compass labels
for the payload. Its maximum turn decreases linearly from 90 degrees at speed
5 or lower to 10 degrees at speed 100 or higher. The previous heading is used
as the reference, so an opposite turn cannot occur.

Alternative: choose directly among eight compass labels. That makes the
high-speed limit impossible to express cleanly because a single label step is
45 degrees.

### Derive distance from position

Each one-second movement advances an in-memory `(x, y)` position by the speed
vector for the heading. The payload's `distance` is `hypot(x, y)`, rather than
an accumulated travelled distance. This preserves the stated distance-from-
origin meaning, including when movement returns toward the origin.

### Simulate repeating road trips through random towns

Each journey chooses three to five town locations around the origin and visits
them in sequence before returning home. A leg is represented by several
intermediate, laterally offset road waypoints, rather than a straight line to
the town. The vehicle steers gradually toward its next waypoint with a small
random sway, so its heading continues to obey the speed-sensitive turn limit
while its route visibly meanders.

While travelling, a randomly scheduled traffic light stops the vehicle for
five to ten seconds. Reaching the origin stops it for fifteen seconds, then a
new set of town locations begins another journey. Zero-speed events are still
published once per second throughout each stop, creating natural plateaus in
the distance plot. Normal moving speed remains smooth; entering or leaving a
traffic-control stop is the intentional exception to the usual speed-change
bound.

Alternative: model a real road network. That would require map data and would
obscure the event-queue demonstration. Generated waypoint roads provide the
same visual story without a new dependency or persisted data.

### Use a listener-owned rolling projection, SSE, and inline SVG

The registered event listener validates the event shape, de-duplicates by the
monotonically increasing identifier, and maintains the most recent bounded
distance projection in the dashboard process. A server-sent event endpoint
emits an update when that projection changes. Small inline JavaScript consumes
the stream and redraws a compact inline SVG immediately: distance on the Y
axis and event-receipt time on the X axis. A fixed recent-time window scrolls
as events arrive. Each segment is green for an increase in speed, red for a
decrease, and blue for no change. The queue is never read by the dashboard to
reconstruct state.

Alternative: query the event queue from the dashboard. Event records are
transient and the example must demonstrate listener delivery, not misuse the
queue as persistence. Canvas would make axes and labels manual; D3 is
unnecessary for a small one-Hz series. WebSockets would work, but SSE matches
the one-way listener-to-browser flow and avoids unrelated connection management.

### Keep the demo separate from `demo_aq`

`demo_eq` will mirror the small-project shape and local documentation of
`demo_aq`, but use event-queue configuration, listener registration, and the
event runtime. This makes the contrast between async lifecycle work and
transient event delivery explicit. It will be an independent uv project with
its own `pyproject.toml`, lockfile, virtual environment, and an editable source
reference to the parent checkout, rather than adding demo dependencies to the
root project.

## Risks / Trade-offs

- [The in-memory projection resets when the dashboard process restarts] → Make
  that transient behavior explicit; new events repopulate it immediately.
- [Event delivery can be delayed or duplicated during recovery] → Keep the
  projection idempotent by its increasing event identifier and present it as a
  demo, not as authoritative storage.
- [Discrete direction labels hide small heading changes] → Use continuous
  headings internally and show the requested compass label as telemetry.
- [A disconnected browser misses streamed updates] → Send the current rolling
  projection when a new SSE connection opens, then stream subsequent changes.
- [The two demos drift in setup conventions] → Reuse `demo_aq`'s project,
  Compose, and local-development patterns where they fit the event demo.

## Migration Plan

1. Add the standalone demo project and its run instructions.
2. Start Redis and the dashboard locally, then run the producer command.
3. Roll back by removing `demo_eq`; no application data or library migration is
   involved.
