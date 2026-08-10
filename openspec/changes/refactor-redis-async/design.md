## Context

`BaseQueue` is a synchronous contract. The Redis backends wrap `redis.Redis`,
whose commands block, and the memory backends wrap `queue.Queue`. The
asynchronous worker therefore reaches every backend operation through
`asyncio.to_thread`: seven such hops exist, in `worker._run` (dequeue),
`_dispatch` (mark running), `_record_terminal` (the terminal write, the
read-back, and the persistence-failure write), `asgi` (activation poll), and
`runqueues` (activation wait).

Handlers are already coroutines — `QueueHandler` is typed
`Callable[[QueueEntry], Coroutine[...]]`, `_dispatch` uses `create_task`, and
`runqueues` rejects a non-coroutine handler. So the thread hops are not handler
accommodation; they exist solely because the backend is synchronous.

`add-timeout-governance` shipped an execution budget but had to defer its
heartbeat. `asyncio.Timeout.reschedule` mutates a `TimerHandle` and is not
thread-safe, so a budget cannot be extended from a delegated thread. The
conversion is what unblocks it.

Two constraints are settled before this design and are not open here: the
asynchronous surface uses Django's `a`-prefix convention with the synchronous
names retained as wrappers, and every synchronous/asynchronous crossing uses
`asgiref.sync`, not `asyncio.to_thread` and not a hand-rolled adaptor.

## Goals / Non-Goals

**Goals:**

- Remove every `asyncio.to_thread` hop from the package.
- One backend contract, asynchronous, implemented by memory and Redis alike.
- Preserve the synchronous producer API so a synchronous Django view is
  unaffected.
- Ship the budget half of the heartbeat, which the conversion unblocks.

**Non-Goals:**

- Claim/acknowledge or lease recovery. `add-redis-claim-ack` and
  `add-redis-lease-recovery` own those, and this change must not pre-empt their
  delivery semantics.
- Lease ownership validation in the heartbeat. There is no lease to validate
  against; see the heartbeat decision.
- Concurrent dispatch. A worker still handles one entry at a time per alias;
  removing the thread hops changes where the I/O waits, not how many entries are
  in flight.
- Changing budget resolution, entry records, instants, or observability. Those
  are settled by the preceding three changes.
- A new dependency. `redis.asyncio` ships in the pinned redis 8.1.0 and
  `asgiref` is already a Django dependency.

## Decisions

### The asynchronous method is the implementation; the synchronous name wraps it

`aenqueue` contains the logic and `enqueue` is `async_to_sync(self.aenqueue)`.
The reverse — synchronous primary, asynchronous wrapper — would put the real
work on a thread and defeat the change.

This follows Django's ORM (`aget`/`get`, `afilter`/`filter`), which is the
convention a Django developer already reads fluently. The alternative
considered was a parallel `AsyncQueue` class hierarchy: rejected because two
hierarchies double the backend surface, and a queue alias would have to resolve
to one or the other at configuration time, which is exactly the kind of split
`entry_class`/`worker_class` deliberately avoids.

### `async_to_sync` supplies the sync-called-from-async guard for free

Calling a synchronous wrapper from inside a running event loop is a mistake: it
would block the loop it was called on. `asgiref` already refuses it, raising
`RuntimeError: You cannot use AsyncToSync in the same thread as an async event
loop - just await the async function directly.`

That message names the fix, so the package adds no guard of its own and writes
no custom exception. This mirrors the Django ORM's `SynchronousOnlyOperation`
in intent, and re-raising it as a package-specific class would only obscure a
diagnostic that is already better than one we would write.

### The Redis client is acquired per running event loop

This is the sharpest hazard in the change. A `redis.asyncio` connection pool
binds to the loop that created it, and `async_to_sync` does **not** run on the
caller's loop — it runs the coroutine on a loop of its own. Verified: a
coroutine awaited natively and the same coroutine reached through
`async_to_sync` observe different `get_running_loop()` identities.

So a single client instance created at queue construction would be created on
one loop and then used from another the first time a synchronous wrapper is
called, which is undefined at best. A queue therefore holds no client at
construction; it acquires one lazily keyed by the identity of the running loop,
so each loop gets its own pool and disposal is per loop.

Alternatives considered. Creating a fresh client per call is correct but
discards pooling, which is most of the point of a pool. Forbidding synchronous
wrappers on Redis backends would keep one loop but breaks the producer API this
change exists to preserve. Requiring the caller to pass a loop pushes an
implementation detail into application code.

### Memory backends convert too, and `apoll` is the one real bridge

A uniform contract is what lets the worker drop `to_thread` entirely; a mixed
contract would force it to keep both paths, which is the status quo with extra
steps. Memory backends gain no concurrency from being asynchronous, and their
methods are `async def` with no internal await. That is ceremony, and it is
worth it for the single contract.

One memory operation genuinely blocks: `poll()` is `queue.Queue.get(block=True)`
and waits on a threading primitive. `apoll` is therefore the one place the
package bridges the other way, with `sync_to_async(thread_sensitive=False)`.
Rewriting the memory queues on `asyncio.Queue` was considered and rejected: they
are documented as usable from synchronous Django code across threads, and an
`asyncio.Queue` is not thread-safe. The worker path — `dequeue_entry`,
`has_pending_entries`, and the `mark_*` transitions — touches only non-blocking
dictionary and `queue.Queue` operations, so none of it needs a bridge.

### Disposal becomes asynchronous, and the signal receiver bridges

An `redis.asyncio` pool must be closed from the loop that owns it, so `aclose`
is the real implementation and `close` wraps it. `close_queues` is connected to
a Django signal and must stay synchronous, so it calls the synchronous wrapper.
Because clients are keyed per loop, `aclose` disposes the pool for the running
loop, and `close` disposes what its own bridge loop owns.

### The heartbeat extends the budget, and says that is all it does

`_dispatch` publishes its `asyncio.Timeout` context in a `ContextVar`; the
public `heartbeat()` reads it and calls `reschedule()`. The handler task
inherits the worker's context at creation, so the call works at any depth.
Outside a dispatch the `ContextVar` is unset and the call raises.

It does **not** verify that the worker still owns the entry, because there is
nothing to verify against: delivery is best-effort, no claim exists, and no
other worker can reclaim an entry mid-flight. Adding an ownership check now
would mean inventing a lease this change has no mandate to design —
`add-redis-claim-ack` owns the ownership boundary and `add-redis-lease-recovery`
owns expiry. The heartbeat requirement here states the limitation in the
requirement text, so the gap is a documented scope boundary rather than an
implied guarantee. `add-redis-lease-recovery` extends the heartbeat to renew the
lease and to raise when ownership has been lost.

Rate limiting is the caller's contract, not a mechanism: a heartbeat is an
assertion of progress made when a handler approaches its budget and needs
another allotment. A handler that pings on a timer has turned its budget off
without saying so. This is documented as an expectation rather than enforced,
because a worker cannot distinguish an honest frequent ping from a dishonest
one, and a mechanism that guessed would break the legitimate case.

### Lease duration must exceed budget plus grace, and that belongs to the lease change

Recorded here because the conversion is where the constraint becomes visible,
not to implement it. When leases arrive, a lease equal to the budget leaves a
window: a handler abandoned at budget expiry still has to be cancelled, its
terminal outcome written, and — during shutdown — a grace period observed, all
after the lease has lapsed and another worker is entitled to reclaim the entry.
A lease must therefore exceed the budget by at least the cancellation grace
period. The two also run on different clocks: the budget is monotonic loop time,
the lease is Redis wall time.

## Risks / Trade-offs

- [A pool is created per event loop, so a process mixing synchronous producers
  and an asynchronous worker holds more than one] → Accepted and bounded. There
  are at most two in practice, the worker's loop and asgiref's bridge loop, and
  the alternative is a pool used from a loop that does not own it.
- [`async_to_sync` from a synchronous Django view is slower than the direct
  synchronous client it replaces] → Accepted. The producer path is one command;
  the worker path is the hot one and it becomes strictly cheaper. A project that
  enqueues heavily from async views can call `aenqueue` and pay nothing.
- [Custom `BaseQueue` subclasses break] → **BREAKING** and stated in the
  proposal. The package has no released consumers, and the README documents the
  contract for custom backends.
- [Memory backends carry `async def` with nothing to await] → Accepted, as the
  price of one contract. A mixed contract would keep `to_thread` in the worker,
  which is the thing being removed.
- [A budget-only heartbeat may read as a full liveness guarantee] → Mitigated by
  stating the limit in the requirement itself and in the README, and by
  `add-redis-lease-recovery` completing it. Nothing can reclaim an entry today,
  so the gap is not currently reachable.
- [`sync_to_async` on `apoll` reintroduces a thread] → Accepted and confined to
  one item-oriented method that blocks by design. No worker path reaches it.

## Migration Plan

The conversion lands bottom-up so the suite is meaningful at each step: the
`BaseQueue` contract and the memory backends first, then the Redis backends onto
`redis.asyncio`, then the three call sites that shed `to_thread`, then the
heartbeat. Synchronous wrappers are added with the contract, so existing
synchronous tests keep passing throughout and act as the regression net for the
producer API.

There is nothing to roll back in storage: the wire format, the entry record and
the Redis key layout are untouched. Rollback is reverting the change.

## Open Questions

- Whether `runqueues` should expose the resolved event loop policy for hosts
  that already run one. Out of scope here; the command owns its loop today and
  continues to.
