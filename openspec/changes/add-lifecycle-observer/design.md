## Context

Queue-entry state is durable, but applications currently have to poll it to
build a task dashboard or integration view. Django signals are useful for
in-process side-effects only: a `runqueues` process cannot use them to notify
another horizontally scaled Django process.

Redis is already the shared transport for Redis-backed queues, while memory
queues are process-local by design. This change adds an intrinsic, best-effort
completion-notification facility for both backends. It is deliberately an
observation mechanism: it does not participate in, delay, or make durable the
queue-entry lifecycle.

## Goals / Non-Goals

**Goals:**

- Notify locally registered passive observers of task-worker lifecycle
  snapshots: queued when a worker receives the persisted entry, then running
  and terminal states after they are recorded.
- Bootstrap a dashboard from the queue's current retained snapshots, including
  entries that a producer in another process has enqueued.
- Avoid repeated status polling and additional Django configuration.
- Work across independently deployed `runqueues` and Django application
  processes for Redis-backed queues, and within the same process for
  memory-backed queues.

**Non-Goals:**

- Durable, at-least-once, or cross-process observer delivery.
- Django Channels, WebSockets, middleware-based dispatching, or a separate
  ASGI listener.
- A generic replacement for Django signals or a notification UI.
- Completion notifications for memory-backed queues outside their local
  process.

## Decisions

### Backends choose the live transport

The task worker owns live lifecycle publication. After it receives an already
persisted queued entry, it publishes that immutable `queued` snapshot; after it
persists `running` and a terminal state, it publishes those snapshots in turn.
Producers and direct entry-state operations only persist state, because they
may run in another process and cannot be relied on to notify local observers.
Redis-backed queues publish through an intrinsic queue-scoped Redis channel;
memory-backed queues submit to an intrinsic in-memory broker. Each Django
process that registers an observer runs one local notification runtime that
invokes callbacks registered in that same process.

Redis Pub/Sub broadcasts rather than load-balances each message, so every
dashboard or integration process with an observer can update its own
projection. A Redis list or stream consumer group was rejected because it
would deliver a lifecycle state to one arbitrary process. A normal configured
`MemoryQueue` and `AsyncQueueWorker` are not used for memory notifications
because they require an application-owned async lifespan; the intrinsic broker
is thread-backed and local instead.

### Registration starts the runtime lazily

The public `queue_observer(queue_name, callback, entry_id=None)` registration
API accepts a queue name, callback, and optional entry ID filter. It starts the
runtime idempotently on first use and returns a subscription object with an
explicit `unsubscribe()` method. It will not be started from `AppConfig.ready()`: doing so would also
create a background consumer for management commands, test setup, reloaders,
and pre-fork parent processes that never register a callback.

Passive lifecycle observation is implemented in `django_queue.observers`.
Future active event handling belongs in a separate `django_queue.listeners`
module; the two features do not share a public API or a runtime contract.

This is automatic for applications that use the API and needs no additional
`QUEUES` entry. An in-process daemon thread is used rather than an ASGI task
because normal Django request processes have no durable application-level async
lifespan to own such a task. A Redis receiver blocks in Pub/Sub while idle,
does not poll or hold up process shutdown, and intentionally remains available
for the process lifetime after the last subscription is removed. If Redis makes
the receiver exit, it logs the failure and clears its local registration; a
later observer registration starts a fresh receiver without a custom reconnect
loop.

### Redis endpoints and memory state are local backend details

Notifications are published through the Redis client of the queue that owns
the entry. The runtime subscribes to the intrinsic completion channel for each
configured Redis-backed queue endpoint, deduplicating equivalent endpoints.
This supports applications with queues on more than one Redis deployment
without choosing an arbitrary default broker. Memory queues publish only to the
same process's broker and cannot notify a separate process.

### Queue snapshots establish current state during registration

An observer registers its subscription before requesting all current retained
entry snapshots from the named queue. It delivers the collection to the
observer, then continues with published states. Snapshot delivery is
intentionally idempotent from the observer's perspective: a current state may
arrive in both the retained collection and a live notification, and the
callback receives both without maintaining observer-side history.

The queue's retained entries, rather than an observer-owned record, are the
bootstrap source. A missed Pub/Sub message remains possible during registration,
downtime, or transport loss; this is explicitly accepted best-effort behaviour.

### Callbacks are local observations

The callback receives an immutable QueueEntry snapshot. Observers are passive
and apply only to AsyncQueue instances:
they cannot consume, retry, alter, delay, or otherwise participate in task
lifecycle. The runtime owns an
asyncio loop: asynchronous callbacks run directly on it, while synchronous
callbacks use the framework's asynchronous bridge. Matching callbacks run
sequentially in registration order. For one entry, snapshots are delivered in
the order lifecycle states were recorded, so a listener cannot observe a later
state before an earlier one. Callback exceptions are logged without stopping
delivery to later callbacks.
Retained snapshots are delivered only when a subscription is registered; this
is not a replay mechanism. Applications needing a later retained-state
bootstrap may register a new observer, and applications needing durable work
must record it themselves rather than rely on a callback.

## Risks / Trade-offs

- [A Pub/Sub message can be missed during registration, downtime, or connection loss] → The
  initial retained-entry collection provides bootstrap state; callers retain
  responsibility for durable workflow state.
- [A slow callback delays later local callbacks for the same entry] → Document
  sequential lifecycle delivery and isolate/log callback failures; applications
  can hand work off to their own executor or queue. The local runtime retains
  at most 128 pending snapshots per observed queue; excess snapshots are
  dropped, with one warning logged for that queue's process-local lifetime.
- [Redis endpoint discovery may encounter invalid or unavailable queue
  configuration] → Reuse configured queue construction and log notification
  runtime failures without changing a completed entry's outcome.
- [Multiple web processes receive every message] → Each process updates only
  observers registered in that process; delivery is intentionally broadcast.
- [A memory completion has no shared transport] → Deliver it only through the
  intrinsic broker in the producing process and document that boundary.

## Migration Plan

No data migration or setting is required. Deploying the feature is additive:
existing queues and workers begin best-effort lifecycle publication. Removing
the feature has no effect on retained task entries.

## Open Questions

None for the initial best-effort implementation. A future durable notification
or at-least-once queue strategy will need acknowledgement, retry, and consumer
ownership semantics and is intentionally deferred.
