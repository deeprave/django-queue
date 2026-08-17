# Event Queue Demo

`demo_eq` is a deliberately stripped-down Django application that shows how a
`django_queue` event queue works in practice. It has no database,
authentication, or admin application: Redis is the only service it needs.

Unlike `demo_aq`, this demo does not retain task lifecycle records or run
handlers through `runqueues`. Its `demo` management command continuously
publishes transient JSON movement events. A registered listener in the
dashboard process receives each event, keeps a small in-memory projection, and
streams that projection to the browser with Server-Sent Events.

The `demo` command is only a visual fixture generator. It is not an example of
how production applications should model telemetry or own long-running
publishers.

## Run it

From `demo_eq`, create the independent uv environment:

```sh
uv sync
```

Start Redis:

```sh
docker compose up -d
```

Run the dashboard from this checkout so source changes reload without a
container rebuild:

```sh
uv run python manage.py runserver
```

Open <http://127.0.0.1:8000/>. Its first request starts Django Queue's local
event runtime and the Redis event listener. In another terminal, run the
continuous producer:

```sh
uv run python manage.py demo
```

Redis is exposed only at `127.0.0.1:16380`. The optional containerised
dashboard is also localhost-only:

```sh
docker compose --profile dashboard up -d
```

## What the demo shows

The producer emits one JSON object each second:

```json
{
  "id": 42,
  "speed": 73.2,
  "direction": "NE",
  "distance": 281.4
}
```

Each journey picks several town locations, follows gently offset road
waypoints between them, then returns to the origin and begins a new route after
a fifteen-second pause. It also makes occasional five-to-ten-second
traffic-light stops. Speed moves up or down by at most ten percent between
moving events, with equal probability away from the boundaries; deliberate
stops emit zero speed. Direction is generated from a continuous heading: slow
movement can turn further than fast movement, but never reverses by more than
ninety degrees. `distance` is calculated from the simulated position's
Euclidean distance to the origin.

The dashboard does not query Redis for events. Its listener updates a small
process-local projection and sends full snapshots over SSE. Inline JavaScript
renders the latest sixty seconds in SVG: distance is on the Y axis, time is on
the X axis, and each line segment is green when the object accelerated, red
when it decelerated, and blue when its speed stayed the same.

Because events are transient, restarting the dashboard clears its local graph.
The producer continues publishing and newly delivered events immediately build
a new projection.
