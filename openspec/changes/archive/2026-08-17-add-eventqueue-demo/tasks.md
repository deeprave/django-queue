## 1. Demo application and event stream

- [x] 1.1 Create `demo_eq` as an independent minimal uv project, following
  `demo_aq`'s project and local-development patterns with its own metadata,
  lockfile, editable local package source, Redis JSON event queue, and
  event-listener registration.
- [x] 1.2 Implement the stateful movement generator and management command that
  publishes one valid JSON event per second from one asyncio run.
- [x] 1.3 Implement smooth speed, continuous heading, compass conversion, and
  distance-from-origin calculations for generated events.
- [x] 1.4 Make the generator simulate repeating road trips through randomly
  located towns, offset road waypoints, traffic-light stops, and an origin
  stop before the next journey.

## 2. Listener projection and dashboard

- [x] 2.1 Implement the listener-owned, identifier-deduplicated rolling
  distance projection and an SSE endpoint that supplies its current snapshot
  and later updates.
- [x] 2.2 Build the dashboard template with inline JavaScript and SVG to
  render axes, distance over time, the scrolling recent-time window,
  colour-coded speed transitions, and current telemetry from listener state.
- [x] 2.4 Make the dashboard layout fluid across the available viewport width.
- [x] 2.5 Show and automatically scroll a five-event feed beneath the graph.
- [x] 2.3 Ensure the dashboard starts listener delivery through the configured
  event runtime and never reads events directly from the queue.

## 3. Documentation and manual validation

- [x] 3.1 Document setup, Redis, dashboard, event producer, transient listener
  behavior, and the distinction from `demo_aq`.
- [x] 3.2 Manually run the dashboard and producer together to verify one-Hz
  event delivery, plausible motion, and live graphical updates; iterate the
  demo directly rather than adding automated demo tests.
- [x] 3.3 Manually verify the fluid dashboard and road-trip behaviour,
  including traffic-light and origin stops.
