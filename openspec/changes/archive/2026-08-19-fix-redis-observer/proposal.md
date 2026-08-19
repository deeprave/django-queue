## Why

The Redis backend is async-native everywhere except one place:
`QueueProviderRedis.observe()` (`django_queue/backends/redis/provider.py:596`)
opens an isolated *synchronous* `redis.Redis` client
(`_observer_redis_client`) and blocks on `pubsub.listen()`. This is the only
synchronous Redis usage anywhere in the backend — every other operation
(`aadd`, `aget`, `apoll`, `aclaim`, `aack`, `apublish`, ...) already uses
`redis.asyncio` and follows the `a`-prefix naming convention, with no
synchronous counterpart at all. `observe()` is the sole exception to both:
the only method without an `a` prefix, and the only synchronous one.

`observe()` runs on its own dedicated `receiver` thread (`observers.py`'s
`_QueueObservers._run_receiver`), separate from the `dispatcher` thread that
already owns a private `asyncio` event loop
(`_run_dispatcher`: `asyncio.new_event_loop()`) purely to bridge callback
invocation via `sync_to_async`. So one queue's lifecycle observation already
spins up two OS threads, one of which exists only because the receiver is
written as a blocking synchronous loop instead of an `async for` over
`redis.asyncio`'s pubsub. Consolidating onto one async-native receiver removes
the blocking sync client, removes the second thread, and matches the pattern
the rest of the backend already follows.

## What Changes

- Add `QueueProviderRedis.aobserve()`, an async replacement for `observe()`
  using `redis.asyncio`'s pubsub (`async for message in pubsub.listen()`),
  named with the same `a`-prefix convention every other provider method
  already uses. Remove the old synchronous `observe()` outright — it has
  exactly one caller, so no compatibility shim is needed.
- Change the `_observer_receiver` contract (`backends/base.py`,
  `backends/redis/redisqueue.py`) from a synchronous `Callable[[], None]` to
  an async `Callable[[], Awaitable[None]]`, with the Redis override returning
  a callable bound to `aobserve` instead of `observe`.
- Update `_QueueObservers._run_receiver` (`observers.py`) to drive the
  receiver with `async_to_sync(receiver)()` instead of calling it directly —
  the same `asgiref.sync.async_to_sync` bridge this codebase already uses at
  every other point where sync code calls into async (`BaseQueue.close()`,
  `clock.py`'s `now()`, `provider.py`'s `_anow_and_close`). `asyncio.run()` is
  not used here: it appears exactly once in this codebase, at
  `runqueues.py`'s single top-level process entry point, and stays reserved
  for that role rather than becoming a second, inconsistent bridging idiom.
  The `dispatcher` thread, its `queue.Queue` hand-off, and `sync_to_async`
  callback bridging are unchanged — this is scoped to removing the sync
  Redis client, not to redesigning the observer dispatch architecture.
- Remove the now-unused `_observer_redis_client` sync-client helper once
  `observe()` is gone.

## Capabilities

No spec-level behavior changes: the public `queue_observer` API, delivery
ordering, bootstrap-from-retained-entries behavior, and best-effort failure
handling described in `completion-notifications` are unchanged. This is an
internal concurrency/implementation refactor of how the Redis backend
receives lifecycle notifications, not a change to what observers receive or
when. `skip_specs: true` is set in `.openspec.yaml` accordingly.

## Impact

- `django_queue/backends/redis/provider.py`: `observe()` removed,
  `aobserve()` added, `_observer_redis_client()` removed.
- `django_queue/backends/redis/redisqueue.py`: `_observer_receiver()` override.
- `django_queue/backends/base.py`: `_observer_receiver()` base contract/type.
- `django_queue/observers.py`: `_QueueObservers._run_receiver`.
- Tests exercising the Redis observer receiver (thread-based mocking may need
  to become coroutine-based).
- No change to the memory backend, which has no cross-process receiver
  (`_observer_receiver` returns `None` by default).
