## Context

Queue configuration already carries `BACKEND` and optional `HANDLER` metadata,
but `runqueues` constructs `AsyncQueueWorker` directly. Both Redis and memory
backends also construct and restore `QueueEntry` directly. The ASGI wrapper
constructs a single worker for all supplied handlers at lifespan startup,
despite `runqueues` already treating each configured handler as a distinct
worker.

Task-oriented queues need specialised worker behaviour and entry metadata while
retaining the generic queue lifecycle. Extensibility must not make generic
queues depend on task code or force users to configure it when they need only
the defaults.

## Goals / Non-Goals

**Goals:**

- Support optional queue-level worker and entry-class extensions with the
  current generic types as defaults.
- Accept a class object or dotted import path in Django or programmatic queue
  configuration, validate it, and keep it out of backend constructor options.
- Resolve `ENTRY_CLASS` during settings initialisation and `WORKER` at command
  startup, construct a worker only when its queue first has pending work, and
  create entries only for enqueue, restore, or lifecycle operations.
- Create a configured worker per active queue in `runqueues`.
- Start ASGI workers lazily per queue after local enqueue activity, rather than
  eagerly creating one aggregate worker for all handlers.
- Preserve immutable entries, JSON persistence, lifecycle fields, and existing
  worker cancellation and delivery behaviour for every entry subtype.

**Non-Goals:**

- Multiple worker instances for one queue alias, worker pools, scheduling, or
  cross-process ASGI wake-up.
- Arbitrary worker constructor signatures or non-`AsyncQueueWorker` worker
  implementations in the initial extension point.
- Arbitrary non-`QueueEntry` envelopes, alternative serialisation formats, or
  relaxing the required base lifecycle fields.
- Changing the external `runqueues` process model or enabling the in-process
  ASGI worker for production.

## Decisions

### Queue settings own extension metadata

Each queue may declare optional `WORKER` and `ENTRY_CLASS` metadata. Each value
accepts either a class object or a dotted import path. Omission inherits
`AsyncQueueWorker` and `QueueEntry`, respectively. Settings initialisation
validates and resolves the entry class but preserves a configured worker value
unchanged on the queue. The queue resolves and validates its worker class only
when a worker consumer activates that queue. Neither operation instantiates an
extension. Public metadata remains outside third-party backend options.

`WORKER` must resolve to an `AsyncQueueWorker` subclass whose constructor
accepts the normal queue lookup and handler mapping. This is deliberately a
subclass contract, rather than a loose `run()` protocol, so the command and ASGI
lifecycle retain common cancellation, observability, and error semantics.

`ENTRY_CLASS` must resolve to a `QueueEntry` subclass. Backends retain the
resolved class on a common internal entry-factory boundary, then use it only to
create and restore entries. A task entry can therefore add JSON-safe metadata
while inheriting the standard ID, queue, status, timestamp, payload, result,
and error contract. Entry subclasses remain responsible for extending their
wire conversion coherently.

### Worker activation follows queue activity

`runqueues` preflights every configured handler but starts an activation task
rather than constructing each worker immediately. An activation task observes
its queue for pending work using the same non-blocking backend access pattern as
the generic worker. On the first observed pending entry, it asks that queue to
resolve and construct its `WORKER`, then starts its normal dispatch loop. An
idle queue therefore has no worker instance, while an activated worker continues
until its established cancellation or failure policy ends it.

The ASGI wrapper follows the same per-alias model. At lifespan startup it
installs local enqueue observation for its supplied aliases but constructs no
worker. The first enqueue observed in that Django process starts the alias's
configured worker, exactly once for that lifespan. The worker then retains its
normal loop and can process later entries without recreation.

This enqueue observation is intentionally process-local. An entry added by a
different process cannot wake an ASGI worker without introducing shared
notification or polling. That is acceptable because the ASGI worker remains a
local/integration-test facility; production cross-process dispatch uses
`runqueues` with a shared backend.

### Entry selection belongs at the backend boundary

Queue backends receive the resolved entry class as constructor configuration or
an equivalent internal factory. They call it for `enqueue` and `get_entry`,
instead of importing `QueueEntry` directly. `BaseQueue` retains methods typed
to `QueueEntry`, allowing subclasses without imposing generic type parameters
on every queue API.

This keeps worker handlers polymorphic: a generic handler receives
`QueueEntry`, while a specialised worker or handler can rely on its configured
subclass after the configuration contract has selected it.

## Risks / Trade-offs

- [A bad dotted worker path or incompatible class delays failure] → Resolve and
  validate it when the queue first needs a worker, with a clear queue-specific
  error. Entry classes remain configuration-time validation.
- [Metadata leaks into a backend constructor] → Strip `WORKER` and
  `ENTRY_CLASS` beside existing `HANDLER` metadata, set the resolved entry
  factory through the common boundary, and regression-test a strict backend.
- [ASGI misses work enqueued by another process] → Document the local enqueue
  boundary and retain `runqueues` as the external shared-backend worker.
- [A custom entry breaks persistence] → Require a `QueueEntry` subclass and
  exercise create, restore, running, and terminal transitions across memory
  and Redis backends.
- [A custom worker bypasses expected lifecycle behaviour] → Require an
  `AsyncQueueWorker` subclass for this initial API; broader worker protocols
  remain future work.

## Migration Plan

The defaults retain current behaviour, so no application settings migration is
required. Users can add one extension key per alias incrementally. Custom queue
backends that support identified entry dispatch must implement
`has_pending_entries()` so lazy worker activation can observe their entry
backlog. Removing an extension reverts future construction to the generic
class; existing durable entries still require the entry class that wrote their
extended representation until they expire or are consumed. Existing eager ASGI
workers are replaced by lazy activation, so an idle ASGI queue will no longer
create a worker at lifespan startup.

## Open Questions

None for the initial extension contract. Supporting entry-schema migration or
arbitrary worker factories requires a separate compatibility design.
