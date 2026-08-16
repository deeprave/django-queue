## Context

Existing queues are durable async queues: one worker handler records a terminal
entry for later lookup. Event delivery instead routes transient entries through
local listeners; consumed, rejected, and expired events have no result record
to retain.

## Goals / Non-Goals

**Goals:**

- Provide explicit Redis and memory event queue variants.
- Dispatch local sync and async listeners fairly from one shared runtime loop.
- Bound unconsumed events with a 60-second default lifetime.
- Preserve async-queue behaviour and public imports.

**Non-Goals:**

- Async-queue lifecycle observers or completion callbacks.
- A shared listener registry, durable delivery, or global ordering.
- JetStream, AMQ, or Kafka backend implementations.

## Decisions

### Semantic queue and worker boundaries

`AsyncQueue` and `EventQueue` are placed beneath `BaseQueue`. The initial
implementation used a `QueueProvider` hierarchy that prescribed
backend storage, claims, release, removal, and clock operations. That hierarchy
was superseded by the provider-composition decision below: only resource closure
is common, while each provider exposes the operations required by its own
transport-aware worker.
Existing memory and Redis queue classes retain AsyncQueue task compatibility;
new event variants remove rather than terminally settle entries. A narrow
`BaseQueueWorker` supports the common lifecycle; `AsyncQueueWorker` and
`EventQueueWorker` provide provider-agnostic handler and listener orchestration.
Concrete backend workers perform delivery, claim, release, removal, and expiry.

`QueueProviderRedis` owns all Redis-specific URL parsing, connection lifecycle,
script registration, key layout, and atomic storage operations. The provider
instance is an internal extension boundary; future backends need not reproduce
Redis delivery behaviour.

Extending `AsyncQueueWorker` with an event mode was rejected because task
terminal-state persistence and transient event removal are materially different
settlement contracts.

### Follow-up: provider composition and transport-aware workers

The initial provider hierarchy assumed that Redis claim, renewal, settlement,
and recovery operations were generic queue semantics. That is incorrect:
JetStream, NATS, Kafka, and SQS have native acknowledgement, visibility, or
commit models which do not map to Redis claims. The follow-up implementation
therefore replaces the semantic provider inheritance hierarchy with composition.

`AsyncQueue` and `EventQueue` become concrete, generic queue facades. They
retain only producer, reader, and administration semantics: raw item access,
entry enqueue/read, direct dequeue where supported, and explicit terminal
pruning. They compose a provider but do not expose it, inherit it, or forward
its delivery methods. Lifecycle transitions, pending scans, observer bootstrap,
and ownership values are runtime implementation details rather than public
queue operations.

Concrete backends become thin provider injectors. `RedisAsyncQueue` and
`RedisEventQueue` compose `QueueProviderRedis` and declare Redis-aware default
worker classes. Memory backends compose their memory provider and declare
memory-aware default workers. The common `AsyncQueueWorker` and
`EventQueueWorker` layers own only scheduling and handler/listener
orchestration; they do not read a provider or prescribe its delivery API. A
concrete worker receives its queue-owned provider during backend-controlled
construction and implements its transport's native receive, acknowledgement,
retry, renewal, recovery, and settlement behaviour. A future transport chooses
its own provider and default worker without being required to imitate Redis or
memory delivery.

Redis queue, worker, and provider implementation lives behind the optional
`django_queue.backends.redis` package boundary. The generic
`django_queue.backends` package remains Redis-free, while the Redis package
exports the supported concrete Redis queue and worker extension classes.

Workers receive their provider through backend-controlled construction. Redis
workers own the provider's private, stable delivery context and call Redis
claim/lease/acknowledgement/recovery primitives directly. Direct public dequeue
performs its complete receive-and-remove operation internally with an ephemeral
context. Handlers and listeners continue to receive only immutable entries and
communicate outcomes by return value or exception.

The public `QueueProvider` name remains available as an extension type, but its
initial common contract is limited to asynchronous resource closure. In
particular, it does not yet prescribe a clock: distributed transports may need
a transport-independent time reference, but that is promoted only when a
second concrete provider establishes its contract. Transport delivery methods
live on the queue-owned provider and are paired with their transport-aware
worker, not a public universal protocol. The provider instance is private to
its queue; that instance boundary, rather than leading underscores on its
methods, prevents application code from using transport coordination operations.

### Runtime ownership

`DjangoQueueConfig.ready()` registers a `request_started` receiver instead of
starting a thread during application import. Each WSGI or Django-ASGI process
starts one process-wide `EventRuntime` when it handles its first HTTP request.
Its single background thread owns one asyncio loop and schedules one event
worker task per queue. This avoids a thread and loop per queue, as well as an
operational `runqueues` or `runevents` process, and avoids inheriting a started
thread when a server preloads Django before forking workers.
If a dispatcher stops due to an infrastructure failure, the runtime recreates
it after bounded exponential backoff; listener failures continue to use the
normal delayed-release delivery path.
Each event queue owns one private worker identity for the lifetime of its
runtime. A recreated worker for that queue reuses the identity; public queue
operations never expose provider claim ownership.

### Listener protocol

`queue_listener(queue_name, filter=None)` exports local decorator registration.
`None` passes, `True` consumes, and `False` rejects and logs. A filter or
listener exception is unexpected: log it and release after one short fixed
delay. All-pass cycles use the same delay. The cursor advances on every
listener visit, so future dispatch begins after the listener that ended the
previous cycle.

### Lifetime and distribution

Event timeout resolution is entry override, queue `TIMEOUT`, then 60 seconds.
It is an unconsumed lifetime, not handler execution time. Redis claim leases
distribute events across scaled processes; configured memory event queues share
one process-local backend instance across Django request threads. Redis key
expiry alone cannot remove pending IDs, so workers prune expired entries during
receipt and idle cleanup. Event claims also reject and remove an event that
crossed its lifetime boundary after pruning but before the claim, so it cannot
reach a listener.

Redis event workers also renew the claim while an asynchronous or bridged sync
listener is active, and recover expired claims before receiving more work. This
keeps a slow active listener exclusive while returning a stopped worker's
unsettled event to pending delivery after its lease.

## Risks / Trade-offs

- [Broken listener repeatedly releases entries] → Fixed delayed release limits
  log and CPU spin; expiry bounds retention.
- [Multiple processes/listeners need ordering] → Document indeterminate order;
  applications needing strict order use one listener in one process.
- [Slow sync listener] → Bridge it off the shared event loop.

## Migration Plan

This is additive. Existing queue classes retain task semantics. Applications
select an event backend explicitly and deploy normally; AppConfig starts its
dispatcher. Rollback removes the event backend configuration and leaves only
short-lived Redis keys or local state to expire.

## Open Questions

None.
