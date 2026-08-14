## Why

Applications need an event-driven way to monitor task queues without polling
entry state. Django signals are process-local and cannot notify a dashboard or
integration running in another horizontally scaled Django process.

## What Changes

- Publish best-effort, ordered lifecycle notifications after an identified queue
  entry changes state, including completion notifications for terminal states.
- Provide passive queue-scoped observers, optionally filtered to one entry ID,
  using Redis Pub/Sub for Redis queues and an in-memory broker for memory queues.
- Provide the current retained entry snapshots when an observer registers, then
  deliver later snapshots so dashboards discover entries created elsewhere.
- Start the local observer runtime lazily on first registration, without adding
  a `QUEUES` definition or a Django Channels dependency.

## Capabilities

### New Capabilities

- `completion-notifications`: Deliver best-effort ordered task-queue lifecycle
  snapshots to locally registered Django-process observers.

### Modified Capabilities

- `async-queue-workers`: Publish lifecycle notifications after a worker records
  an entry state transition.
- `async-queue-backends`: List retained queue-entry snapshots for observer
  bootstrap.

## Impact

Adds Redis and in-memory observer publishing, a Django-process observer runtime,
a public `queue_observer` API, retained-entry listing, and documentation.
Observation remains best-effort and does not alter task queue lifecycle or
delivery guarantees.
