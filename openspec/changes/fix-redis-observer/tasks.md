## 1. Redis provider: async aobserve()

- [x] 1.1 In `django_queue/backends/redis/provider.py`, add
      `async def aobserve(self, on_snapshot) -> None` to `QueueProviderRedis`,
      using `async_redis.from_url(self._redis_url)` (the module's
      existing `redis.asyncio` import) instead of `_observer_redis_client()`.
- [x] 1.2 Replace `pubsub.listen()` (blocking generator) with
      `async for message in pubsub.listen():`, keeping the existing
      `message["type"] != "message"` filter and the
      `entry_class.from_dict(json.loads(...))` decode-and-log-on-failure
      logic unchanged.
- [x] 1.3 Wrap the loop in `try/finally` with `await pubsub.aclose()` and
      `await client.aclose()` (not the deprecated `.close()`).
- [x] 1.4 Remove the old synchronous `observe()` method entirely — it has
      exactly one caller (Task 2.2 below), so no compatibility shim is
      needed.
- [x] 1.5 Remove `_observer_redis_client()` once nothing references it.

## 2. Receiver contract and thread driver

- [x] 2.1 In `django_queue/backends/base.py`, change `_observer_receiver`'s
      return type from `Callable[[], None] | None` to
      `Callable[[], Awaitable[None]] | None`.
- [x] 2.2 In `django_queue/backends/redis/redisqueue.py:38`, update the
      `_observer_receiver` override to return a zero-arg callable bound to
      `on_snapshot` that returns a coroutine (e.g.
      `functools.partial(self._provider.aobserve, on_snapshot)` or an
      equivalent zero-arg async wrapper), matching the new `aobserve`
      signature from Task 1. Used `functools.partial` specifically — a plain
      `lambda: self._provider.aobserve(on_snapshot)` is not itself a
      coroutine function, which made `asgiref.sync.async_to_sync` warn at
      runtime ("passed a non-async-marked callable"); `functools.partial`
      over an `async def` method is recognized correctly.
- [x] 2.3 In `django_queue/observers.py`, change `_run_receiver` to drive the
      receiver with `async_to_sync(receiver)()` (import `async_to_sync` from
      `asgiref.sync`, matching its use in `backends/base.py` and
      `clock.py`) instead of calling `receiver()` directly. Keep its thread
      naming, daemon flag, and the `try/except/finally` (log failure, clear
      `self.receiver`) unchanged.

## 3. Verification

- [x] 3.1 Run `tests/test_redis_entries.py::test_pruning_publishes_a_terminated_snapshot_to_an_observer`
      against a real Redis instance to confirm the end-to-end receiver
      thread still delivers lifecycle snapshots.
- [x] 3.2 Run `tests/test_entry_queue.py::test_observer_receiver_clears_its_queue_registration_on_exit`
      and confirm `_run_receiver` still clears `self.receiver` when the
      receiver raises (now raised before/through `async_to_sync`). Updated
      the test's fake receiver from a mismatched-arity sync lambda to a real
      zero-arg `async def` that raises, matching the actual contract and
      removing an `async_to_sync` warning the old fake triggered.
- [x] 3.3 Run the full test suite (`pytest`) to catch any other test that
      mocks/monkeypatches `observe()`/`aobserve()`, `_observer_redis_client`,
      or `_observer_receiver` with sync assumptions, and update it to the
      async signature. 1516 passed, 2 skipped, zero warnings.
- [x] 3.4 Grep the codebase and docs for `_observer_redis_client` and for a
      bare `.observe(` call (not `aobserve`) and confirm no remaining
      references outside this change's diff. None found.
- [x] 3.5 Add a test registering an `async def` callback via
      `queue_observer(queue_name, callback)` and confirming it is invoked —
      exercises the dispatcher's existing but previously untested
      `inspect.iscoroutinefunction` branch (`django_queue/observers.py`).
      Added `test_observer_dispatches_an_async_callback` in
      `tests/test_entry_queue.py`.

## 4. Docs

- [x] 4.1 Check `README.md`/architecture docs for any description of the
      Redis observer using a synchronous client or thread, and update if
      present. The existing description (README.md around line 287) is
      already implementation-agnostic ("blocks in Pub/Sub", "one daemon
      receiver") and stays accurate; no change needed.
