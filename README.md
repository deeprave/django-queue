# Django Queue

This is an implementation of message queues for Django.

## Requirements

`django-queue` requires Python 3.14 or later. Queue entry IDs use the
standard-library UUIDv7 implementation introduced in Python 3.14.

## Message Queues

What are message queues? In Django, message queues enable independent and decoupled communication between parts of an
application or with external systems. For instance, one app can generate messages for another app to consume, avoiding
direct dependencies. This module implements a simple mechanism where a sender publishes messages, and consumers read and
remove them from the queue.

This module supports various queue types: first-in-first-out (FIFO), last-in-first-out (LIFO or stacks), and priority
queues, where messages with higher priority are consumed before lower-priority ones, regardless of their addition order.

## Implementation

This module currently implements two types of queues. Both use the same interface and are, to some extent,
interchangeable:

- **memory queues**: non-persistent and available only while the application is running.
- **redis queues**: persistent queues backed by a Redis server.

## Configuration

Queues are configured in the Django settings module, and use a simple and familiar configuration format like **DATABASES** and **CACHES**.

Example

```python
QUEUES = {
    "default": {
        "BACKEND": "django_queue.backends.RedisQueueJson",
        "LOCATION": f"redis://localhost:6379/12",
        "maxsize": 64,
    },
}
```

The above configures the queue backend to be redis, storing FIFO data in JSON format.
Redis-backed queues take a Redis URL as their location and own their asynchronous
connections; application code does not supply Redis client instances.

To implement a stack (FILO), the `django_queue.backends.RedisStackJson` can be used instead, or a `"stack": True` option added to the options.

All aliases are validated and initialised when Django starts. Application code
can retrieve a configured queue through `queues["alias"]`; initialisation only
constructs queue services and never starts a worker.

Configured in-memory queues are local to the resolving process and thread, so
they are not a shared broker. Use Redis when producers and consumers must share
work across threads, processes, containers, or external workers.

### Queue type extensions

Each alias may optionally choose the concrete worker and entry types it uses:

```python
QUEUES = {
    "requests": {
        "BACKEND": "django_queue.backends.RedisQueue",
        "LOCATION": "redis://redis:6379/12",
        "HANDLER": "myproject.queue_handlers.process_request",
        "WORKER": "myproject.workers.RequestWorker",
        "ENTRY_CLASS": "myproject.entries.RequestEntry",
    },
}
```

`WORKER` and `ENTRY_CLASS` each accept either a class object or a dotted import
path. They default to `AsyncQueueWorker` and `QueueEntry`, respectively.
Workers must subclass `AsyncQueueWorker` and use its normal queue-lookup and
handler-mapping constructor. A queue constructs its worker with its own clock,
so a subclass that overrides `__init__` must accept a `clock` keyword and pass
it to `super().__init__`, or accept `**kwargs` and forward them. Entry classes
must subclass `QueueEntry` and declare any additional fields as a frozen
dataclass; those fields must be JSON-serialisable, and are persisted and
restored without further work. Django validates and imports entry types during queue configuration and worker types during
`runqueues` startup. A worker is constructed only when its queue first becomes
active; an entry only when it is enqueued, restored, or updated.

Custom queue backends that support identified entry dispatch must implement
`has_pending_entries()`, returning whether `dequeue_entry()` can immediately
return an entry. To support local ASGI activation, they must also call
`send_entry_enqueued()` after durably enqueueing an entry. Built-in backends
expose `queue_name`, their stable entry namespace, which local ASGI enqueue
observation uses to match entries to an
alias.

## Usage

Within an application, data is added to the queue by using the `add` method:

Example

```python
from django_queue import queue
...
   queue.add({"some": "object", "with": "values"})
...
```

Priority queues require slightly different handling in that a priority should be set to determine the order in which messages are consumed and when added should be done as a `(priority, value)` tuple:

```python
from django_queue import queue
...
   queue.add((10, {"some": "object", "with": "values"}))
...
```

Multiple values can be added in the one `add()` call if required.

### Instants

`ClockTime` is how this package names a point in time: an immutable value
holding whole seconds and microseconds since the Unix epoch. It exists so an
instant and a duration cannot be confused — a duration stays a plain count of
seconds.

```python
from datetime import UTC, datetime

from django_queue import ClockTime

moment = datetime(2026, 8, 3, 23, 33, 20, 250_000, tzinfo=UTC)

instant = ClockTime.from_timestamp(1785800000.25)  # a count of seconds
instant = ClockTime.from_timeval(1785800000, 250_000)  # a Redis TIME pair
instant = ClockTime.from_datetime(moment)  # a timezone-aware datetime

instant.to_timestamp()  # 1785800000.25, the durable form
instant.to_datetime()  # an aware UTC datetime, for calendar work
```

Instants compare and order chronologically. Subtracting one from another gives
the seconds between them, and adding or subtracting a count of seconds gives
another instant, with the duration on either side:

```python
elapsed = finished - started  # a float count of seconds
later = started + 600.0  # a ClockTime
same = 600.0 + started  # the order of operands does not matter
started + finished  # TypeError: adding two instants means nothing
```

Construction rejects anything that cannot describe an instant. A component of
the wrong type raises `TypeError` — including a `bool`, which is an integer in
Python but not a moment. A microsecond component outside `[0, 1000000)`, a naive
datetime, a count of seconds that is NaN or infinite, or a time before the epoch
raises `ValueError`. The epoch is a floor on arithmetic too: shifting back past
it fails rather than yielding a negative time.

An instant does not convert to a number implicitly. `float(instant)` raises, and
so does `json.dumps` on one, so a caller that wants a number asks for it.

### Identified queue entries

The entry-oriented API is appropriate when a producer needs to poll the
outcome of work processed later. Payloads and handler results must be
JSON-serialisable. The queue generates the UUIDv7 identifier and owns all
lifecycle timestamps, taking them from its own clock — Redis-aligned for a Redis
queue, local time otherwise. That clock is available as `queue.clock`, so
anything recording times alongside a queue's entries can share its basis.

`queued_at`, `dispatched_at` and `finished_at` are `ClockTime` values, stored as
a float count of seconds since the epoch. Nothing parses a string or resolves a
timezone to read one, and a stored instant is directly usable as a Redis
sorted-set score.

Because those instants share one basis, an entry can report elapsed time
directly:

```python
entry.queued_for  # seconds it waited before a worker picked it up
entry.ran_for  # seconds its handler took
```

Each is a count of seconds carrying its microseconds, not a whole number of
them — a handler that ran for 137 microseconds reports `0.000137`, which matters
because most work finishes in well under a second.

Both are derived from the instants rather than stored, so they cannot disagree
with them, and both are `None` until the instants describing them exist — an
entry still waiting has not waited zero seconds. They are also `None` if the
instants contradict, which a clock recalibrating backwards can cause: a negative
elapsed time is meaningless rather than merely small.

### Asynchronous queue API and heartbeat

The `a`-prefixed entry operations are the primary API in asynchronous code:
`aenqueue`, `aget_entry`, `adequeue_entry`, `ahas_pending_entries`, and the
`amark_*` lifecycle operations. Built-in queues also expose `aadd`, `aget`,
`apoll`, `apeek`, `asize`, and `aclear` for raw queue values. Await these from
an ASGI view, a handler, or another coroutine:

```python
entry_id = await queue.aenqueue({"request_id": 42})
entry = await queue.aget_entry(entry_id)
```

The corresponding synchronous methods remain for synchronous Django code. They
must not be called from a running event loop; use the `a`-prefixed operation
instead. A custom backend implements the asynchronous methods, while the base
class supplies the synchronous wrappers. `len(queue)`, `bool(queue)`, and
`is_empty()` are likewise synchronous-only; use `asize()` or `ais_empty()` in
an event loop. Synchronous wrapper calls release their bridge-loop resources
after each operation, so a custom backend's `aclose()` must be idempotent.

An ASGI worker and `runqueues` dispose their queues on their owning event loop.
Other async hosts must await `aclose_queues()` before closing that loop;
`close_queues()` only serves synchronous-wrapper resources and cannot close a
different loop's Redis client.

For a Redis backend, a synchronous queue operation uses a fresh bridge-loop
connection and Redis `TIME` calibration before closing it. Prefer the async API
in asynchronous or high-volume producer code, where the loop-local client and
clock are reused.

Long-running handlers may call `heartbeat()` after genuine progress to restart
their current execution budget as they approach its deadline:

```python
from django_queue import heartbeat


async def process_request(entry):
    await store_progress(entry.payload)
    heartbeat()
    return {"processed": True}
```

Heartbeat extends only the local execution budget. It is neither a lease
renewal nor an ownership or delivery guarantee; a later claim-and-recovery
backend may add those guarantees. It is not a keepalive to call on a timer or
in a loop: doing so disables the protection the budget provides.

```python
from django_queue import queue

entry_id = queue.enqueue({"request_id": 42})
entry = queue.get_entry(entry_id)

assert entry.status == "queued"
```

### Lifecycle observation

Use `queue_observer` for best-effort, passive task monitoring. A subscription
receives immutable entry snapshots from a task worker; it cannot affect task
execution.

```python
from django_queue import queue_observer


def update_dashboard(entry):
    print(entry.id, entry.status)


subscription = queue_observer("default", update_dashboard)
subscription.unsubscribe()  # stop future local delivery
```

Memory queues notify only within the same Django process. Redis queues use
best-effort Pub/Sub: a disconnected observer can miss transitions. Register a
new observer when a new retained-state bootstrap is needed. Observer callback
failures are logged and do not affect queue processing. The local observer
delivery queue holds up to 128 snapshots; later snapshots are dropped when it
is full, with one warning logged for the process lifetime.

When a worker receives an entry, it first publishes that entry's persisted
`queued` snapshot, then publishes `running` and its terminal state after each
state is stored. An entry awaiting a worker is still available in the retained
snapshots delivered at subscription, but it produces no live
observation until a worker receives it.

The first Redis observer for a queue starts one daemon receiver for that
process. It blocks in Pub/Sub while idle rather than polling, consumes no CPU
while it waits, and does not keep Django alive during shutdown. The receiver is
intentionally retained for the process lifetime so later subscriptions can
reuse it. If it exits because Redis fails, it logs the failure and clears its
registration; a later observer registration starts a fresh receiver.

An entry transitions through `queued`, `running`, and one terminal status:
`succeeded`, `failed`, or `timeout`. Failed entries expose only an exception
type and safe message; the worker logs the traceback for diagnosis. A fourth
terminal status, `cancelled`, exists on the backend contract but no worker path
produces it: a handler that finishes during shutdown is recorded by what it
returned, and one that overruns is recorded as `timeout`. It is reserved for a
deliberate per-entry cancellation the queue does not yet offer.

### Asynchronous worker

An application or management command explicitly owns the worker task. It must
not be started from a request handler or Django app initialisation hook.

```python
import asyncio

from django_queue import AsyncQueueWorker, queues


async def process_request(entry):
    return {"processed": entry.payload["request_id"]}


worker = AsyncQueueWorker(
    {"default": queues["default"]},
    {"default": process_request},
)
asyncio.run(worker.run())
```

The worker dispatches one entry at a time and runs until cancelled. On
cancellation it stops accepting new entries, gives an active handler its
configured grace period, then cancels it if needed.

Redis queues use leased claims for at-least-once delivery. A worker claims an
entry, renews its lease while dispatching, and atomically settles its terminal
entry outcome only while it still owns that claim. Expired claims return the
same entry ID to pending work, so a process failure can cause the handler to
execute more than once. Handlers that make external changes must therefore be
idempotent. Queue backends without claim-lease support retain best-effort
delivery.

Claim, renewal, acknowledgement, recovery, and settlement are backend-neutral
queue operations; Redis keys, scripts, timestamps, and record layout are not
part of the public contract. A claim raises `QueueEmptyException` when no entry
is pending, `QueueClaimConflictError` when its pending entry is already claimed,
and `QueueEntryMissingError` when the claimed entry record is unavailable.
Redis Cluster is not supported by these primitives.

If a terminal outcome cannot be persisted because of an infrastructure failure,
the worker logs the failure and continues. When it can still read a `running`
entry, it makes one best-effort attempt to record a safe
`QueuePersistenceError` failure outcome. If it cannot confirm either terminal
outcome, the worker raises `QueuePersistenceError` rather than accepting
further entries.

Loss of claim ownership is different: the worker stops handling that entry
without recording an outcome, then continues serving later work. Recovery or
the worker that acquired the claim owns the retry and its terminal outcome.

### Execution budgets

Every dispatch runs under a budget: a count of seconds after which the worker
stops waiting, cancels the handler, and records the entry as `timeout`. The
worker then moves to the next entry, so one handler that never returns cannot
starve an alias. A budget is always in force — an unbounded handler is the
defect the budget exists to remove — so there is no value meaning unlimited.

The budget is resolved per dispatch, taking the first of these that is set:

1. the worker's `timeout_seconds` override, which applies to every alias it serves
2. the entry's own `timeout_seconds`, set when it was enqueued
3. the alias's `TIMEOUT` setting
4. 600 seconds

```python
QUEUES = {
    "default": {
        "BACKEND": "django_queue.backends.RedisQueueJson",
        "LOCATION": "redis://localhost:6379/12",
        "TIMEOUT": 30,
    },
}
```

`TIMEOUT` is a finite positive number of seconds, validated when settings are
initialised rather than at first dispatch, so a bad value fails at startup. The
same rule applies wherever a budget is supplied — the setting, the `enqueue`
keyword, and the worker override all reject a non-number with `TypeError` and a
zero, negative, infinite or NaN value with `ValueError`, at the point it is
supplied. There is no value meaning unlimited, infinity included.

A single piece of work that legitimately takes longer carries its own budget:

```python
entry_id = queue.enqueue({"request_id": 42}, timeout_seconds=120)
```

The budget expires on the event loop's monotonic clock, while the entry's
timestamps are read from the queue's own clock. They are deliberately
independent: the budget decides when to stop, and the entry records what
happened. A timed-out entry's `ran_for` is a wall-clock measurement and will not
equal the budget that expired.

Custom entry-capable backends must implement `mark_timed_out(entry_id)`
alongside the other terminal transitions, moving a `running` entry to `timeout`
and setting `finished_at`.

The shutdown grace period is separate from the budget: it bounds how long a
cancelled worker waits for an active handler, and its expiry is also recorded as
`timeout`.

A handler that raises `TimeoutError` of its own — from `asyncio.wait_for`, an
HTTP client, or a database driver — is recorded `failed` with that error, not
`timeout`. Only the budget actually running out means the handler never
answered.

### Worker observability

Each `AsyncQueueWorker` has a generated UUIDv7 identity and exposes a frozen,
process-local `snapshot`. It reports the current run state, registered queue
aliases, active queue name and entry ID, total dispatches, and confirmed persisted
terminal outcomes:

```python
from django_queue import WorkerSnapshot

snapshot: WorkerSnapshot = worker.snapshot
health = {
    "worker_id": str(snapshot.worker_id),
    "running": snapshot.running,
    "queue": snapshot.active_queue_name,
    "succeeded": snapshot.succeeded_count,
}
```

The worker emits INFO lifecycle records with `queue_worker_event` set to
`started`, `dispatch_started`, `terminal_recorded`, or `stopped`. Their
structured fields are prefixed with `queue_worker_` and include the same worker
ID, running state, registered queue aliases, active queue name and entry ID,
start time, dispatch count, and outcome counters as the snapshot. Counters advance
only after the corresponding terminal entry state has been confirmed in the
backend. `timed_out_count` is counted separately from `cancelled_count`, so a
handler abandoned on its budget is distinguishable from one the queue was told
to cancel.

`started_at` comes from the worker's clock, which its queue supplies when it
creates the worker, so a worker's recorded time and the entries it dispatches
share one basis and elapsed time across them is meaningful. A worker built
directly defaults to local time and accepts a `clock` argument. Like every
instant the package reports it is a `ClockTime`, rendered in structured log
records as a count of seconds rather than an ISO string.

`running_for` reports how long the worker has been running, measured on that
same clock, and stops advancing once the worker leaves its dispatch loop so it
reports how long it ran. Structured records carry it, and a terminal-outcome
record also carries the entry's `queued_for` and `ran_for`.

Reading a running worker's snapshot samples the queue clock to measure
`running_for`, so on a Redis-backed queue a snapshot read takes the clock's lock
and may trigger its periodic recalibration. A stopped worker reads its recorded
stop instant instead and touches no clock. Read snapshots from the worker's
event loop for a consistent observation; they do not coordinate cross-thread
reads. A shutdown
can interrupt the worker's acknowledgement of an in-flight terminal write, so
the final snapshot records only terminal outcomes the worker observed before it
stopped.

Snapshots and log records are local to the worker process. Collect logs or add
an exporter in application infrastructure to aggregate multiple `runqueues` or
web processes; this package does not provide distributed liveness or metrics.

### ASGI in-process worker

For local, single-process use and integration tests, an ASGI application can
explicitly run a worker through the ASGI lifespan protocol. The application,
not `django-queue`, decides whether to apply this wrapper; for example, it can
use an environment-derived Django setting in `asgi.py`.

```python
from django.conf import settings
from django.core.asgi import get_asgi_application

from django_queue.asgi import with_queue_worker


async def process_request(entry):
    return {"processed": entry.payload["request_id"]}


application = get_asgi_application()
if settings.ENABLE_LOCAL_QUEUE_WORKER:
    application = with_queue_worker(
        application,
        handlers={"default": process_request},
    )
```

`with_queue_worker()` observes entries enqueued by that ASGI process after
lifespan startup and starts one configured worker for each alias only when that
alias first receives entry work. It cooperatively stops active workers during
lifespan shutdown. The observation is deliberately process-local: another
process cannot wake this worker. It logs a warning whenever it starts because
an in-process worker is not supported for production use. Use an external
`runqueues` worker with a shared backend such as Redis in production.

The wrapper accepts an explicit queue mapping for integration tests. Pass the
same `MemoryQueue` instance to the wrapper and to the component producing work
to exercise a complete request-to-worker flow without Redis. That queue remains
local to one ASGI process: it cannot be consumed by another process, container,
or external `runqueues` worker.

### External `runqueues` worker

For production, run queue processing as a separate Django process and use a
shared backend such as Redis. Declare an asynchronous handler on each queue
that the process should dispatch:

```python
# settings.py
QUEUES = {
    "requests": {
        "BACKEND": "django_queue.backends.RedisQueueJson",
        "LOCATION": "redis://redis:6379/12",
        "HANDLER": "myproject.queue_handlers.process_request",
    },
}


# myproject/queue_handlers.py
async def process_request(entry):
    return {"processed": entry.payload["request_id"]}
```

Start it as its own service or container command:

```console
python manage.py runqueues
```

`runqueues` validates every configured `HANDLER` and `WORKER`, exiting non-zero
on a configuration error, then waits to create each configured worker until that
alias has pending entry work. It reports the configured handler count at startup
and each alias as its worker begins. Once started, a worker runs until it
receives `SIGINT` or `SIGTERM`; shutdown cooperatively stops all active workers.
Queue definitions without `HANDLER` remain available to application code but are
not dispatched; when no handlers are configured, the command reports this and
exits successfully. A worker failure is logged while the remaining queues stay
watched; the command exits non-zero only when no configured queue is left.

With all queues, the `get()`, `peek()` and `pull()` methods return the object.
With priority queues the priority is only used with and relevant to `add()`.
Identified entries have no priority parameter, so their worker dispatch remains
FIFO until priority-aware entry enqueueing is introduced.

## Queue Interface

All queues conform to the following interface:

#### Properties

- stack: returns True if the queue is a stack (LIFO) otherwise it is FIFO or priority based
- capacity: returns the queue capacity, 0 for unlimited
- queue_name: the stable entry namespace when the backend exposes one

#### Methods

- add(item1[, item2, item3 ...]): add one or more items to the queue. With priority queues, items can be passed as `(priority, item)` tuples, although if not a tuple the default priority of 0 is defined. Priorities are evaluated as higher values = high priority, lower values = low priority. Priority can be positive or negative with 0 considered "normal".
- get(): retrieve and remove the next item from the queue.
- poll(): same as get(), but blocks if no item is available. Redis priority
  queues accept ``timeout`` and ``retries``; timeout applies to each retry
  attempt.
- peek(): retrieve but not remove the next item in the queue.
- size(): returns the number of items currently in the queue. `len(queue)` also returns this value.
- is_empty(): returns true if there are no items currently in the queue.
- clear(): remove all items from the queue.
- close(): closes and destroys the queue.
- has_pending_entries(): returns whether `dequeue_entry()` can return an entry.
- enqueue(): custom entry-capable backends must call `send_entry_enqueued()`
  after durable enqueue when local ASGI activation is required.
- the queue itself can be used in the context of a boolean: True if there are items in the queue else False.

#### Exceptions

- InvalidQueueBackendError: an `ImproperlyConfigured` error indicating an issue
  with the Django `QUEUES` configuration.
- QueueFullException: operation (addition) attempted on a queue that has reached capacity
- QueueEmptyException: operation (get, peek or timed out poll) accepted on an empty queue
- QueueEncodingException: error occurred in encoding the item
- QueueValueError: error occurred in decoding an item
