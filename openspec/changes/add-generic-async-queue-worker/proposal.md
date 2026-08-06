## Why

`django-queue` can store values in named queues but has no durable item identity,
lifecycle record, or generic asynchronous worker. `django-redis-tasks` has
therefore grown its own untracked polling loop instead of sharing a reusable
worker foundation. Django 6 supplies the task API but intentionally leaves
worker execution to external infrastructure, making this foundation necessary
now.

## What Changes

- Add a durable, JSON-serialisable queue-entry model with queue-generated UUIDv7
  identifiers and lifecycle timestamps.
- Extend the generic queue interface to enqueue and retrieve entries by ID, and
  to record dispatch outcomes.
- Add a cancellable asynchronous worker that repeatedly dispatches queue entries
  using registered asynchronous handlers.
- Add Redis-aligned time for Redis queues, with a monotonic-clock cache refreshed
  no more frequently than every 600 seconds.
- Add best-effort Redis and memory implementations of the new entry and worker
  APIs.
- **BREAKING** Require Python 3.14 or later for standard-library UUIDv7 support.

## Capabilities

### New Capabilities

- `queue-entries`: Create, store, retrieve, and transition identified generic
  queue entries using a safe JSON-serialisable format.
- `async-queue-workers`: Run registered asynchronous handlers over queue entries
  until cancellation, with defined best-effort dispatch semantics.

### Modified Capabilities

None.

## Impact

- Affected code: `django_queue.backends.base`, the memory and Redis backends,
  connection handling, and new worker/entry modules.
- Affected public API: new entry-oriented queue methods and worker API; existing
  raw queue operations remain available during this change.
- Dependencies: Python baseline changes from 3.11 to 3.14; no new runtime
  dependencies are required.
- Downstream system: `django-redis-tasks` can later replace its local run loop
  with this API and implement the Django 6 task backend contract.
