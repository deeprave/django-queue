# AsyncQueue dashboard display design

## Purpose

Display retained `demo` queue entries using `queue_observer` snapshots as the
sole data source. The dashboard deliberately does not enumerate Redis itself.

## Data flow

When the dashboard service starts, it subscribes to `queue_observer("demo",
callback)`. The observer bootstrap snapshots and later lifecycle snapshots
update one process-local projection keyed by entry ID. An SSE endpoint exposes
the projection to the browser.

The page starts as a table shell. An inline `EventSource` script receives an
initial snapshot, then receives a new snapshot only when an observer callback
changes the projection. It inserts rows for new IDs and updates the cells of
existing rows. Keeping the script in the template avoids a static-asset setup.
There are no WebSockets or repeated polling requests.

## Table

Each row presents: ID, state, message, queued timestamp, finished timestamp
when present, and normal queue timeout. Timestamps use a compact, non-wrapping
`YYYY-MM-DDTHH:mm:ss` representation.

The template loads Pico CSS from its CDN and keeps custom styling inline. Pico
automatically follows the browser's light/dark preference. Local rules use the
inverse primary palette for the header, make the table fluid across the page,
truncate messages to one line with an ellipsis, and format IDs in a truncated
monospace style. There are no local static assets.

State uses foreground colour only: queued is blue, running is yellow,
succeeded is green, and failed is red. The state value is rendered in a span
inside its ordinary table cell so table-column alignment remains intact.

Terminal snapshots remain represented in the projection and table while Redis
retains their entries. The Refresh control unsubscribes the observer, clears
the projection, and reloads the page so a fresh observer subscribes.

## Boundaries

The projection owns presentation state only. It never changes queue entries.
Redis remains the durable queue backend; the dashboard observer is the only
component that supplies data to its projection.

## Clean demo runs

Before publishing its random batch, `manage.py demo` clears only the `demo`
queue's retained entry records, pending IDs, and claims. Redis Pub/Sub needs no
cleanup because it retains no messages. The demo assumes one publisher at a
time, so reset does not coordinate with another active handler. An
already-open dashboard retains its local rows until the user presses Refresh,
which resubscribes and rebuilds from the clean queue state.

## Demo timing and outcomes

The command defaults to 6–16 entries. Each payload contains a two-item list of
timestamped transitions: queued to running after a random 10–30 seconds, then
running to its terminal state after a random 30–60 seconds. Its demo-only
worker is configured in Django's `QUEUES` settings and run with `manage.py
runqueues`. It rotates not-yet-due queued entries to the back of its pending
list. When a queued transition is due, it marks that entry running and spawns
its handler, then immediately continues to process the next record. Each
handler waits independently for its terminal transition and reports success or
failure through the normal worker path. Several rows can therefore progress
through their lifecycle together. The publisher command selects one random
failure in a 6–9 entry batch and two random failures in a 10–16 entry batch.
When `runqueues` stops, the demo worker stops its active handler tasks and
records their entries as failed with a termination error.
