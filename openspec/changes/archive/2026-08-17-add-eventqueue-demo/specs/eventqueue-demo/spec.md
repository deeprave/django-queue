## Purpose

Provide a runnable Django event-queue demonstration that makes transient event
publication, listener delivery, and live movement progress visible to users.

## ADDED Requirements

### Requirement: Provide an event-queue demo application
The repository SHALL provide `demo_eq`, a minimal Django application that
configures a JSON event queue and includes a dashboard for the demonstration.
It SHALL be an independent uv project with its own dependency metadata and
lockfile, using the local package checkout as an editable dependency.

#### Scenario: Start the dashboard
- **WHEN** a developer starts the demo Django application and opens the dashboard
- **THEN** the dashboard identifies the event queue and is ready to display
  received movement events

### Requirement: Publish plausible movement events once per second
The demo producer SHALL publish one JSON event per second with an incrementing
`id`, numeric `speed` from 0 through 150, compass `direction` from `N`, `S`,
`E`, `W`, `NE`, `NW`, `SE`, or `SW`, and numeric `distance` from the origin.
While moving, successive events SHALL vary speed by no more than ten percent
of the previous speed, apart from the minimum movement needed to leave zero or
a boundary. Entering or leaving a simulated traffic-control stop MAY set speed
to or from zero. In the absence of a boundary, moving-speed increases and
decreases SHALL be selected with equal probability.

#### Scenario: Generate a sequence of events
- **WHEN** the producer runs for several seconds
- **THEN** it publishes one valid JSON movement event per second with strictly
  increasing identifiers and smoothly changing speed

#### Scenario: Turn while moving quickly
- **WHEN** a generated movement event has a higher speed than its predecessor
- **THEN** its direction varies less than the direction of an equivalent
  low-speed movement and never reverses by more than ninety degrees

### Requirement: Calculate distance from successive movements
The demo producer SHALL calculate each event's distance as the Euclidean
distance from the origin after applying the preceding one-second movement to
its current position.

#### Scenario: Move away from the origin
- **WHEN** successive generated movements carry the object away from its origin
- **THEN** later events report a greater calculated distance than the initial
  position

### Requirement: Simulate repeating journeys
The demo producer SHALL generate a small random set of town locations for each
journey, travel to them through offset intermediate road waypoints, return to
the origin, and then begin a new journey after a fifteen-second stop. It SHALL
occasionally simulate traffic-light stops lasting five through ten seconds,
while continuing to publish zero-speed events once per second.

#### Scenario: Visit towns before returning home
- **WHEN** a journey begins
- **THEN** the producer travels through its generated town locations before
  reaching and stopping at the origin

#### Scenario: Stop at a traffic light
- **WHEN** the producer reaches a randomly scheduled traffic light
- **THEN** it publishes zero-speed events for five through ten seconds before
  resuming the journey

### Requirement: Visualise received event progress
An event listener SHALL receive movement events and update the dashboard's
current projection. The dashboard SHALL receive projection updates through a
server-sent event stream and render a graphical distance-from-origin plot
without reading event records directly from the queue.

#### Scenario: Receive a movement event
- **WHEN** the event listener receives a valid movement event
- **THEN** the dashboard projection includes its latest identifier, speed,
  direction, distance, and plotting time

#### Scenario: Plot an event sequence
- **WHEN** the dashboard receives successive projection updates
- **THEN** it plots distance from origin on the vertical axis against time on
  the horizontal axis using an inline SVG graph

#### Scenario: Show a speed transition
- **WHEN** the dashboard connects two plotted events
- **THEN** it colours the segment green when the current speed is greater than
  the previous speed, red when it is less, and blue when it is equal

### Requirement: Show recent received events
The dashboard SHALL display the five most recent received movement events below
the graph, including their identifier, speed, direction, and distance, with the
newest event first. The event feed SHALL remain scrolled to its newest event
after each update.

#### Scenario: Receive another event
- **WHEN** the listener projection receives a new valid event
- **THEN** the dashboard updates its five-event feed and keeps its newest row
  visible

#### Scenario: Advance the visible time window
- **WHEN** newly received events exceed the dashboard's visible time range
- **THEN** the plot scrolls forward while retaining a fixed-duration recent
  time window

#### Scenario: Refresh the dashboard
- **WHEN** a browser refreshes the dashboard while the producer is running
- **THEN** it renders the listener's currently retained distance projection and
  subsequently redraws as server-sent event updates arrive

### Requirement: Document the event demonstration workflow
The demo documentation SHALL distinguish the event producer from listener
delivery and explain the commands and services required to run the dashboard.

#### Scenario: Follow the demo instructions
- **WHEN** a developer follows the documented setup and startup steps
- **THEN** they can publish movement events and observe their progress on the
  dashboard
