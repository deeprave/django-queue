## Why

The package now supports event queues, but there is no runnable example showing
their transient listener-driven delivery model. A focused visual demo will make
the distinction from retained async-queue work observable in practice.

## What Changes

- Add `demo_eq`, an independent minimal uv/Django project modelled on
  `demo_aq`.
- Configure an event queue and a management command that publishes one JSON
  movement event per second.
- Generate plausible movement telemetry: incrementing identifiers, smoothly
  varying speed, speed-constrained direction changes, and distance calculated
  from successive displacements.
- Register an event listener that streams received events to a browser plot of
  distance from origin over time.
- Document how to run the event producer, listener runtime, and dashboard.

## Capabilities

### New Capabilities

- `eventqueue-demo`: A runnable Django demonstration of event queue publishing,
  listener delivery, and live movement visualisation.

### Modified Capabilities

- None.

## Impact

Adds the `demo_eq` example project, its own uv metadata, lockfile, local
dependencies, and documentation. It does not change the public queue API or
delivery semantics.
