## Context

Queue-entry status is durable state, but applications currently have to poll it
to react to an entry completing. Django signals are useful for in-process
side-effects only: a `runqueues` process cannot use them to notify another
process in a horizontally scaled Django deployment.

Redis is already the shared transport for Redis-backed queues, while memory
queues are process-local by design. This change adds an intrinsic, best-effort
completion-notification facility for both backends. It is deliberately an
observation mechanism: it does not participate in, delay, or make durable the
queue-entry lifecycle.

## Goals / Non-Goals

**Goals:**

- Notify a locally registered callback when an identified queue entry reaches a
  terminal status.
- Avoid repeated status polling and additional Django configuration.
- Cover the race where an entry completes immediately before callback
  registration.
- Work across independently deployed `runqueues` and Django application
  processes for Redis-backed queues, and within the same process for
  memory-backed queues.

**Non-Goals:**

- Durable, at-least-once, ordered, or cross-process callback delivery.
- Django Channels, WebSockets, middleware-based dispatching, or a separate
  ASGI listener.
- A generic replacement for Django signals or a notification UI.
- Completion notifications for memory-backed queues outside their local
  process.

## Decisions

### Backends choose the live transport

The worker will publish a compact terminal-entry notification only after it has
successfully recorded the terminal entry. Redis-backed queues publish to an
intrinsic Redis channel; memory-backed queues submit to an intrinsic in-memory
broker. Each Django process that registers a listener runs one local, daemon
notification runtime that invokes callbacks registered in that same process.

Redis Pub/Sub broadcasts rather than load-balances each message, so the process
holding the matching in-memory callback can act on it. A Redis list or stream
consumer group was rejected because it would deliver a completion to one
arbitrary process and therefore miss the process that owns the callback. A
normal configured `MemoryQueue` and `AsyncQueueWorker` are not used for memory
notifications because they require an application-owned async lifespan; the
intrinsic broker is thread-backed and local instead.

### Registration starts the runtime lazily

The public listener-registration API will start the runtime idempotently on its
first use. It will not be started from `AppConfig.ready()`: doing so would also
create a background consumer for management commands, test setup, reloaders,
and pre-fork parent processes that never register a callback.

This is automatic for applications that use the API and needs no additional
`QUEUES` entry. An in-process daemon thread is used rather than an ASGI task
because normal Django request processes have no durable application-level async
lifespan to own such a task.

### Redis endpoints and memory state are local backend details

Notifications are published through the Redis client of the queue that owns
the entry. The runtime subscribes to the intrinsic completion channel for each
configured Redis-backed queue endpoint, deduplicating equivalent endpoints.
This supports applications with queues on more than one Redis deployment
without choosing an arbitrary default broker. Memory queues publish only to the
same process's broker and cannot notify a separate process.

### Backend-local TTL records close the registration race

Before publishing, the producer writes a compact notification record under an
entry-ID key with a fixed short retention period: a Redis key for Redis queues
or an expiring in-memory record for memory queues. Registration installs the
local callback and performs one lookup of the matching record. If it exists,
the runtime invokes the callback using that same notification payload.

The record is a bounded, per-entry cache rather than a notification queue. A
missed Redis Pub/Sub message remains possible after the TTL expires or while a
process is down; the in-memory record is lost when its process exits. This is
explicitly accepted best-effort behaviour. Retention and Redis key/channel
names remain internal constants.

### Callbacks are local observations

The callback receives an immutable completion notification containing the
entry ID, queue identity, terminal status, and completion timestamp. Callbacks
run in the notification runtime's listener thread, must return promptly, and
their exceptions are logged without stopping delivery to other callbacks.
The registration API returns an unsubscribe handle. Applications needing
durable work must record it themselves rather than rely on a callback.

## Risks / Trade-offs

- [A Pub/Sub message can be missed during downtime or connection loss] → The
  TTL record provides a one-time catch-up check at registration; callers retain
  responsibility for durable workflows.
- [A slow callback delays other local callbacks] → Document the synchronous
  callback contract and isolate/log callback failures; applications can hand
  work off to their own executor or queue.
- [Redis endpoint discovery may encounter invalid or unavailable queue
  configuration] → Reuse configured queue construction and log notification
  runtime failures without changing a completed entry's outcome.
- [Multiple web processes receive every message] → Each process only invokes
  callbacks registered in that process; delivery is intentionally broadcast.
- [A memory completion has no shared transport] → Deliver it only through the
  intrinsic broker in the producing process and document that boundary.

## Migration Plan

No data migration or setting is required. Deploying the feature is additive:
existing workers continue to record outcomes, then begin best-effort publish
attempts. Removing the feature only leaves expiring Redis keys or local memory
records and has no effect on stored entries.

## Open Questions

None for the initial best-effort implementation. A future durable notification
or at-least-once queue strategy will need acknowledgement, retry, and consumer
ownership semantics and is intentionally deferred.
