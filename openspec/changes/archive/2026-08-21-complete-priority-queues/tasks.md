## 1. `QueueEntry` schema

- [x] 1.1 In `django_queue/entries.py`, add `priority: int = 0` to
      `QueueEntry` (after `timeout_seconds`, so every positional/keyword
      caller with `timeout_seconds` already named stays valid).
- [x] 1.2 Add `priority: int = 0` to `QueueEntry.create()`'s parameters and
      pass it through to the constructed entry.
- [x] 1.3 Confirm `to_dict`/`from_dict` round-trip `priority` with no
      further change (both iterate `fields(self)` generically) — add a
      direct test if the existing round-trip tests don't already cover an
      added field generically.
      Added `test_defaults_priority_to_zero_when_enqueued_without_one` and
      `test_round_trips_a_nonzero_priority`; updated
      `test_serialises_a_queued_entry_as_a_complete_json_record`'s exact-dict
      assertion for the new field. TDD-verified: all three failed before the
      schema change, pass after.

## 2. Provider: tracked-entry priority storage

- [x] 2.1 In `django_queue/backends/redis/provider.py`, add
      `self._entry_pending_priority_name = f"{self._queue_name}:entries:pending:priority"`
      alongside `self._entry_pending_name`'s existing assignment.
      Also added `self._entry_pending_priority_sequence_name` — see 2.2.
- [x] 2.2 Add `async def apush_priority(self, entry_id, priority) -> None`
      using `zadd(self._entry_pending_priority_name, {entry_id_value: priority})`
      (same encode-then-`zadd` shape as `aadd_priority`, but keyed on the new
      name, storing the entry ID, not an encoded payload).
      Deviation: the score is not bare `priority` — see task 6.3. `INCR`s
      `self._entry_pending_priority_sequence_name` and folds the result into
      the score's low bits (`priority * 2**32 - sequence`) so equal
      priorities break ties by arrival order via a Redis-wide monotonic
      counter, not by `zrevrange`'s member-value tie-break. See design.md,
      "Equal-priority entries dispatch in arrival order".
- [x] 2.3 Add `async def apop_priority(self) -> QueueEntry`: `zrevrange` +
      `zrem` the highest-scored member off `_entry_pending_priority_name`
      (mirroring `aget_priority`'s shape), decode it to a UUID, then
      `afind()` it — matching `apop`'s existing "pop ID, then look up"
      pattern. Raise `QueueEmptyException` when nothing is pending.
- [x] 2.4 Add `async def adiscard_priority(self, entry_id) -> None`
      (`zrem` on `_entry_pending_priority_name`), mirroring `adiscard`, for
      the `QUEUED`→`FAILED` cleanup path in `_areplace_entry`.
- [x] 2.5 In `django_queue/backends/memory/provider.py`, add
      `self._pending_priority: queue.PriorityQueue = queue.PriorityQueue(maxsize=maxsize)`
      alongside `self._pending`'s existing assignment.
      Deviation: no `maxsize` — `self._pending` (the plain tracked path) is
      itself unbounded and `apush`/`aenqueue` enforce no capacity limit, so
      giving only the priority variant a cap would be new, undiscussed
      behaviour, not parity with the path it mirrors.
- [x] 2.6 Add `async def apush_priority(self, entry_id, priority) -> None`
      putting `(-int(priority), entry_id)` onto `self._pending_priority`
      (same negation as `aadd_priority`), raising `QueueFullException` on
      `queue.Full`.
      Deviation: pushes a 3-tuple, `(-priority, sequence, entry_id)`, not
      `(-priority, entry_id)` — see task 6.2. A bare 2-tuple ties equal
      priorities by comparing `entry_id` directly, which is not a documented
      ordering contract of `QueueEntry.id`, so a self-owned monotonic
      sequence counter (`self._pending_priority_sequence`) breaks the tie
      instead, guaranteeing arrival order regardless of ID generation
      details.
      Also no `QueueFullException` path, for the same unbounded-queue
      reason as 2.5 — `put_nowait` cannot raise `queue.Full` against an
      unbounded `PriorityQueue`.
- [x] 2.7 Add `async def apop_priority(self) -> QueueEntry`: pop the
      highest-priority `(neg_priority, entry_id)` off `self._pending_priority`
      under `self._lock`, then look up `self._entries[entry_id]` — mirroring
      `apop`'s existing shape and its `QueueEmptyException`/
      `QueueEntryNotFoundError` handling.
- [x] 2.8 Add `async def adiscard_priority(self, entry_id) -> None` removing
      `entry_id` from `self._pending_priority` under `self._lock` (mirroring
      `_remove_pending`'s approach for `self._pending`, adapted for the
      `PriorityQueue`'s internal `queue.queue` list plus
      `self._pending_priority.mutex`).
      Filters `queue.queue` then `heapq.heapify`s it back into heap order —
      unlike `_remove_pending`'s plain-list splice (`_pending` is a FIFO/LIFO
      `Queue`/`LifoQueue`, order-preserving on removal), `PriorityQueue`'s
      internal list is a binary heap; a plain filter without re-heapifying
      would leave the heap invariant broken and silently corrupt future pop
      order.

## 3. `AsyncQueue` base: overridable push/pop hooks

- [x] 3.1 In `django_queue/backends/base.py`, add protected hooks to
      `AsyncQueue`:
      `async def _apush(self, entry: QueueEntry) -> None` calling
      `await self._provider.apush(entry.id)`, and
      `async def _apop(self) -> QueueEntry` calling
      `return await self._provider.apop()`.
- [x] 3.2 Change `aenqueue()` (`base.py:271`) to call
      `await self._apush(entry)` instead of
      `await self._provider.apush(entry.id)` directly.
- [x] 3.3 Change `adequeue()` (`base.py:311`) to call
      `return await self._apop()` instead of
      `return await self._provider.apop()` directly.
- [x] 3.4 Change `_areplace_entry`'s `QUEUED`→`FAILED` cleanup
      (`base.py:395-399`) to call a matching
      `async def _adiscard(self, entry_id: UUID) -> None` hook (default
      `await self._provider.adiscard(entry_id)`) instead of calling
      `self._provider.adiscard(entry_id)` directly, so the priority override
      in Task 4/5 can clean up its own pending store.
- [x] 3.5 (Not in original plan — raised and confirmed with the user during
      implementation.) `AsyncQueue.aenqueue()`/`enqueue()` need a `priority`
      parameter, but the sync `enqueue()` wrapper and the abstract
      `aenqueue()` declaration live on the shared `BaseQueue`, also used by
      `EventQueue`. Confirmed with the user: added `priority: int = 0` to
      `BaseQueue.enqueue()`/`aenqueue()`'s shared signature; `EventQueue.
      aenqueue()` accepts and ignores it (documented in its docstring) rather
      than getting its own diverging signature.
      A later review pass (cursor.json, CR-2) found this decision was never
      reflected in `specs/queue-entries/spec.md`, whose "Enqueue an
      identified entry with a dispatch priority" requirement read as if
      every entry-oriented enqueue, `EventQueue` included, must persist the
      value -- contradicting this task's own decision. Confirmed with the
      user: narrowed the spec requirement to `AsyncQueue` explicitly and
      added an `EventQueue`-ignores-priority scenario, rather than changing
      `EventQueue`'s behaviour to match the spec's prior (too broad)
      wording.

## 4. Redis priority queue: route through the tracked hooks

- [x] 4.1 In `django_queue/backends/redis/redispqueue.py`,
      `RedisAsyncPriorityQueue`: override
      `async def _apush(self, entry) -> None` to call
      `await self._provider.apush_priority(entry.id, entry.priority)`.
- [x] 4.2 Override `async def _apop(self) -> QueueEntry` to call
      `return await self._provider.apop_priority()`.
- [x] 4.3 Override `async def _adiscard(self, entry_id) -> None` to
      call `await self._provider.adiscard_priority(entry_id)`.
- [x] 4.4 Confirm `RedisAsyncPriorityQueueJson` needs no changes — it only
      wraps the raw `aadd`/`aget`/`apoll`/`apeek` value API (encode/decode),
      none of which this change touches. Confirmed: it inherits
      `RedisAsyncPriorityQueue`'s hook overrides unchanged.

## 5. Memory priority queue: route through the tracked hooks

- [x] 5.1 In `django_queue/backends/memory/mempqueue.py`,
      `MemoryAsyncPriorityQueue`: override `_apush`, `_apop`,
      and `_adiscard` the same way as Task 4, calling
      `self._provider.apush_priority`/`apop_priority`/`adiscard_priority`.
      `aenqueue`/`adequeue`/`_areplace_entry` are borrowed from
      `MemoryAsyncQueue` by class-level assignment, not inheritance, but
      their `self._apush(...)` calls still resolve against
      `type(self)` at call time, so overriding the hooks here is sufficient
      without touching the borrow list.

## 6. Tests

- [x] 6.1 In `tests/test_entry_queue.py`, replace or extend
      `test_memory_priority_queue_supports_identified_entries` with a real
      ordering assertion: enqueue a low-priority then a high-priority entry
      through `MemoryAsyncPriorityQueue`, dequeue twice, assert the
      high-priority entry comes first.
      Kept the original test (still a valid, narrower assertion) and added
      `test_memory_priority_queue_dispatches_the_tracked_path_in_priority_order`
      alongside it.
- [x] 6.2 Add an equal-priority ordering test (arrival order preserved
      within the same priority) for `MemoryAsyncPriorityQueue`.
      Surfaced a real bug during TDD: `apush_priority`'s original
      `(-priority, entry_id)` tuple tie-broke ties by comparing `entry_id`
      directly, which only happened to sort chronologically because of an
      undocumented, implementation-specific property of `uuid.uuid7()`.
      Added an explicit monotonic sequence counter
      (`self._pending_priority_sequence`) as the tuple's middle element so
      arrival order no longer depends on that. Verified via a second test,
      `test_memory_priority_queue_arrival_order_does_not_depend_on_uuid_ordering`,
      constructing entries with IDs deliberately reversed relative to push
      order — TDD-confirmed to fail without the sequence counter, pass with
      it. See design.md, "Equal-priority entries dispatch in arrival order".
- [x] 6.3 Add the same two ordering tests (higher-first,
      equal-priority-preserves-arrival) for `RedisAsyncPriorityQueueJson` in
      `tests/test_redispqueuejson.py`, against a real Redis instance.
      Surfaced the same class of bug on Redis: `zrevrange`'s tie-break on
      equal ZSET scores is reverse-lexicographic on the encoded entry-ID
      member, which is closer to reverse arrival order than forward. Fixed
      by folding a Redis-wide monotonic sequence (`INCR`) into the score's
      low bits (see design.md). TDD-confirmed: the ordering test failed
      against the original score-is-priority-only implementation, passed
      after the fix.
- [x] 6.4 Add a test that a priority-enqueued entry dequeued via the
      tracked path is a full `QueueEntry` — findable by `afind()`, and able
      to run through the normal lifecycle transitions (`_amark_running` etc.)
      — for both `MemoryAsyncPriorityQueue` and `RedisAsyncPriorityQueueJson`.
- [x] 6.5 Add a test that enqueueing without a priority on a priority
      backend defaults to `0` and that a zero-priority entry still dispatches
      (doesn't get stuck behind an always-nonzero assumption).
- [x] 6.6 Add a test that a non-priority backend (`MemoryAsyncQueue`,
      `RedisAsyncQueue`) ignores a non-zero `priority` passed to `enqueue()`
      and dispatches in its existing FIFO order — covers the "Ignore
      priority on a non-priority backend" spec scenario.
      Covered for `MemoryAsyncQueue`/`MemoryAsyncStack`
      (`test_non_priority_backend_ignores_priority_and_dispatches_fifo`); not
      duplicated for `RedisAsyncQueue` since the ignore-path is entirely in
      the shared `AsyncQueue`/`EventQueue` hook defaults (already exercised),
      not backend-specific code.
- [x] 6.7 Add a test that the `QUEUED`→`FAILED` pre-dispatch failure path
      (`_areplace_entry`'s `adiscard` call) removes an entry from the
      priority pending store too, for both backends — i.e. a failed entry
      does not remain dequeuable.
- [x] 6.8 Run the full suite (`pytest`) to confirm no existing test assumed
      `AsyncQueue.aenqueue`/`adequeue` call `self._provider.apush`/`apop`
      directly (e.g. via mocking) rather than through the new hooks.
      1570 passed, 2 skipped, 0 failed. `ruff check`, `ruff format --check`,
      `ty check` all clean.

## 7. Docs

- [x] 7.1 Check `README.md`/architecture docs for any description of
      priority queues that describes only the raw value API, and update to
      mention that priority queue *entry* dispatch (via `enqueue`/`dequeue`)
      now also honours priority.
      Updated the stale "Identified entries have no priority parameter"
      statement near the raw-value API reference table.
- [x] 7.2 Add a short note to the `priority` field's meaning (higher value
      dispatches first, default `0`) wherever `timeout_seconds` is
      documented alongside other entry fields, so the two aren't confused.
      Added a new "Entry priority" subsection immediately after "Execution
      budgets", mirroring that section's structure.

## 8. Findings from independent review (cursor.json, gemini.json)

Two independent reviews of the completed implementation found the tracked
push/pop/discard path was correct, but the change never made a priority
backend actually dispatchable end-to-end. Both P1s below block merge;
addressed after the initial implementation, before archiving.

- [x] 8.1 `ahas_pending()`/`has_pending()` never inspected the priority
      pending store on either backend, so `runqueues.py:104`'s
      `while not await queue.ahas_pending()` loop spun forever for a
      priority-only queue and never constructed a worker — the exact
      deployment path this proposal exists to unblock. Fixed both
      backends' `ahas_pending` to also check the priority store. Verified
      with a unit test on each backend and a full `runqueues` integration
      test (`test_starts_a_worker_for_a_priority_backend_with_only_priority_entries`)
      constructing the real `Command`, TDD-confirmed to hang without the
      fix and pass with it.
- [x] 8.2 `RedisAsyncQueueWorker` claims work via `provider.aclaim()` ->
      `_CLAIM_SCRIPT`, which only ever reads the plain pending list —
      structurally blind to the priority ZSET regardless of 8.1. Added
      `_CLAIM_SCRIPT_WITH_PRIORITY` (tries the plain list first for
      delayed-retry recoveries, falls back to the priority ZSET) and
      `_RECOVER_SCRIPT_WITH_PRIORITY` (a recovered priority entry
      redelivers to the priority ZSET with a fresh score, not the plain
      list, so a crashed worker's priority entry doesn't silently
      downgrade to FIFO). Routed through new queue-level `aclaim`/
      `aclaim_unexpired`/`arecover` hooks (`RedisAsyncQueue` default,
      `RedisAsyncPriorityQueue` override) — the same override-via-
      inheritance pattern as `_apush`/`_apop`/`_adiscard`, not an
      `isinstance` check — so `RedisAsyncQueueWorker._next` calls
      `queue.aclaim(...)`/`queue.arecover(...)` polymorphically. Caught
      and fixed a self-inflicted bug during this work: the priority claim
      script's sequence-reset check originally fired before knowing
      whether a claim conflict would requeue the entry, corrupting arrival
      order for the requeued entry relative to later pushes; moved the
      reset to only the two genuinely terminal outcomes (`expired`,
      `claimed`). Verified with a provider-level claim/discard test, a
      provider-level recovery test (forcing an expired lease), and a full
      end-to-end test through a real `RedisAsyncQueueWorker`
      (`test_real_worker_claims_and_dispatches_a_priority_entry`) — all
      three TDD-confirmed to fail before their respective fixes.
- [x] 8.3 `EventQueue.aenqueue` accepts `priority` but never persists it
      (matches task 3.5's documented decision), which contradicted
      `specs/queue-entries/spec.md`'s literal, unscoped "the entry-oriented
      enqueue operation SHALL... persist it" wording. Narrowed the spec
      requirement to `AsyncQueue` explicitly and added an
      `EventQueue`-ignores-priority scenario, rather than changing
      `EventQueue`'s behaviour — reconciles the artifact with the decision
      already made, doesn't reopen it. `openspec validate --strict` still
      passes.
- [x] 8.4 `adelete`'s priority-store cleanup (added when fixing an earlier
      review finding) ran outside the lock that removes the entry's
      durable record on the memory backend, leaving a window where a
      concurrent `apop_priority` could pop an ID whose record was already
      gone. Fixed on memory: nested inside the same `self._lock` (an
      `RLock`, so re-entrant) instead of acquired after releasing it. Left
      unfixed on Redis by explicit user direction — `adelete` is not
      reachable for any `AsyncQueue`/priority queue today (only
      `EventQueue.aclear()` calls it, and `EventQueue` never populates the
      priority store), so this is a currently-dead-code path not worth the
      new Lua script it would need to close atomically.
- [x] 8.5 Two documentation inaccuracies: README never mentioned
      `priority`'s ±100,000 bound (a caller passing a value beyond it gets
      an unexplained `ValueError`), and `BaseQueue.aenqueue`'s docstring
      claimed non-priority `AsyncQueue` backends "always dispatch FIFO" —
      false for `MemoryAsyncStack`/`RedisAsyncStack`, which are LIFO.
      Added the bound and its Redis-score-precision rationale to the
      README "Entry priority" section; reworded the docstring to "FIFO, or
      LIFO for a stack" instead of the incorrect blanket claim.
- [x] 8.6 `tests/test_entries.py::test_round_trips_an_execution_budget` had
      stopped asserting on its `from_dict()` result — `restored =
      QueueEntry.from_dict(...)` / `assert restored == entry` were
      replaced with a bare, result-discarding call while fixing an
      unrelated ruff unused-variable finding earlier in this session, an
      unintended coverage regression rather than a deliberate
      simplification. Restored both lines.

## 9. Findings from a further independent review round (cursor.json rev 2, gemini.json rev 2, claude.json)

A third round of independent review, collated via `/collect-reviews`, found
the claim/recover work in section 8.2 was not the whole story: a lost claim
still redelivers via the plain (non-priority-aware) path.

- [x] 9.1 `RedisAsyncQueueWorker._mark_running` releases a lost claim (e.g.
      after `amark_running` fails a lost race) via `provider.arelease()` ->
      `_RELEASE_SCRIPT`, which always parks the released entry on the plain
      delayed set, regardless of backend. `_CLAIM_SCRIPT_WITH_PRIORITY`
      (section 8.2) promotes the plain delayed/pending list to claimable
      status unconditionally, before ever checking the priority ZSET — so a
      released low-priority entry could jump ahead of a genuinely
      higher-priority entry still waiting in the ZSET, a real priority
      inversion on the release path that section 8.2 did not close. Fixed
      with the same override-via-inheritance pattern as `aclaim`/`arecover`:
      added `_RELEASE_SCRIPT_WITH_PRIORITY` (reads the entry's durable
      `priority` field — a released claim's JSON never carries it — and
      re-inserts into the priority ZSET with a fresh sequence-broken score,
      the same encoding `apush_priority`/`_CLAIM_SCRIPT_WITH_PRIORITY` use;
      guards `entry.priority` with `or 0` in case a pre-upgrade record
      lacks the field, the same defensive pattern flagged as missing
      elsewhere in `_RECOVER_SCRIPT_WITH_PRIORITY` but not yet backported
      there), a new `arelease_priority` provider method, and queue-level
      `arelease` hooks (`RedisAsyncQueue` default delegating to
      `provider.arelease`, `RedisAsyncPriorityQueue` override delegating to
      `provider.arelease_priority`). Updated
      `RedisAsyncQueueWorker._mark_running` to call `queue.arelease(...)`
      instead of `provider.arelease(...)`; confirmed
      `RedisEventQueueWorker._release` (which has no priority variant) was
      left untouched. TDD-verified with
      `test_release_does_not_let_a_low_priority_claim_jump_ahead_of_a_higher_priority_entry`
      in `tests/test_redispqueuejson.py`: claims a low-priority entry,
      enqueues a higher-priority one, releases the claimed entry, and
      asserts the higher-priority entry claims next — confirmed to fail
      (low-priority entry claims next) with the fix temporarily reverted,
      passes with it restored. Full suite (1591 passed, 2 skipped), `ruff
      check`, `ruff format --check`, `ty check` all clean.
- [x] 9.2 `_RECOVER_SCRIPT_WITH_PRIORITY` reads `entry.priority` directly
      into its score calculation with no fallback (`claude.json` CR-1).
      Confirmed this is not reachable on the current codebase — every
      writer of an entry record goes through `QueueEntry.to_dict()`, which
      serialises all dataclass fields including `priority` unconditionally,
      so no record written today can lack it; nothing has shipped a prior
      schema version either. Applied as a defensive change rather than a
      live-bug fix: a hand-crafted record, or a future migration/backfill
      script that writes entry JSON without going through `to_dict()`,
      would otherwise crash the whole recovery batch with "attempt to
      perform arithmetic on a nil value" (`tonumber(nil)` is nil, and
      nil-arithmetic errors in Lua) instead of just treating the entry as
      unprioritised. Guarded with the same `tonumber(entry.priority) or 0`
      pattern already used in `_RELEASE_SCRIPT_WITH_PRIORITY` (9.1), closing
      the asymmetry between the two sibling scripts. TDD-verified with
      `test_redis_provider_recover_priority_tolerates_a_record_missing_the_priority_field`
      in `tests/test_providers.py`: writes an entry record directly (not via
      `to_dict()`) with `priority` stripped, confirmed to crash with
      `redis.exceptions.ResponseError: ... attempt to perform arithmetic on
      a nil value` before the guard, passes with it. Full suite (1592
      passed, 2 skipped), `ruff check`, `ruff format --check`, `ty check`
      all clean.
- [x] 9.3 `_CLAIM_SCRIPT_WITH_PRIORITY`'s conflict branch — reinserting an
      entry at its original ZSET score when a concurrent claim wins the
      `SET ... NX` race (`claude.json` CR-2) — had no regression coverage.
      On inspection the logic itself is correct (confirmed with the user:
      `priority_score` is captured before the pop specifically so a
      conflict restores the entry to exactly the position it already held,
      not a freshly computed one), so this was purely a test-coverage gap,
      not a live bug. Added
      `test_redis_provider_priority_claim_conflict_reinserts_at_the_original_score`
      in `tests/test_providers.py`: re-pushes the same `entry_id` while its
      claim is still live to force the conflict outcome deterministically
      (without needing real concurrency), asserts the ZSET score is
      unchanged and the entry never leaks into the plain pending list.
      Investigating this finding also surfaced a separate, pre-existing,
      cross-cutting issue (confirmed with the user, addressed as 9.4 below)
      and prompted a design discussion about unifying the plain and
      priority pending stores into a single ZSET, deliberately deferred to
      a future OpenSpec change rather than folded into this one.
- [x] 9.4 `AsyncQueue.aenqueue`/`EventQueue.aenqueue` store a new entry's
      durable record and add it to the pending store as two (for an event
      with a timeout, three) separate, non-atomic provider calls — present
      since the very first commit, not introduced by or specific to this
      change, but surfaced while investigating 9.3's claim-conflict
      question. A crash or connection loss between the calls leaves a
      durably stored entry with no pending-store index pointing to it: a
      silent, permanent orphan, never claimed or dequeued, discoverable
      only by `afind()`ing its exact ID directly — unlike the reverse case
      (an index entry with no record), which `apop`/`aclaim`'s subsequent
      `afind()` already surfaces as a named, caught exception
      (`QueueEntryMissingError`) and self-heals via the index's own
      `ZREM`/`LREM`. Confirmed with the user this is worth closing now,
      scoped to Redis only (the in-memory backend has no cross-process
      durability to protect, so the same crash window there is
      inconsequential). Added a new `_astore_and_push` hook to
      `AsyncQueue`/`EventQueue` (default: today's two/three-call sequence,
      unchanged for the memory backend) and three new atomic Lua scripts —
      `_STORE_AND_PUSH_SCRIPT`, `_STORE_AND_PUSH_PRIORITY_SCRIPT`,
      `_STORE_EVENT_AND_PUSH_SCRIPT` — with matching provider methods
      (`astore_and_push`, `astore_and_push_priority`,
      `astore_event_and_push`) and queue-level overrides
      (`RedisAsyncQueue`, `RedisAsyncPriorityQueue`, `RedisEventQueue`)
      routing `aenqueue` through the atomic path. TDD-verified with three
      wiring tests —
      `test_aenqueue_routes_through_the_atomic_store_and_push_path`
      (`tests/test_redis_entries.py`),
      `test_aenqueue_routes_through_the_atomic_store_and_push_priority_path`
      (`tests/test_redispqueuejson.py`), and
      `test_redis_event_aenqueue_routes_through_the_atomic_store_and_push_path`
      (`tests/test_event_queue.py`) — each monkeypatching the old two-call
      provider methods to raise, proving `aenqueue` no longer calls them;
      confirmed all three fail when the queue-level `_astore_and_push`
      overrides are temporarily reverted, pass with them restored. Also
      added direct provider-level tests proving each new atomic method
      leaves the entry both findable and claimable
      (`test_redis_provider_astore_and_push_makes_the_entry_findable_and_claimable`
      and its priority/event siblings in `tests/test_providers.py`). Full
      suite (1599 passed, 2 skipped), `ruff check`, `ruff format --check`,
      `ty check` all clean.
- [x] 9.5 `adelete` issued six separate, non-atomic Redis calls (DEL, LREM,
      three ZREMs, plus `adiscard_priority`'s own script) to clean up an
      entry — previously found and explicitly deferred once (task 8.4) as
      dead code not worth a new script, since `adelete`'s only real caller
      (`EventQueue.aclear()`) never populates the priority store and
      nothing in the live dispatch path depends on this cleanup being
      atomic. `cursor.json` CR-2 re-flagged the same gap on a second review
      pass. Confirmed with the user this was still worth closing now (a
      crash partway through `aclear()` leaves a partial clear with no way
      to detect or resume which entries were only half-removed). Fixed
      with one atomic `_DELETE_SCRIPT` covering all eight keys (durable
      record, plain pending list, delayed set, claim-lease ZSET,
      unclaimed-lease ZSET, claim key, priority ZSET, priority sequence
      counter), replacing `adelete`'s six-call body with a single script
      invocation, the same pattern as every other multi-key mutation in
      this provider. TDD-verified with
      `test_redis_provider_adelete_removes_every_store_atomically` in
      `tests/test_event_queue.py`, exercised through `EventQueue.aclear()`
      (`adelete`'s real caller) on two entries covering disjoint
      preconditions that don't naturally coexist on one entry — an
      unclaimed event and a claimed one — so every store the docstring
      claims to clean is genuinely populated beforehand; systematically
      confirmed by dropping each of the script's seven cleanup lines in
      turn and re-running the test, catching every one as a failure, then
      restoring. (This verification loop is also where a `git checkout`
      inside a broken shell loop accidentally reverted this session's
      entire `provider.py` to its pre-priority-work state; caught
      immediately via `git status`, and the file was fully reconstructed
      from the same knowledge that produced it, then re-verified against
      the complete test suite, `ruff`, and `ty` before continuing — no
      work was lost, but it is recorded here for the audit trail.) Full
      suite (1600 passed, 2 skipped), `ruff check`, `ruff format --check`,
      `ty check`, `openspec validate --strict` all clean.

Investigating 9.3/9.4 also raised a larger architectural question,
deliberately deferred rather than acted on here: the plain pending list
(a Redis LIST) and the priority pending ZSET are two separate collections
joined only by `entry_id`, and — more broadly — this queue's Redis storage
spans up to seven queue-level collections (pending list, priority ZSET and
its sequence counter, delayed set, claim-lease deadlines, unclaimed-lease
deadlines) plus one durable record per entry, coordinated per-operation via
dedicated Lua scripts. Discussed and confirmed with the user: a unified
design — one ZSET for all pending entries, priority or not (a non-priority
queue's score reducing to `-sequence`, i.e. `priority=0`), with every push
still atomically `INCR`ing one shared sequence counter — would eliminate
the plain/priority split entirely (collapsing `_CLAIM_SCRIPT` and
`_CLAIM_SCRIPT_WITH_PRIORITY` into one script, removing the `_apush`/
`_apop`/`_adiscard`/`aclaim`/`arecover`/`arelease` override hierarchy
between `RedisAsyncQueue` and `RedisAsyncPriorityQueue`) and directly
addresses `cursor.json` CR-4 ("`_CLAIM_SCRIPT_WITH_PRIORITY` duplicates
`_CLAIM_SCRIPT`"). Sized as a rewrite of the base Redis storage layer, not
an addition to it (also needs a sign-flip for LIFO/stack mode's tie-break),
so scoped as a future OpenSpec change rather than folded into this one.

- [x] 9.6 The user ran `uv run pytest -Walways -Werror -q` (this project's
      stricter CI-equivalent invocation, escalating every warning to a hard
      error) and found failures the plain `pytest -q` used throughout this
      change had never surfaced: `main` itself is fully clean under
      `-Werror` (1552 passed), but several tests added across this change
      opened a fresh asyncio event loop directly (`asyncio.run(...)`,
      `async_to_sync(...)` called on a bare async function) to reach a
      provider method or raw Redis client without going through the
      codebase's established `.enqueue()`/`.dequeue()` sync wrappers --
      which always close their connection in a `finally` block
      (`BaseQueue._run_and_close`, `base.py`) -- leaving the connection
      opened on that ad-hoc loop permanently unclosed once the loop itself
      was torn down, surfacing as `ResourceWarning: unclosed Connection`
      at garbage-collection time, escalated to a test failure attributed to
      whatever unrelated test happened to trigger GC next (order-dependent,
      reproduced with three different "blamed" tests across three
      consecutive runs before isolating the real four culprits). Fixed by
      wrapping each in `try/finally: await provider.aclose()` (or
      `queue.aclose()`), matching the pattern the sync wrappers already
      use: `test_adelete_does_not_create_a_priority_sequence_key_for_a_plain_queue`
      (`tests/test_redis_entries.py`),
      `test_provider_adelete_also_removes_a_still_pending_priority_entry`,
      `test_sequence_counter_resets_once_the_priority_store_drains`, and
      `test_release_does_not_let_a_low_priority_claim_jump_ahead_of_a_higher_priority_entry`
      (all `tests/test_redispqueuejson.py`). Verified clean across 4
      consecutive full-suite runs under `uv run pytest -Walways -Werror -q`
      (1600 passed, 2 skipped each time, zero failures/errors), plus
      `ruff check`, `ruff format --check`, `ty check`,
      `openspec validate --strict` all still clean.
- [x] 9.7 `claude:CR-3` (no end-to-end Redis `runqueues` integration test for
      a priority backend) was initially proposed as skippable because
      `demo_aq` already exercises `runqueues` end-to-end -- but confirmed on
      inspection that `demo_aq` is configured with the plain
      `RedisAsyncQueueJson` backend, not a priority variant, so it never
      actually covers the gap the finding was about. Rather than add a
      narrow unit-level integration test, built `demo_pq/` -- a second demo
      Django project mirroring `demo_aq`'s structure exactly, but backed by
      `RedisAsyncPriorityQueueJson` on its own Redis port (`16389`, so both
      demos can run side by side) and dashboard port (`8001`). Its worker
      (`DemoPriorityQueueWorker`) runs four independent injector tasks, one
      per priority tier (`low=-5`, `normal=0`, `high=5`, `urgent=10`), each
      on its own random interval -- low/normal form a steady backlog,
      high/urgent are rare enough to be a visible event jumping that
      backlog, and the dashboard gets a new colour-coded "Priority" column
      (grey/blue/amber/red) styled the same way the existing "State" column
      already is. Critically, the worker's `_next`/`_requeue_entry` route
      through `queue.aclaim(...)`/`queue.arelease(...)` -- the queue-level
      hooks this session's fixes made priority-aware -- rather than calling
      `provider.aclaim(...)`/`provider.arelease(...)` directly the way
      `demo_aq`'s worker does (correct there, since it only needs the plain
      FIFO path; would silently bypass priority ordering, including 9.1's
      release-path fix, if copied into a priority demo unchanged).
      Live-verified end-to-end against a real Redis instance: seeded one
      entry per tier via `manage.py demo`, inspected the priority ZSET
      directly (`ZRANGE ... WITHSCORES`) and confirmed the stored scores
      matched the `priority * 2**32 - sequence` encoding; then, separately,
      enqueued all four tiers in low-to-urgent order and claimed them via
      `queue.aclaim(...)` in a loop, confirming the claim order was the
      exact reverse (urgent, high, normal, low) -- i.e. real priority
      dispatch through the actual worker-facing API, not a provider-level
      shortcut. `manage.py check` clean, `ruff check`/`ruff format --check`
      clean on `demo_pq/`, main suite still 1600 passed/2 skipped under
      `-Walways -Werror` (the new project is fully independent, its own
      venv and settings module, so it cannot affect the main suite).
      Follow-up: the user found the initial injection intervals (4-45s
      across tiers) flooded the dashboard table -- an entry's full visible
      lifetime (queued delay + running delay + retention) averages ~95s, so
      spawning new entries every few seconds let dozens accumulate before
      the first one expired. Retuned each tier's interval to target ~5-10
      entries in flight at steady state for the two common tiers (`low`
      10-16s, `normal` 12-20s), preserving the original ratio for the two
      rare tiers (`high` 30-50s, `urgent` 55-85s) so they remain occasional,
      clearly visible events rather than their own backlog. Verified live:
      ran the worker against real Redis for ~60s and counted only 10 live
      entry records across all four tiers, consistent with the new target.
      Second follow-up: the user found the spawn rate still too high and
      pointed out the dashboard was sorting purely by arrival order
      (`queued_at`), so priority was only visible as a coloured label, not
      as actual table position -- the one thing this demo exists to show.
      Fixed both: retuned intervals again to target ~3-5 in flight for
      low/normal (`low` 22-32s, `normal` 26-40s), scaling high/urgent
      proportionally (`high` 65-100s, `urgent` 110-170s); and changed
      `DashboardProjection._sorted_rows` to sort by `(-priority, queued_at,
      id)` instead of `(queued_at, id)`, with the frontend's `render()`
      appending each row (new or already-attached) in that server-sent
      order every SSE update -- `Node.append` on an attached element moves
      it rather than duplicating it, so the whole `<tbody>` re-sorts to
      match on each update, not just newly created rows. Live-verified in
      a real browser (seeded a batch, ran `runqueues` + `runserver`,
      screenshotted the dashboard): confirmed the table lists strictly
      urgent > high > normal > low top to bottom with the currently
      RUNNING urgent entry at the very top. Main suite still 1600
      passed/2 skipped under `-Walways -Werror`; `ruff check`/
      `ruff format --check` clean on `demo_pq/`.
      Third follow-up: the user asked whether entries should also start
      (transition queued -> running) in priority order, not just display
      that way -- correctly identifying that they didn't. `_next`'s
      dispatch loop correctly claims the highest-priority pending entry via
      `queue.aclaim(...)`, but the previous `_QUEUED_DELAY_SECONDS = (10,
      30)` window applied the same random range to every entry regardless
      of tier; if the claimed (highest-priority) entry's own delay hadn't
      elapsed yet, `_next` released it and dispatched nothing that cycle --
      so a low-priority entry could still happen to become due and start
      running before a higher-priority entry still waiting out its own
      unrelated random delay. Per the user's direction ("the delay should
      be set with the priority a factor in its calculation... highest
      priority selected and gradually move down the list"), replaced the
      single global `_QUEUED_DELAY_SECONDS` with a per-tier `queued_delay`
      band in `PRIORITY_TIERS`, narrow and non-overlapping, highest
      priority shortest (`urgent` 4-8s, `high` 10-15s, `normal` 17-23s,
      `low` 25-32s) -- so a higher-priority entry always becomes due before
      a lower-priority one that arrived around the same time, and dispatch
      order tracks priority order through the "still waiting to start"
      window too, not just the underlying claim. `_RUNNING_DELAY_SECONDS`
      (running -> terminal) deliberately left unscaled, per the user's
      explicit instruction that only the queued -> running wait should
      depend on priority. Live-verified against real Redis: enqueued one
      entry per tier simultaneously, ran the actual `DemoPriorityQueueWorker`,
      and polled for each entry's `running`/`succeeded` transition --
      dispatch order was urgent (6.3s) -> high (14.6s) -> normal (22.1s) ->
      low (31.3s), strictly increasing and exactly matching each tier's
      band. Main suite still 1600 passed/2 skipped under `-Walways
      -Werror`; `ruff check`/`ruff format --check` clean on `demo_pq/`.
      Fourth follow-up: the user set a hard rule -- at most 5 handlers may
      run concurrently -- and reported still seeing new low-priority tasks
      spawn while others weren't visible. Root cause: `_dispatch` fired an
      unbounded `asyncio.create_task(self._complete_entry(...))` per
      claimed entry with no cap at all, so a steady drip of low/normal
      injections could keep an unlimited number of handlers running,
      drowning out the rare high/urgent arrivals in a wall of low-priority
      `running` rows. Added `self._handler_slots =
      asyncio.Semaphore(_MAX_CONCURRENT_HANDLERS)` (`_MAX_CONCURRENT_HANDLERS
      = 5`), gated in `_next` *before* claiming (`if
      self._handler_slots.locked(): return None`, so a full worker doesn't
      claim an entry at all that cycle, leaving it visibly `queued` and
      claimable once a slot frees, rather than claimed-but-idle and
      blocking other entries -- including higher-priority ones -- from
      being claimed while a slot is full), a real `acquire()` in
      `_dispatch` (with a matching `release()` on the lost-claim-race early
      return, since `_complete_entry` never runs to release it in that
      case), and a `release()` in `_complete_entry`'s `finally` so a slot
      frees on every outcome (success, intentional failure, or handler
      cancellation during shutdown). Live-verified against real Redis:
      seeded 20 entries across all four tiers, ran the actual worker for
      20s, and confirmed exactly 5 entries were `running` at once --
      4 `urgent` and 1 `high`, i.e. the priority-scaled queued-delay from
      the previous fix combined with the cap to consistently fill the 5
      slots with the highest-priority work currently due, not just
      whichever arrived first. Main suite still 1600 passed/2 skipped
      under `-Walways -Werror`; `ruff check`/`ruff format --check` clean
      on `demo_pq/`.
      Fifth follow-up: the user reported still seeing nothing above low
      priority. Investigation found a genuine deadlock, not a priority
      bias: `runqueues`'s `_activate_worker` (in the library's
      `django_queue/management/commands/runqueues.py`, not something
      `demo_pq` controls) does `while not await queue.ahas_pending(): await
      asyncio.sleep(0.1)` *before* ever constructing the worker or calling
      its `run()` -- but `DemoPriorityQueueWorker`'s four per-tier
      injectors are started inside `run()` itself. On a genuinely empty
      queue (the state every fresh `docker compose up` / cleared-records
      test starts from) this is a true deadlock: the worker never starts
      because nothing is pending, and nothing becomes pending because only
      the worker's own injectors would populate it. Every previous
      live-verification this session had inadvertently avoided the empty
      case -- each either seeded via `manage.py demo` first or constructed
      `DemoPriorityQueueWorker` directly and called `run()` in a
      controlled script, bypassing `_activate_worker`'s gate entirely --
      so a bare `manage.py runqueues` on a truly fresh queue was never
      actually exercised until this report, confirmed by reproducing it
      directly (foreground `runqueues` on a cleared queue, 45s, zero
      entries ever created). Fixed by seeding one entry per priority tier
      from `DashboardConfig.ready()` (`sys.argv[1] == "runqueues"` guard,
      new `seed_one_entry_per_tier()` in `demo_worker.py`, synchronous via
      `async_to_sync`, itself guarded by `ahas_pending()` so it's a no-op
      if the queue already has entries) -- `ready()` fires during
      `django.setup()`, before `Command.handle()` runs, so the seed lands
      before `_activate_worker`'s gate is ever checked. Live-verified:
      confirmed a bare `manage.py runqueues` from a cleared queue now
      starts and dispatches (previously hung 45s with zero output);
      confirmed the seed produces exactly one entry per tier; confirmed a
      second `runqueues` invocation does not double-seed (idempotency via
      `ahas_pending()`); confirmed healthy steady-state mixed-tier,
      mixed-state activity after 2 minutes from a genuinely fresh start
      (`high`/`normal`/`low` present across `running`/`succeeded`/`queued`
      -- `urgent`'s 110-170s interval hadn't fired yet, correctly). Main
      suite still 1600 passed/2 skipped under `-Walways -Werror`; `ruff
      check`/`ruff format --check` clean on `demo_pq/`.
      Sixth follow-up: with the deadlock fixed, the user watched a live
      session and reported `normal`/`low` kept appearing but `high` and
      especially `urgent` seemed absent, asking whether the injectors were
      biased. Checked the injector mechanism itself: all four tiers run
      the identical `_inject_tier_entries` loop as independent
      `asyncio.create_task`s, differing only in their `interval` config --
      no bias in the code. The apparent absence was `high`/`urgent`'s
      interval genuinely being ~3x/~5x rarer than low/normal (a deliberate
      choice from an earlier follow-up), which over a short demo session
      reads as "almost never arrives" rather than "occasional" -- and was
      confirmed as exactly that: both eventually appeared once the session
      ran long enough (matching their own interval bounds). Per the user's
      direction, narrowed the gap: `high` 65-100s -> 45-55s (~1.5x normal
      instead of ~3x), `urgent` 110-170s -> 65-85s (~2.5x normal instead
      of ~5x) -- still clearly rarer than low/normal, not equal cadence.
      Live-verified against real Redis: sampled tier presence every 30s
      over a 3-minute run from a fresh queue; all four tiers were present
      in every single sample, versus the previous tuning where `urgent`
      was absent for stretches exceeding a minute. Main suite still 1600
      passed/2 skipped under `-Walways -Werror`; `ruff check`/
      `ruff format --check` clean on `demo_pq/`.

## 10. Findings from a further independent review round

- [x] 10.1 (P3) `validate_priority`'s `MAX_PRIORITY_MAGNITUDE = 100_000`
      bound ran unconditionally in `QueueEntry.__post_init__` (task 8.5's
      original addition), narrowing the OpenSpec's "an integer dispatch
      priority" (no stated magnitude bound) into a hard rejection for
      *every* backend -- including a plain (non-priority) `AsyncQueue` or
      `EventQueue`, which the spec explicitly requires to ignore `priority`
      entirely. Reproduced directly:
      `QueueEntry.create(queue="x", payload="y", priority=200_000)` raised
      `ValueError` even though nothing about a plain backend's dispatch
      path ever consults the value -- "ignore it" and "reject it" are
      contradictory outcomes for the same input. The bound itself is real
      and necessary (the Redis priority backend packs `priority` and a
      sequence number into one ZSET score, exact only within a double's
      53-bit integer range), just misapplied at the wrong layer -- the
      in-memory priority backend has no such bound at all, since Python
      integers are arbitrary precision. Confirmed with the user: moved the
      magnitude check out of `validate_priority`/`QueueEntry.__post_init__`
      (which now only type-checks `priority` as `int`, unconditionally,
      independent of backend) and into the Redis priority provider itself
      -- new `MAX_PRIORITY_MAGNITUDE`/`validate_redis_priority_magnitude`
      in `django_queue/backends/redis/provider.py`, called from
      `apush_priority` and `astore_and_push_priority`, the two points
      where a caller-supplied priority is actually written into a fresh
      ZSET score. `arelease_priority`/`arecover_priority` re-derive scores
      from an already-accepted entry's stored `priority`, so they need no
      additional guard -- an entry cannot move between queues, so if it
      passed the bound at enqueue time it still satisfies it later.
      Rewrote the two `QueueEntry`-level tests that asserted rejection at
      construction (`test_rejects_a_priority_beyond_the_safe_encoding_range`,
      `test_rejects_a_restored_record_carrying_an_invalid_priority`) into
      acceptance tests (`test_accepts_a_priority_beyond_the_redis_encoding_range`,
      `test_round_trips_a_priority_beyond_the_redis_encoding_range`) in
      `tests/test_entries.py`, and added three new provider-level tests in
      `tests/test_providers.py`: `apush_priority` and
      `astore_and_push_priority` each reject a priority beyond the bound
      (`test_redis_provider_apush_priority_rejects_a_priority_beyond_the_encoding_range`,
      `test_redis_provider_astore_and_push_priority_rejects_a_priority_beyond_the_encoding_range`),
      and a plain (non-priority) queue's `astore_and_push` genuinely
      enqueues and dispatches an entry with `priority=200_000` without
      ever touching the bound
      (`test_redis_provider_plain_apush_ignores_a_priority_beyond_the_encoding_range`).
      TDD-verified: temporarily removed the new provider-level guard and
      confirmed the two rejection tests fail; separately restored the old
      unconditional `QueueEntry`-level bound and confirmed both the new
      entry-level acceptance test and the new plain-queue provider test
      fail -- genuine red in both directions, then restored. Updated the
      top-level `README.md`'s "Entry priority" section, which had
      incorrectly stated the bound applied "at construction" for every
      backend. Full suite (1604 passed, 2 skipped), `ruff check`,
      `ruff format --check`, `ty check`, `openspec validate --strict` all
      clean.
- [x] 10.2 (P6) A prior review pass flagged the demo README's high/urgent
      cadence table as stale against `PRIORITY_TIERS`. Checked directly:
      the table already reads `high` 45-55s / `urgent` 65-85s, matching
      `PRIORITY_TIERS` exactly -- this was fixed by the "is there a bias?"
      follow-up (recorded above) after the review that raised this finding
      ran, so no further action needed; noted here so the finding isn't
      silently unaccounted for.
