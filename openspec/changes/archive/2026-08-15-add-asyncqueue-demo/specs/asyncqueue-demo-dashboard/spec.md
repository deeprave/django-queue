## ADDED Requirements

### Requirement: Provide a minimal dashboard project
The repository SHALL provide a runnable Django project under `demo_aq/` with
its own `pyproject.toml`. It SHALL require no database, auth, or admin setup,
and SHALL depend on no project package other than the parent `django_queue`
module. Its Django settings SHALL configure one Redis AsyncQueue alias named
`demo` using Redis port 16379.

#### Scenario: Start the dashboard configuration
- **WHEN** a developer starts the demo dashboard with its documented command
- **THEN** Django starts without database migration, auth, or admin
  configuration and resolves the `demo` Redis AsyncQueue

### Requirement: Display retained demo entries
The dashboard SHALL provide one page that displays the current retained entries
from the `demo` queue, including their identifier, state, queued and finished
times, and normal queue timeout.

#### Scenario: Render current queue state
- **WHEN** the dashboard page is requested while `demo` has retained entries
- **THEN** the page displays one row for each current retained entry

### Requirement: Observe and update dashboard entries
The dashboard SHALL create a queue observer for `demo` when its local
dashboard receives its first page or event-stream request. It SHALL use
lifecycle snapshots to add or update displayed entry rows without altering the
queue entry itself. A terminal lifecycle snapshot SHALL update its corresponding
displayed row with the terminal state and finished time.

#### Scenario: Receive a lifecycle update
- **WHEN** the demo queue observer receives a snapshot for an entry
- **THEN** the dashboard projection reflects that entry's latest observed state
  and the SSE page helper updates its corresponding row

#### Scenario: Receive a terminal lifecycle update
- **WHEN** the demo queue observer receives a terminal snapshot for a displayed
  entry
- **THEN** the dashboard retains that entry's row with its terminal state and
  finished time

### Requirement: Refresh observer state
The dashboard page SHALL provide a `Refresh` control that unsubscribes the
active queue observer, clears the local dashboard projection, and fully reloads
the page. The newly loaded dashboard SHALL subscribe to `demo` afresh.

#### Scenario: Refresh the dashboard observer
- **WHEN** a user activates `Refresh`
- **THEN** the current observer and local projection are discarded and the
  reloaded dashboard creates a new observer subscription for `demo`

### Requirement: Provide Compose startup
The repository SHALL provide `demo_aq/compose.yaml` that starts Redis and
exposes it on host port 16379. It SHALL make the dashboard available through
an opt-in Compose profile.

#### Scenario: Start the demo stack
- **WHEN** a developer starts the documented Compose configuration
- **THEN** Redis is reachable at port 16379 and a locally run dashboard can
  connect to its configured `demo` queue
