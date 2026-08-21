## 1. Alias-scoped observer registry

- [x] 1.1 In `django_queue/observers.py`, replace `_observers_for(queue)`'s
      instance-attribute lookup (`queue._lifecycle_observers`) with a
      module-level, alias-keyed store (`_observers_by_alias: dict[str,
      _QueueObservers]`), guarded by a lock, created once per alias
      regardless of which queue instance or thread first needs it.
- [x] 1.2 Update `queue_observer`'s direct-call registration path, and any
      other internal caller of `_observers_for`, to resolve through the
      queue's `queue_name` (alias) rather than the queue instance. No caller
      changes were needed — every caller already just passes a `queue`
      object; the alias resolution is entirely internal to `_observers_for`.
- [x] 1.3 Confirm `_QueueObservers.publish()`'s existing thread-safety
      contract (callable from arbitrary threads, including memory-backend
      callers with no receiver) is unaffected by the alias-keyed lookup.
      Confirmed by the full test suite passing unchanged.

  Added `test_observers_share_state_across_instances_of_one_alias` to
  `tests/test_entry_queue.py` (TDD: written first, proven to fail against
  the old per-instance lookup, then passed after the fix) — two separate
  queue instances for the same alias now share one `_QueueObservers`.

  Discovered and fixed a real test-isolation gap this change introduced:
  `_observers_by_alias` is process-global, so tests reusing the alias
  `"requests"` (common across this test suite) now leak observer state
  between tests. Fixed by clearing the alias's entry in the `observer_queue`
  fixture (`tests/test_entry_queue.py`) and in
  `test_pruning_publishes_a_terminated_snapshot_to_an_observer`
  (`tests/test_redis_entries.py`, the only other test touching
  `queue_observer`/`_observers_for`) — opt-in per-test cleanup, not an
  autouse fixture, per the same "not autouse by default" preference used
  for the runtime-suppression fixture in task 5.
  `django_queue/backends/base.py`'s now-obsoleted `_initialise_observers`,
  `_lifecycle_observer_lock`, and `_lifecycle_observers` (and the now-unused
  `threading` import) were removed, along with their three call sites in
  `memory/memqueue.py`, `memory/mempqueue.py`, and `redis/redisqueue.py` —
  this is a refactor and dead code obsoleted by it is removed, not kept.

## 2. Rename EventRuntime to QueueRuntime and add the receiver task kind

- [x] 2.1 `git mv django_queue/event_runtime.py django_queue/queue_runtime.py`.
      Rename `EventRuntime` → `QueueRuntime` and the module-level singleton
      `event_runtime` → `queue_runtime` throughout the file.
- [x] 2.2 Rename `self._workers` → `self._tasks` (now shared by both event
      workers and observer receivers, keyed by alias — an alias is either an
      `EventQueue` or an `AsyncQueue`, never both, so one dict is
      unambiguous). Update `_start_worker`, `_worker_done`, `shutdown()`, and
      `_run_loop()`'s cleanup accordingly.
- [x] 2.3 Added `_start_receiver(alias, queue)`, mirroring the existing
      `_start_worker`'s idempotent-guard shape. **Deviation from the written
      task**: the task body calls `queue._observer_receiver(on_snapshot)`
      (the existing `AsyncQueue` hook `_QueueObservers.register()` already
      used, returning a bound `functools.partial(self._provider.aobserve,
      on_snapshot)` for Redis, `None` for memory backends) rather than
      calling `queue._provider.aobserve(on_snapshot)` directly. Calling the
      provider's `aobserve` unconditionally would break memory-backend
      queues, which have no `_provider.aobserve` at all — the existing
      `_observer_receiver` abstraction is exactly what already handles "does
      this backend have a receiver" correctly, and reusing it avoids
      duplicating that logic in the runtime.
- [x] 2.4 Added `_receiver_done(alias, task)`, mirroring `_worker_done`: pops
      from `self._tasks`, logs any exception via `task.result()` on failure,
      never re-raises into the loop.
- [x] 2.5 `start(queues)` walks `queues` once and, per alias, schedules
      whichever task kind applies: `_start_worker` for an `EventQueue`
      alias, `_start_receiver` for an `AsyncQueue` alias with at least one
      **direct** registration. Pending-decorator registrations (task 4) do
      not exist yet as of this task — `start()`/`_alias_has_observer_registration`
      will need a follow-up touch once task 4's pending-registration store
      exists, to also recognise an alias with only a pending decorator
      registration as needing a receiver.
- [x] 2.6 `shutdown()` cancels both task kinds uniformly via one
      `self._tasks` dict; `_run_loop()`'s cleanup (`queue.aclose()` for
      every queue in `self._queues`) closes both event and async queues
      since `self._queues` is now typed `dict[str, EventQueue | AsyncQueue]`.

  Added `_alias_has_observer_registration(alias)` to `django_queue/observers.py`
  — not in the original task list, but required by 2.5: `start()` needs to
  know whether an alias has any registration *without* creating a
  `_QueueObservers` entry as a side effect (which `_observers_for` does),
  since that would defeat "no-op when nothing is registered" for every
  configured alias the runtime ever looks at. TDD: 4 tests added to
  `tests/test_entry_queue.py` covering no-registration, no-side-effect,
  has-registration, and after-unsubscribe cases.

  Also fixed, in `django_queue/backends/base.py`: `_observer_receiver`'s
  return type annotation was `Callable[[], Awaitable[None]] | None`, too
  loose for `asyncio.create_task(receiver())`, which requires a `Coroutine`.
  Tightened to `Callable[[], Coroutine[Any, Any, None]] | None` — the real
  return value already was coroutine-producing; the annotation was wrong.

## 3. Wire the runtime into startup

- [x] 3.1 **Revised after implementation, per user simplification request**:
      originally wired `queue_runtime.start(...)` behind the
      `request_started` signal (renaming `_start_event_runtime_on_request` →
      `_start_queue_runtime_on_request`), mirroring the old `EventRuntime`
      wiring exactly. After task 3.2's fix introduced a second entry point
      (`create_connection`), the user asked to simplify: the thread itself
      should have exactly one start path, gated only on whether `QUEUES` is
      non-empty, not on which of several call sites happens to fire first.

      Added `QueueRuntime.start_thread()` — the sole method that creates the
      background thread, idempotent, no task scheduling. `django_queue/apps.py`'s
      `ready()` now calls `initialise_queues()`, and if the returned
      registry's `.settings` is non-empty, calls `queue_runtime.start_thread()`
      then `queue_runtime.start(registry)` directly — synchronously, at
      process start. The `request_started` signal is no longer connected;
      `_start_queue_runtime_on_request` was removed. `start()`/`start_one()`
      no longer create the thread themselves — they assume it already
      exists (via `ready()`) and only schedule tasks, no-op-ing safely if
      the loop isn't up.
- [x] 3.2 **Found and fixed a real bug not anticipated by design.md**: calling
      `queue_runtime.start(self)` unconditionally from inside
      `create_connection` recurses infinitely. `BaseConnectionHandler.__getitem__`
      only caches an alias's queue *after* `create_connection` returns, so
      `start()`'s own `queues[alias]` loop re-enters `create_connection` for
      the alias currently being built (and any other not-yet-cached alias),
      which calls `queue_runtime.start(self)` again, forever. Confirmed via
      `RecursionError` when running the existing test suite.

      Fixed by adding `QueueRuntime.start_one(alias, queue)` — schedules the
      task for one already-resolved alias directly, without walking or
      re-resolving other aliases through `queues[alias]`. `start()` and
      `start_one()` share a `_classify`/`_start_classified` helper pair
      rather than duplicating the type/registration-filtering logic.
      `create_connection` calls `queue_runtime.start_one(alias, queue)` with
      the queue it just built, not `queue_runtime.start(self)`. Also fixed
      the same gap on the `connection_scope == "process"` cache-hit path,
      which previously returned early without ever scheduling that alias's
      task.

      Also tightened `QueueRegistry._process_queues`'s type from
      `dict[str, object]` to `dict[str, AsyncQueue | EventQueue]` — required
      for `ty` to prove `start_one`'s `queue` argument type at the
      cache-hit/cache-miss join point; `object` was too loose for what the
      dict actually always holds.
- [x] 3.3 Removed `_QueueObservers`'s `self.receiver` attribute and
      `_run_receiver` entirely, and the block in `register()` that started a
      per-instance receiver thread — done as part of task 4, alongside
      `register()`'s other changes, as planned. The old
      `test_observer_receiver_clears_its_queue_registration_on_exit` test
      (which tested `_run_receiver` directly) was removed; its concern —
      a failed receiver clears its tracking entry — is now covered by
      `QueueRuntime._receiver_done`, exercised indirectly by the real-Redis
      end-to-end test and directly by unit tests on `QueueRuntime`.

## 4. Decorator registration API

- [x] 4.1 Changed `queue_observer`'s signature to `queue_observer(queue_name,
      callback=None, *, entry_id=None)`; when `callback` is provided,
      registration and activation happen immediately via `_register_now`
      (today's exact behavior, refactored out unchanged), returning a
      `QueueSubscription`.
- [x] 4.2 When `callback` is `None`, `queue_observer` returns a `decorator`
      closure calling `_register_deferred`, which performs no I/O: it only
      validates shape (non-empty `queue_name`, `entry_id` UUID-or-`None`,
      both checked up front in `queue_observer` itself before branching) and
      appends to `_pending_by_alias`, a module-level alias-keyed dict.
      **Correction to the written task**: does *not* also mirror
      `queue_listener`'s eager `queues[queue_name]` existence check —
      checked `queue_listener` (`django_queue/listeners.py:41`) and found it
      *does* query the registry at decoration time, which would violate the
      spec's explicit "Import a module defining a decorated observer" ...
      "without querying any queue backend or opening any backend connection"
      scenario. Only the decorator-factory *shape* is mirrored, not that
      eager check.
- [x] 4.3 `DecoratorSubscription` (attached as
      `fn._queue_observer_subscription`) provides `.unsubscribe()`: before
      activation (`_pending.subscription is None`), marks the pending entry
      `cancelled`; after activation, delegates to the real
      `QueueSubscription`.
- [x] 4.4 `_activate_pending_for(alias)` (called from `QueueRuntime._classify`,
      before an alias is added to `receiver_queues` — see the note on task 2
      about why activation must run synchronously, off the event loop, not
      inside `_start_receiver`) replays `_register_now` for each pending,
      non-cancelled, not-yet-activated entry, binding the resulting
      `QueueSubscription` into `pending.subscription`.

      **Found and fixed a second real recursion bug**, structurally similar
      to task 3.2's: `_register_now` calls `queue_runtime.start_one(...)`
      after registering (needed so a *direct* `queue_observer()` call also
      gets a receiver scheduled — not originally called out as a task, see
      below), which re-enters `_classify` → `_activate_pending_for` for the
      same alias, which would re-activate the same still-`subscription is
      None` pending entry, calling `_register_now` again, forever. Fixed by
      adding an `activating: bool` flag to `_PendingRegistration`, set
      *before* calling `_register_now`, so the re-entrant activation check
      correctly treats an in-flight activation as already handled. Confirmed
      via `RecursionError` before the fix, TDD tests passing after.

      **Also found and added, not in the original task list**: `_register_now`
      (direct-call registration) did not previously trigger the runtime at
      all — only `create_connection`/`request_started` did, at process
      startup. A direct `queue_observer("alias", callback)` call can happen
      at *any* time (not just before the runtime starts), so it must also
      call `queue_runtime.start_one(alias, queue)` after registering, or a
      receiver is never scheduled for an alias whose only registration
      arrived after the runtime had already looked at (and skipped) that
      alias. Discovered via a real, non-contrived test failure
      (`test_pruning_publishes_a_terminated_snapshot_to_an_observer` in
      `tests/test_redis_entries.py`, which registers via direct call after
      the queue is already resolved).
- [x] 4.5 Decoration-time validation (`queue_name` non-empty string,
      `entry_id` UUID-or-`None`) happens in `queue_observer` itself, before
      branching into `_register_now`/the `decorator` closure — raises at
      decoration time for both calling conventions, confirmed by
      `test_decorator_form_records_without_querying_a_backend` (which also
      confirms zero registry access) and the existing direct-call error
      tests.

## 5. Test infrastructure: runtime-suppression fixture

- [x] 5.1 Added `no_runtime_startup(monkeypatch)` to `tests/conftest.py` —
      function-scoped, **not autouse**, monkeypatching `queue_runtime.start_thread`,
      `queue_runtime.start`, and `queue_runtime.start_one` all to no-ops
      (only `start`/`start_one` were in the original design; `start_thread`
      was added after task 3.1's revision, and needs suppressing too, since
      `DjangoQueueConfig.ready()` — exercised directly by a couple of tests —
      now calls it).
- [x] 5.2 Audited every test file resolving a queue through the registry
      (the original six, confirmed via `grep`) for real background-thread
      exposure, **empirically** — not just by inspection: instrumented a
      throwaway pytest plugin counting live `django-queues-*` threads after
      each test's teardown, since a leaked daemon thread doesn't fail a test
      by itself and inspection alone had already missed one case (see
      below). Findings, and the fixture added to each:
      - `test_entry_queue.py`: every test calling `queue_observer`, its
        decorator form, or resolving `queue_observer("events", ...)` against
        an `EventQueue` alias for its `TypeError` — including cases where
        activation never actually reaches `_register_now` and so don't
        leak, confirmed individually rather than added blanket-wide (kept
        those *without* the fixture, so the real fallback path stays
        exercised, per the task's own intent).
      - `test_event_listeners.py`: **found and fixed a real, previously
        unnoticed leak** — `@queue_listener("events")` resolves
        `queues[queue_name]` internally (`listeners.py:41`) for an
        `EventQueue` alias, which unconditionally starts a worker task
        via `create_connection`'s fallback, regardless of `queue_observer`
        involvement (`queue_listener`/`EventQueue` dispatch is unrelated to
        observers). Both tests in the file needed the fixture.
      - `test_configured_queues.py`: same `EventQueue`-resolution root
        cause — `test_configured_memory_event_queue_is_shared_across_threads`
        and `test_passes_the_configured_entry_class_to_a_redis_event_provider`
        (via `initialise_queues`, which force-resolves every alias) both
        needed it.
      - `test_redis_entries.py`, `test_runqueues.py`: audited, confirmed
        clean — `runqueues` tests use only `AsyncQueue` backends and never
        call `queue_observer`; `test_redis_entries.py`'s one observer test
        is deliberately real (see 5.3-adjacent note below) rather than
        suppressed.
      - `test_event_runtime.py`: not audited here — task 6.1's rewrite.

      **Found a second real gap along the way, not anticipated by
      design.md**: `test_pruning_publishes_a_terminated_snapshot_to_an_observer`
      (`test_redis_entries.py`) legitimately needs the real runtime (that's
      the point of the test), but nothing stopped its receiver task once the
      test finished — `subscription.unsubscribe()` only removes the
      registration, not the runtime's task, so it kept running against the
      module-scoped Redis testcontainer after that container was torn down.
      Discussed with the user; added `QueueRuntime.stop_one(alias, timeout=5.0)`
      — cancels and, unlike a bare `task.cancel()`, synchronously *awaits*
      one alias's task via `asyncio.run_coroutine_threadsafe`, so a caller
      gets a real guarantee the task's own cleanup (e.g. `aobserve()`'s
      `finally: pubsub.aclose(); client.aclose()`) has finished, not just
      been requested — before a shared resource like a test container goes
      away. Deliberately scoped to one alias, unlike `shutdown()`: leaves
      the shared thread/loop and every other alias's task untouched, so
      other tests relying on the singleton are unaffected. Verified this
      distinction concretely (`shutdown()` would have permanently poisoned
      `_closed` for the rest of the test process). The Redis test now calls
      it in its `finally` block alongside `unsubscribe()`.
- [x] 5.3 `test_create_connection_starts_the_runtime_for_an_event_queue` and
      `test_create_connection_starts_the_runtime_for_an_observed_async_queue`
      (`tests/test_configured_queues.py`) — the latter needed
      `monkeypatch.setattr(django_queue, "queues", handler)`, since
      `queue_observer` resolves through the module-level `django_queue.queues`
      global, not whatever local `QueueRegistry` a test constructs (a real
      mistake caught by running the test, not assumed). Both poll
      `queue_runtime._tasks` with a timeout via a small `_wait_for_task`
      helper, since scheduling onto the loop via `call_soon_threadsafe` is
      asynchronous with respect to the calling thread — an immediate,
      non-waiting assertion is a race, confirmed by a real failure before
      the fix.
- [x] 5.4 `test_create_connection_does_not_double_schedule_a_started_alias` —
      resolves the same alias twice, confirms the second resolution reuses
      the exact same `asyncio.Task` object rather than creating a new one.

## 6. Verification

- [x] 6.1 Done earlier, alongside the thread-startup simplification (group 3
      revision) — `tests/test_queue_runtime.py` renamed and every
      `EventRuntime`/`event_runtime` reference updated to `QueueRuntime`/
      `queue_runtime`, plus `self._workers` → `self._tasks` and the
      `ready()`-related test rewritten for the new `start_thread()`/`start()`
      split (`_start_event_runtime_on_request` no longer exists).
- [x] 6.2 `test_runtime_hosts_a_worker_and_a_receiver_concurrently`
      (`tests/test_queue_runtime.py`) — one `QueueRuntime()` instance, an
      `EventQueue` alias with a listener and a Redis-backed `AsyncQueue`
      alias with an observer, confirms both `runtime._tasks` entries exist
      and the event listener still receives its payload while the receiver
      is also running.
- [x] 6.3 `test_decorator_form_dispatches_an_async_callback`
      (`tests/test_entry_queue.py`) — `@queue_observer("requests")` on an
      `async def` callback, activated and confirmed delivered.
- [x] 6.4 `test_two_threads_registering_for_one_alias_share_the_receiver`
      (`tests/test_queue_runtime.py`) — two real `threading.Thread`s both
      call `queue_observer("shared", ...)` for the same alias concurrently;
      confirms both registrations succeed with no error and exactly one
      receiver task exists for the alias afterward.
- [x] 6.5 `test_decorator_form_records_without_querying_a_backend`
      (`tests/test_entry_queue.py`, written during task 4) — a
      registry subclass tracking every `__getitem__` call confirms zero
      backend access at decoration time.
- [x] 6.6 `test_decorator_form_activates_on_runtime_start`
      (`tests/test_entry_queue.py`, written during task 4).
- [x] 6.7 `test_unsubscribing_before_activation_prevents_it`
      (`tests/test_entry_queue.py`, written during task 4).
- [x] 6.8 Full suite run: 1545 passed, 2 skipped, zero warnings
      (`-Walways -Werror`). Confirmed zero remaining references anywhere to
      `_lifecycle_observers`, `EventRuntime`, or `event_runtime` (`grep` across
      `django_queue/` and `tests/`).
- [x] 6.9 `test_pruning_publishes_a_terminated_snapshot_to_an_observer`
      (`tests/test_redis_entries.py`) runs against a real Redis
      testcontainer and passes — the runtime-hosted receiver delivers a
      `TERMINATED` snapshot end-to-end.

## 7. Docs

- [x] 7.1 Added a decorator-form example to `README.md`'s Lifecycle
      observation section, alongside the existing direct call, with a note
      on `_queue_observer_subscription` and deferred activation.
- [x] 7.2 Updated `README.md` in three places: the queue-kind comparison
      table (was "Django starts the event runtime when the process first
      handles a request"), the event-queue section's runtime paragraph (was
      "one process-local event runtime... on first HTTP request... one
      worker task per event queue"), and the observer section's old
      "first Redis observer... starts one daemon receiver" paragraph — all
      now describe the single `QueueRuntime` thread/loop, started once at
      process startup when `QUEUES` is non-empty, shared by event workers
      and observer receivers alike. Checked `AGENTS.md`, `CHANGELOG.md`,
      and the `demo_eq`/`demo_aq`/`formal` READMEs for `EventRuntime`/
      `event_runtime`/threading-model mentions — none found, no changes
      needed there.
