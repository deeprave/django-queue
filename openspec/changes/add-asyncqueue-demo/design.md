## Context

The repository has AsyncQueue, Redis-backed task entries, worker lifecycle
snapshots, and process-local `queue_observer` subscriptions, but no small
end-to-end application showing how they fit together. The example must be
independent of a database and the standard auth/admin stack. It also needs a
separate producer/worker process so task creation is not confused with the
dashboard process.

## Goals / Non-Goals

**Goals:**

- Provide a runnable `demo_aq/` Django project with only the settings needed
  for the dashboard, templates/static assets, and `django_queue`.
- Exercise the actual Redis AsyncQueue named `demo` from two processes.
- Show retained entries on initial page load and mutate displayed rows in
  response to a queue observer's snapshots.
- Make observer unsubscription and a fresh subscription visible and manually
  controllable from the page.
- Generate self-contained sample tasks from random manual-page search output.

**Non-Goals:**

- Database models, migrations, authentication, admin, Channels, WebSockets,
  Celery, or production dashboard design.
- Error, retry, cancellation, or broker-outage simulation.
- Implementing retained-entry expiry or pruning; that belongs to the separate
  `add-entry-retention` change.
- Packaging the example as an independently published distribution.

## Decisions

### One isolated Django project, two process roles

`demo_aq/` owns its own `pyproject.toml`, settings, URL configuration, and
Compose configuration. It consumes the parent checkout as its only project
dependency. The dashboard is the normal Django server; the second role is a
management command run independently. This makes the Redis transport and
process boundary visible without a second project or database.

### Minimal Django configuration

`INSTALLED_APPS` contains only Django's static-file support, `django_queue`,
and the demo application needed to render the page and expose its management
command. No migration-dependent Django components are configured. The sole
`QUEUES` alias is `demo`, backed by Redis at port 16379.

### Observer-driven UI with a small browser helper

The dashboard installs one queue observer when it starts. Its bootstrap
snapshots provide the initial list, and observer callbacks update a
process-local dashboard projection; the dashboard does not independently call
`list_entries()`. A small inline browser helper receives Server-Sent Events
from that projection and inserts or updates table rows, avoiding static-asset
setup and repeated polling. This avoids adding Channels or WebSockets while
still demonstrating live observer updates. The `Refresh`
control explicitly unsubscribes the current subscription, clears the local
projection, and fully reloads the page; the new dashboard instance then
subscribes afresh.
Terminal snapshots update their rows with the final state and finished time,
so the dashboard remains a retained-entry view while Redis retains them.

Retained-entry enumeration is an internal `AsyncQueue` bootstrap capability,
not a public `BaseQueue` method. `queue_observer` is its sole consumer. This
keeps future `EventQueue` implementations free of task-entry retention and
lifecycle assumptions.

`man -k .` is run by the publisher command to source random messages. The
configured worker and handler are then started separately with Django's normal
`runqueues` command.
Before publishing, the command clears only the `demo` queue's retained entry
records, pending IDs, and claims. Redis Pub/Sub is transient and needs no
cleanup. The demo assumes a single publisher process during reset. An
already-open dashboard clears its local projection on the user's next Refresh.

### Compose as the runnable boundary

`compose.yaml` exposes Redis on host port 16379 by default. The Django
dashboard remains available through an opt-in Compose profile, but normally
runs from the host checkout so changes do not require image rebuilds. The
publisher command and `runqueues` worker are explicit commands rather than
automatic Compose services, keeping the two roles easy to inspect and restart
independently.

## Risks / Trade-offs

- [The browser helper uses one Server-Sent Events connection rather than a
  WebSocket] → It keeps dependencies and setup minimal; the queue observer
  remains the feature being exercised.
- [The process-local projection disappears when the dashboard restarts] → The
  retained-entry load rebuilds the page state at subscription time.
- [A full observer delivery buffer can drop updates] → This is visible as
  best-effort behavior; a normal page refresh rebuilds from retained entries.
- [Host systems may lack a manual-page index] → The publisher reports a clear
  command error when `man -k .` cannot provide sample messages.
