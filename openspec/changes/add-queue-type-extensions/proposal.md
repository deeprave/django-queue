## Why

Configured queues currently hard-code `AsyncQueueWorker` in their execution
paths and `QueueEntry` in their backends. That prevents task-oriented queues
from adding specialised worker behaviour or typed entry metadata without
forking the queue runtime.

## What Changes

- Allow each configured queue to optionally declare a dotted `WORKER` class,
  defaulting to `AsyncQueueWorker`.
- Allow each configured queue to optionally declare a dotted `ENTRY_CLASS`,
  defaulting to `QueueEntry`; custom entries extend the base entry contract.
- Make `runqueues` activate and construct the configured worker class only
  when its queue first has pending work.
- Make ASGI worker startup lazy per queue: start a queue's configured worker
  only after that queue receives work, rather than constructing one combined
  worker for every configured handler at lifespan startup.
- Validate `ENTRY_CLASS` during settings initialisation and `WORKER` during
  command startup, without instantiating either before its queue becomes active.
- Preserve the generic default behaviour, queue lifecycle, and worker delivery
  guarantees for queues that provide neither extension.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `configured-queue-registry`: Preserve and validate queue-level worker and
  entry-class metadata without passing it to backend constructors.
- `async-queue-workers`: Select worker types per configured queue and lazily
  start ASGI workers for queues that receive work.
- `queue-entries`: Create and restore configured `QueueEntry` subclasses while
  preserving the base entry lifecycle and wire contract.

## Impact

Updates queue settings metadata, worker construction in `runqueues` and ASGI
integration, and memory/Redis entry construction. It adds public extension
contracts but no new dependency or default configuration requirement.
