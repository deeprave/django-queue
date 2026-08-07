## Why

Applications need an event-driven way to react when queued work reaches a
terminal outcome. Polling queue-entry status is undesirable, while Django
signals are process-local and cannot notify another horizontally scaled process.

## What Changes

- Publish a best-effort completion notification after any identified queue entry
  reaches a terminal state.
- Provide an intrinsic notification runtime with process-local callbacks keyed
  by entry ID: Redis Pub/Sub for Redis queues and an in-memory broker for
  memory queues.
- Retain a short-lived backend-local completion record to catch completion
  immediately before a callback is registered.
- Start the local notification runtime automatically when an application first
  registers a completion listener, without adding a `QUEUES` definition or a
  Django Channels dependency.

## Capabilities

### New Capabilities

- `completion-notifications`: Deliver best-effort terminal-entry notifications
  to locally registered Django-process callbacks.

### Modified Capabilities

- `async-queue-workers`: Publish a completion notification after a worker
  records an entry's terminal outcome.

## Impact

Adds Redis and in-memory notification publishing, a Django-process listener
runtime, a public completion-listener API, and documentation. Completion
delivery remains best-effort and does not alter queue-entry lifecycle or
delivery guarantees.
