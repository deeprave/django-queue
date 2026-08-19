## Context

`_QueueObservers` (`django_queue/observers.py`) owns two daemon threads per
observed queue, started lazily on first `register()`:

- **`receiver`**: runs `self.queue._observer_receiver(self.publish)`
  synchronously. For Redis this is `QueueProviderRedis.observe`, which
  blocks on a synchronous `redis.Redis` client's `pubsub.listen()` generator
  and calls `on_snapshot` (i.e. `publish`) per message. The base class
  default (`backends/base.py`) returns `None`, so the memory backend never
  starts a receiver thread — it publishes in-process directly.
- **`dispatcher`**: pulls `_Delivery` items off a bounded, thread-safe
  `queue.Queue(maxsize=128)` (`self.events`) and invokes matching callbacks.
  It creates its own `asyncio.new_event_loop()` solely so it can
  `run_until_complete(sync_to_async(callback)(...))` for synchronous
  callbacks and `run_until_complete(result)` for awaitables — it is not a
  running (`run_forever`) loop and nothing schedules work onto it from
  outside this thread.

`publish()` is called from arbitrary caller threads (Redis: the receiver
thread; memory: whichever thread records a state transition) and must stay
non-blocking — that's why deliveries are handed off through the thread-safe
`queue.Queue` rather than calling callbacks inline.

See [proposal.md](proposal.md) for why the sync Redis client is the problem
being fixed here.

## Goals / Non-Goals

**Goals:**
- Eliminate the only synchronous `redis.Redis` usage in the backend.
- Keep the fix scoped to the receiver: no change to `publish()`'s
  thread-safety contract, the dispatcher's `queue.Queue` hand-off, or
  callback-invocation semantics (ordering, sequential dispatch, failure
  isolation) already specified in `completion-notifications`.

**Non-Goals:**
- Collapsing the `receiver` and `dispatcher` threads into one. The
  dispatcher's loop is not `run_forever`-based today, so hosting the
  receiver inside it would require restructuring the dispatcher too —
  out of scope for a fix targeted at the sync client (see Alternatives).
- Changing `EventRuntime` or event-queue workers; they are unrelated to
  `AsyncQueue` lifecycle observation and already async-native.
- Changing the memory backend, which has no `_observer_receiver`.

## Decisions

**Add `aobserve()` as the async implementation, named per this codebase's
existing `a`-prefix convention; keep the receiver on its own thread, driven
by `async_to_sync(receiver)()`.**

- `QueueProviderRedis` gains `async def aobserve(self, on_snapshot)`, using
  `redis.asyncio`'s pubsub: `client = async_redis.Redis.from_url(self._redis_url)`,
  `async for message in pubsub.listen(): ...; on_snapshot(entry)`, wrapped in
  `try/finally` to `await pubsub.aclose()` / `await client.aclose()` (not the
  deprecated `.close()`). Every
  other async operation on this class (`aadd`, `aget`, `apoll`, `aclaim`,
  `aack`, `apublish`, ...) already follows this naming with no synchronous
  counterpart at all — `observe()` was the sole exception, so it is removed
  rather than converted in place; it has exactly one caller, so no
  compatibility shim is needed.
- `on_snapshot` (`_QueueObservers.publish`) stays a plain synchronous method —
  it only appends to in-memory state under a `threading.RLock` and does a
  non-blocking `queue.Queue.put_nowait`, so it's safe to call directly from
  inside the receiver's coroutine without an async bridge.
- `_observer_receiver`'s declared type (`backends/base.py`) changes from
  `Callable[[], None]` to `Callable[[], Awaitable[None]]`; the Redis override
  (`backends/redis/redisqueue.py:38`) returns a zero-arg callable bound to
  the new `aobserve`.
- `_QueueObservers._run_receiver` changes from calling `receiver()` directly
  to `async_to_sync(receiver)()`. This is not a new bridging idiom for this
  codebase: `asgiref.sync.async_to_sync` is already how every other
  sync-context call into async code is made here — `BaseQueue.close()`
  (`async_to_sync(self.aclose)()`), `BaseQueue._run_synchronously`
  (`async_to_sync(self._run_and_close)(...)`), `clock.py`'s `now()`
  (`async_to_sync(self.anow)()`), and `provider.py`'s `_anow_and_close`
  caller. `asyncio.run()` is deliberately not used: it appears exactly once
  anywhere in this codebase, at `runqueues.py`'s single top-level process
  entry point, and mixing it in as a second, inconsistent bridging idiom
  here would depart from that convention for no benefit — `receiver` already
  matches the `Callable[[], Awaitable[None]]` shape `async_to_sync` expects.
  The thread, its naming (`django-queues-observer-{queue_name}`), daemon
  flag, and the `try/except/finally` that logs failures and clears
  `self.receiver` are unchanged.
- Remove `_observer_redis_client()` (no longer used) once nothing references
  it.

**Why this over consolidating to one thread/loop:** a single shared loop
would need `publish()` (called from arbitrary sync threads) to hand off via
`loop.call_soon_threadsafe` instead of `queue.Queue.put_nowait`, and the
dispatcher's callback-invocation loop would need restructuring from a
blocking `while True: self.events.get()` into real async tasks. That's a
bigger, higher-risk change to a mechanism (`publish`/dispatch) that isn't
broken and isn't what the proposal identified as the problem. The chosen
design fixes exactly the sync-client issue with a small change to how the
receiver thread is driven (`receiver()` → `async_to_sync(receiver)()`) and a
type change to the receiver contract, leaving the dispatcher, its queue, and
its thread-safety guarantees completely untouched. Thread-count parity with
`EventRuntime` is not a goal in itself — `EventRuntime` exists to multiplex
*many* configured event queues onto one shared loop; `_QueueObservers`
already scopes one receiver to one queue, so there is no multiplexing
benefit to gain from sharing a loop here. (A separate, dependent change,
`add-async-runtime`, later revisits this scoping — see its proposal for why
"one receiver to one queue" doesn't hold today across threads — but that is
explicitly out of scope for this change.)

## Risks / Trade-offs

- [Risk] `async_to_sync(receiver)()` runs the coroutine to completion on the
  receiver thread — the same one-time-per-thread-lifetime cost as today's
  code, since the old code also only ran once per thread. → No mitigation
  needed; no change in overhead, and it's the same bridge already used
  elsewhere in this codebase for this exact direction.
- [Risk] If the Redis connection drops, `aobserve()` returns (as `observe()`
  does today when `pubsub.listen()`'s generator ends), `async_to_sync`
  returns, and `_run_receiver`'s existing `finally` clears `self.receiver`
  and logs a warning — matching current behavior. There is no
  reconnect/retry today and this change does not add one. → Out of scope;
  note as a possible follow-up if reconnection is desired later.
- [Risk] Two threads per observed queue remains the on-disk reality (not
  reduced by this change), so the "two mechanisms for one job" framing from
  the proposal is only partially resolved (the sync client is gone; the
  second thread is not). → Accepted trade-off per Non-Goals; the dependent
  `add-async-runtime` change revisits this independently.
- [Risk] Existing tests that mock/monkeypatch the synchronous
  `_observer_redis_client`/`observe()` call shape will need updating to the
  async `aobserve()` signature. → Covered in tasks.md as verification work.

## Migration Plan

No data migration. This is an in-process concurrency change with no stored
state, wire-format, or public-API impact — `queue_observer()` callers are
unaffected. Roll out as a normal code change behind the existing test suite;
no feature flag or staged rollout needed. Rollback is a plain revert.
