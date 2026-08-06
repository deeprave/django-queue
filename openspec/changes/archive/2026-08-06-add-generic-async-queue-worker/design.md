## Context

`django-queue` currently exposes synchronous raw-value queues. It has no stable
identity for a queued value, no retained lifecycle record, and no common worker
runtime. Redis-backed applications can have multiple independent Django or
worker processes using the same Redis server, so queue timestamps must be
aligned to Redis rather than use an uncorrected process wall clock.

This change establishes the generic foundation needed before a separate Django
6 task-backend change can translate task payloads and results into queue entries.

## Goals / Non-Goals

**Goals:**

- Provide a safe, inspectable, JSON-serialisable entry model for generic queues.
- Provide an async worker that is explicitly startable, cancellable, and usable
  outside Django request or app-startup lifecycle hooks.
- Preserve the existing raw queue API while adding entry-oriented operations.
- Support independent Redis clients with Redis-aligned lifecycle timestamps.
- Keep public interfaces compatible with a future claim/acknowledge/lease
  implementation.

**Non-Goals:**

- At-least-once delivery, leases, retries, or recovery of work interrupted by a
  worker crash.
- A Django task backend, task-result adapter, or worker management command.
- Pickle or arbitrary-object serialisation.
- Parallel dispatch, rate limiting, scheduling, or priority redesign.

## Decisions

### Entry model and serialisation

Introduce a frozen, slotted `QueueEntry` value object with `to_dict()` and
`from_dict()` conversion. Its durable form is a JSON object containing a UUIDv7
`id`, queue name, lifecycle status/timestamps, payload, result, and structured
error. Public enqueue accepts any JSON-serialisable value and returns the UUID;
entry lookup returns a `QueueEntry`.

The queue owns the ID and all lifecycle timestamps. UUIDv7 is generated with
`uuid.uuid7()` and is an identity/orderability aid only; it is not the timestamp
authority.

Frozen entries make dispatch transitions explicit via replacement values and
avoid accidental mutation while a handler is running. JSON rather than pickle
is required because shared Redis is a cross-process trust boundary. Pickle is
not safe to deserialise from queue data.

Alternative considered: raw dictionaries throughout. This is simpler but makes
required fields and status transitions easier to corrupt. Alternative considered:
pickle payloads. This permits arbitrary objects but permits code execution when
data is deserialised.

### Entry storage and queue interface

Extend `BaseQueue` with entry-oriented methods: `enqueue(payload)`,
`get_entry(entry_id)`, non-blocking `dequeue_entry()`, and lifecycle update
operations for running, success, failure, and cancellation. `QueueEntryStatus`
is a string enum: entries hold an enum member in memory and persist its string
value. Its `next_state()` method defines the allowed transitions: `queued` to
`running`; `running` to `succeeded`, `failed`, or `cancelled`; and no transition
from a terminal status. Implementations retain an entry record addressable by
ID separately from the pending queue structure.

Existing `add()`, `get()`, `poll()`, `peek()`, and `size()` remain available
during this change for backwards compatibility. New consumers use only the
entry-oriented API.

Memory queues keep entries in process. Redis queues store the pending order and
per-entry JSON records under namespaced keys. Removing an item from the pending
structure is the first-version claim operation and is deliberately best effort.

Alternative considered: add claim/acknowledge now. This would delay the generic
worker for reliability machinery that the first milestone does not promise.

### Redis-aligned time

Redis queue instances synchronously obtain an initial Redis `TIME` reading,
compare it to local UTC, and cache the resulting Redis-to-local offset. They
then calculate queue time as local UTC plus that offset. Once the cache is 600
seconds old, one background refresh obtains a new Redis time reading while
callers continue using the existing offset; no queue operation waits for that
refresh. The cache is per process and protected from concurrent refreshes.

Redis time must be available for initial calibration and for subsequent refresh
health. A refresh failure or a Redis/local UTC difference greater than the fixed
180-second limit is logged, retains the last good offset, and is retried at the
next refresh interval. Initial calibration fails clearly for either condition,
because no Redis-aligned offset is yet available. Memory queues use UTC system
time as their documented fallback.

Alternative considered: query Redis for every timestamp. This is more exact but
adds a network round trip to every lifecycle transition. Alternative considered:
use local `datetime.now()`. Independent workers can drift and wall clocks can
jump.

### Asynchronous worker lifecycle

Add an `AsyncQueueWorker` that accepts queues and an async handler registration
for each queue name. It repeatedly performs non-blocking dequeue work, marks an
entry running, awaits its handler, then records a JSON-serialisable success
result or structured failure. If no item is available, it awaits a configurable
idle delay.

Backends remain synchronous in this change. The worker uses `asyncio.to_thread`
for queue I/O so Redis and memory operations do not block the event loop. It
dispatches one entry at a time per worker. On cancellation it stops taking new
entries, allows an active handler a configurable grace period, then cancels it
if necessary and records the entry as `cancelled`. Cancellation propagates to
the caller; a `finally` block sets the worker's `running` state to `False`.

The public structured failure is limited to an exception class and safe message;
the worker logs the full exception traceback for diagnosis. Tracebacks MUST NOT
be exposed through `QueueEntry`. A terminal-record persistence failure is logged
and does not stop the worker. If the entry can still be read as `running`, the
worker makes one best-effort attempt to record a safe `QueuePersistenceError`
failure outcome; if that also fails, the retained entry is left unchanged.
The worker then raises `QueuePersistenceError` so its supervisor can restart it
with backoff instead of dispatching further work without durable outcomes.

Alternative considered: a background task created by a start method that returns
immediately. That pattern loses ownership of the real worker task and makes
cancellation unreliable. Alternative considered: converting every backend API
to async. That is a larger incompatible redesign and is unnecessary while I/O
can be isolated in worker threads.

## Risks / Trade-offs

- [Worker crash after dequeue loses an entry] → Explicitly document best-effort
  semantics and retain an API shape that can gain claims, acknowledgements, and
  leases later.
- [Serialised payload is invalid] → Validate JSON serialisation before enqueue
  and reject invalid values without adding an entry.
- [Entry records grow indefinitely] → Terminal-entry expiry is defined by the
  follow-up retention change. This foundational API does not expose a
  retain-forever option.
- [Redis time is unavailable or has excessive drift] → Fail initial calibration
  clearly; after a valid calibration, log the failed background refresh, retain
  the last good offset, and retry after the next 600-second interval.
- [A synchronous backend call is slow] → Run all worker queue I/O through
  `asyncio.to_thread`; later backends can offer native async operations.

## Migration Plan

1. Raise the supported Python baseline to 3.14 and add the entry/clock types.
2. Add entry-oriented operations to `BaseQueue`, then implement them for memory
   and Redis queues with focused tests.
3. Add `AsyncQueueWorker` and tests for normal dispatch, idle behavior, handler
   failure, and cancellation.
4. Release the API without switching `django-redis-tasks`; its migration is a
   separate change after this API is validated.

Rollback consists of reverting the release; no existing raw-queue API is
removed. Queues created by this release use namespaced keys so they can be
expired or deleted by an operator if necessary.

## Open Questions

- Define the worker-ID format and multi-worker observability model needed by the
  later Django task-result adapter.

## Implementation review

The implementation satisfies the `queue-entries` scenarios with immutable
UUIDv7 records, JSON validation, lifecycle transitions, and Redis-aligned
timestamps. It satisfies the `async-queue-workers` scenarios with sequential
registered-handler dispatch, threaded synchronous queue I/O, safe failure
records, and cancellation that stops new dispatches while allowing an active
handler its grace period.

Worker IDs, queue-depth and latency metrics, structured lifecycle logging, and
multi-worker observability remain deferred to the `add-worker-observability`
change. Terminal-entry expiry remains deferred to `add-entry-retention`; this
change deliberately does not expose a retain-forever setting.
