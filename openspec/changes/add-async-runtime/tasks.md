## 1. Alias-scoped observer registry

- [ ] 1.1 In `django_queue/observers.py`, replace `_observers_for(queue)`'s
      instance-attribute lookup (`queue._lifecycle_observers`) with a
      module-level, alias-keyed store (`_observers_by_alias: dict[str,
      _QueueObservers]`), guarded by a lock, created once per alias
      regardless of which queue instance or thread first needs it.
- [ ] 1.2 Update `queue_observer`'s direct-call registration path, and any
      other internal caller of `_observers_for`, to resolve through the
      queue's `queue_name` (alias) rather than the queue instance.
- [ ] 1.3 Confirm `_QueueObservers.publish()`'s existing thread-safety
      contract (callable from arbitrary threads, including memory-backend
      callers with no receiver) is unaffected by the alias-keyed lookup.

## 2. AsyncQueueRuntime

- [ ] 2.1 Create `django_queue/async_queue_runtime.py` with
      `AsyncQueueRuntime`, structurally mirroring `EventRuntime`
      (`django_queue/event_runtime.py`): `_lock`, `_loop`, `_thread`,
      `_ready`, a per-alias task dict, `start(queues)`, `_start()`,
      `_run_loop()`, per-alias idempotent task scheduling, task-done
      handling, `shutdown()`.
- [ ] 2.2 `start(queues)` iterates configured aliases, filters for
      `AsyncQueue` instances with at least one registration (direct or
      pending decorator) for that alias, and schedules a receiver task per
      alias via `loop.call_soon_threadsafe`, guarded idempotently (`if alias
      in self._tasks: return`), mirroring
      `EventRuntime._start_worker`.
- [ ] 2.3 The receiver task body calls `await
      self._provider.aobserve(on_snapshot)` (from `fix-redis-observer`)
      directly on the shared loop — no `asyncio.run()`, no
      `async_to_sync()`.
- [ ] 2.4 Handle task failure via `add_done_callback`, mirroring
      `EventRuntime._worker_done`: pop from the per-alias task dict, log any
      exception via `task.result()`.
- [ ] 2.5 `shutdown()` cancels all receiver tasks, closes the loop, and
      clears state — mirrors `EventRuntime.shutdown()`. It does not touch
      dispatcher threads (unchanged, out of scope per design.md Non-Goals).
- [ ] 2.6 Add the module-level singleton `async_queue_runtime =
      AsyncQueueRuntime()`.

## 3. Wire the runtime into startup

- [ ] 3.1 In `django_queue/apps.py`, call
      `async_queue_runtime.start(initialise_queues())` alongside the
      existing `event_runtime.start(initialise_queues())`, in both
      `DjangoQueueConfig.ready()` and the `request_started` receiver
      (`_start_event_runtime_on_request`, extended or paralleled to also
      start the async-queue runtime).
- [ ] 3.2 Remove `_QueueObservers._run_receiver`'s per-instance
      `threading.Thread` path for Redis-backed queues now that the runtime
      hosts the receiver task (`django_queue/observers.py` `register()`).

## 4. Decorator registration API

- [ ] 4.1 Change `queue_observer`'s signature to `queue_observer(queue_name,
      callback=None, *, entry_id=None)`; when `callback` is provided, keep
      today's exact behavior unchanged (immediate registration, returns a
      `QueueSubscription`).
- [ ] 4.2 When `callback` is `None`, return a decorator that performs no
      I/O: validate `queue_name`/`entry_id` eagerly (matching
      `queue_listener`'s validation-at-decoration-time in
      `django_queue/listeners.py`), append to the module-level, alias-keyed
      pending-registration store, and return the original callable
      unchanged.
- [ ] 4.3 Attach an immediately-usable unsubscribe handle,
      `fn._queue_observer_subscription`, at decoration time. Calling
      `.unsubscribe()` before activation marks the pending entry cancelled;
      calling it after activation delegates to the real `QueueSubscription`.
- [ ] 4.4 In the alias-scoped registry's activation path (invoked from
      `AsyncQueueRuntime.start()`), for each alias with pending decorator
      registrations, replay the existing direct-call registration logic
      (`configured_queue.list()` snapshot fetch,
      `_QueueObservers.register`/`activate`) for each non-cancelled pending
      entry, and bind the resulting `QueueSubscription` into that entry's
      handle.
- [ ] 4.5 Confirm decoration-time validation (`queue_name` non-empty string,
      `entry_id` UUID-or-`None`) raises at decoration time, not at
      activation time, so a misconfigured decorator fails fast at import.

## 5. Verification

- [ ] 5.1 Add a test registering an `async def` callback via the decorator
      calling convention, `@queue_observer(queue_name)`, and confirming
      it's invoked. (The direct-call form,
      `queue_observer(queue_name, callback)`, is now covered by
      `fix-redis-observer` — this only needs the decorator-specific path.)
- [ ] 5.2 Add a test simulating two threads each registering an observer for
      the same alias for the first time, confirming both are served by one
      runtime-hosted receiver and no second backend connection is created
      (validates the "Serve concurrent registrations for one alias from a
      single runtime" scenario in
      `openspec/changes/add-async-runtime/specs/completion-notifications/spec.md`).
- [ ] 5.3 Add a test confirming a module defining a decorated observer can
      be imported without querying any queue backend or opening a
      connection before the runtime starts (validates "Import a module
      defining a decorated observer").
- [ ] 5.4 Add a test confirming a decorator-registered observer becomes
      active and receives snapshots once the runtime starts (validates
      "Activate a decorator-registered observer at runtime start").
- [ ] 5.5 Add a test unsubscribing a decorator-registered observer before
      runtime start, confirming it never activates (validates "Unsubscribe a
      decorator-registered observer").
- [ ] 5.6 Run the full test suite, updating any test that reaches into
      `queue._lifecycle_observers` directly (now alias-keyed, not
      instance-keyed) or otherwise assumes the old per-instance
      receiver-thread shape.
- [ ] 5.7 Run against a real Redis instance to confirm end-to-end lifecycle
      delivery still works through the runtime-hosted receiver (mirrors
      `fix-redis-observer`'s task 3.1).

## 6. Docs

- [ ] 6.1 Update `README.md`'s `queue_observer` example/section to document
      the decorator calling convention alongside the existing direct call.
- [ ] 6.2 Check `README.md`/architecture docs for any description of the
      observer receiver using a dedicated per-queue thread, and update to
      describe the shared runtime.
