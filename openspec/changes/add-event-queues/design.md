## Context

Existing queues are durable task queues: one worker handler records a terminal
entry for later lookup. Event delivery instead routes transient entries through
local listeners; consumed, rejected, and expired events have no result record
to retain.

## Goals / Non-Goals

**Goals:**

- Provide explicit Redis and memory event queue variants.
- Dispatch local sync and async listeners fairly from one shared runtime loop.
- Bound unconsumed events with a 60-second default lifetime.
- Preserve task queue behaviour and public imports.

**Non-Goals:**

- Task lifecycle observers or completion callbacks.
- A shared listener registry, durable delivery, or global ordering.
- JetStream, AMQ, or Kafka backend implementations.

## Decisions

### Semantic queue and worker boundaries

`AsyncQueue` and `EventQueue` are placed beneath `BaseQueue`. Existing
memory and Redis queue classes retain AsyncQueue task compatibility; new event variants
remove rather than terminally settle entries. A narrow `BaseQueueWorker`
supports the common lifecycle; `AsyncQueueWorker` keeps task settlement and
`EventQueueWorker` performs event claim, dispatch, release, removal, and
expiry.

Extending `AsyncQueueWorker` with an event mode was rejected because task
terminal-state persistence and transient event removal are materially different
settlement contracts.

### Runtime ownership

`DjangoQueueConfig.ready()` starts one process-wide `EventRuntime` when
event queues are configured. Its single background thread owns one asyncio loop
and schedules one event worker task per queue. This avoids a thread and loop per
queue, as well as an operational `runqueues` or `runevents` process.

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
distribute events across scaled processes; memory delivery is local. Redis key
expiry alone cannot remove pending IDs, so workers prune expired entries during
receipt and idle cleanup.

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
