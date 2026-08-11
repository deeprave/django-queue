## 1. Contract and bridge

- [x] 1.1 Add failing tests for the dual surface on `BaseQueue`: an `a`-prefixed
  method is awaitable, its synchronous counterpart returns the same value, and
  calling a synchronous name from inside a running event loop raises.
- [x] 1.2 Make `BaseQueue` declare the asynchronous entry contract as its
  abstract methods -- `aenqueue`, `aget_entry`, `adequeue_entry`,
  `ahas_pending_entries`, the four `amark_*`, and `aclose` -- and provide the
  synchronous wrappers concretely on the base, bridged with
  `asgiref.sync.async_to_sync`, so no backend writes a wrapper itself.
- [x] 1.3 Confirm the bridge's own error is the sync-called-from-async guard and
  assert on it directly; add no package exception and no custom check.

## 2. Memory backends

- [x] 2.1 Add failing tests for the memory entry lifecycle through the
  asynchronous methods, parametrised over `MemoryQueue`, `MemoryPriorityQueue`
  and `MemoryStack` as the existing lifecycle tests already are.
- [x] 2.2 Convert the three memory backends to the asynchronous contract, with
  `async def` methods and no internal await, and update the class-level borrow
  list `MemoryPriorityQueue` uses to take its entry methods from `MemoryQueue`.
- [x] 2.3 Implement cancellation-safe `apoll` with `get_nowait()` and a short
  asynchronous sleep, and add a test that awaiting it does not stall other
  tasks on the loop.

## 3. Redis backends

- [x] 3.1 Add failing tests for the Redis entry lifecycle through the
  asynchronous methods, covering enqueue, dequeue, each terminal transition, and
  the budget persisted on the record.
- [x] 3.2 Move both Redis backends to `redis.asyncio.Redis`, awaiting every
  command, including the pipeline and Lua paths.
- [x] 3.3 Acquire the client lazily keyed by the running event loop rather than
  at construction, and add a test that the same queue used from a worker loop
  and from a synchronous wrapper's bridge loop obtains distinct clients and both
  operate correctly.
- [x] 3.4 Implement `aclose` to release the resources belonging to the disposing
  loop, and add a test that a closed queue can be used again.
- [x] 3.5 Confirm `RedisQueueClock` still calibrates against the asynchronous
  client, including its background refresh and its lock, since it reads Redis
  `TIME` on the same connection resources.

## 4. Shed the thread hops

- [x] 4.1 Add a failing test that a complete dispatch, from dequeue through the
  terminal outcome, runs entirely on the event loop's own thread.
- [x] 4.2 Remove the five `asyncio.to_thread` calls in `django_queue/worker.py`
  -- the dequeue, the mark-running, and the three in `_record_terminal` -- and
  await the backend instead.
- [x] 4.3 Remove the `asyncio.to_thread` call in `django_queue/asgi.py` and
  await `ahas_pending_entries`.
- [x] 4.4 Remove the `asyncio.to_thread` call in `runqueues` and await the same.
- [x] 4.5 Assert no `asyncio.to_thread` remains anywhere in `django_queue/`.

## 5. Registry disposal

- [x] 5.1 Add failing tests for asynchronous registry disposal and for the
  shutdown signal receiver staying a synchronous callable.
- [x] 5.2 Add asynchronous disposal to the queue handler and have `close_queues`
  delegate through the bridge.

## 6. Heartbeat

- [x] 6.1 Add failing tests for the heartbeat: extending from the handler
  coroutine, extending from a function the handler calls, raising outside a
  dispatch, and a handler that heartbeats repeatedly outliving its budget.
- [x] 6.2 Publish the active dispatch's `asyncio.Timeout` in a `ContextVar` and
  implement the public `heartbeat()` call, rescheduling the deadline through it.
  Clear the variable when the dispatch ends so a later call outside a dispatch
  raises rather than extending a finished one.
- [x] 6.3 Add a test that a heartbeat does not alter the entry's recorded
  `ran_for`, since the budget and the entry's wall-clock timings stay
  independent.

## 7. Documentation and validation

- [x] 7.1 Document the asynchronous surface in the README: the `a`-prefixed
  methods as the primary API, the synchronous names as wrappers that refuse to
  run on an event loop, and the custom-backend contract now being the
  asynchronous methods.
- [x] 7.2 Document the heartbeat as an assertion of progress a handler makes as
  it approaches its budget, and state plainly that it extends the budget only
  and is not an ownership or liveness guarantee.
- [x] 7.3 Record in `add-redis-lease-recovery` that it must extend the heartbeat
  with lease renewal and ownership validation, and that a lease must exceed the
  budget by at least the cancellation grace period, so the constraint is not
  lost between changes.
- [x] 7.4 Run Ruff, ty, the full pytest suite repeatedly to confirm the
  converted I/O paths are not flaky, and strict OpenSpec validation.
