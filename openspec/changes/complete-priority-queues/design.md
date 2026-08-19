## Context

See proposal.md - Why for the gap. In code terms: `AsyncQueue.aenqueue()`
(`django_queue/backends/base.py:261`) always ends with
`await self._provider.apush(entry.id)`, and `AsyncQueue.adequeue()` always
calls `await self._provider.apop()`. Both `apush`/`apop` are plain FIFO/LIFO
(a Redis `LIST` via `_entry_pending_name`, or a `queue.Queue`/`LifoQueue` in
memory). `RedisAsyncPriorityQueue` and `MemoryAsyncPriorityQueue` do not
override `aenqueue`/`adequeue` at all — they only override the *raw* value
API (`aadd`/`aget`/`apoll`/`apeek`/`asize`/`aclear`), which talks to a
completely separate storage structure: a Redis sorted set (`ZADD`/`ZREVRANGE`,
keyed directly on `self._queue_name`, no relation to `_entry_pending_name`)
and a `queue.PriorityQueue`, both storing encoded *values*, not entry IDs, and
with no link to `QueueEntry`/`astore`/`afind` at all.

`AsyncQueueWorker` dispatches by calling `queue.adequeue()` directly
(`django_queue/worker.py:338`) — there is no separate claim step in this path
(that mechanism belongs to `EventQueue`, not `AsyncQueue`), so fixing
`adequeue()`'s ordering is sufficient; no claim/lease code needs to change.

## Goals / Non-Goals

**Goals:**
- Priority-ordered dispatch for the entry-tracked `aenqueue`/`adequeue` path
  on both Redis and memory priority backends.
- No change to non-priority backend behaviour or to the untracked raw
  `add`/`aadd`/`poll`/`peek` API and its existing storage.
- Minimal surface change: reuse `QueueEntry`'s existing generic
  `to_dict`/`from_dict` (iterates `fields(self)`) so adding `priority` needs
  no wire-format special-casing.

**Non-Goals:**
- Priority *rebalancing*, aging, or starvation prevention for low-priority
  entries — out of scope, not requested, and not implied by the gap this
  change closes.
- Priority support for `EventQueue`/event backends — the gap and the sibling
  project's need (`django-redis-tasks`, `Task.priority`) are both about
  `AsyncQueue`-family task dispatch, not events.
- Changing the raw priority API's own storage or semantics
  (`aadd_priority`/`apoll_priority` keep using the existing sorted
  set/`PriorityQueue`, independent of this change).

## Decisions

### Add `priority: int = 0` to `QueueEntry`, higher dispatches first
Matches the existing convention on the raw path: Redis
`aadd_priority`/`apoll_priority` use `zadd(..., {value: priority})` with
`zrevrange` (highest score first, `django_queue/backends/redis/provider.py:506`),
and the memory provider negates priority into `PriorityQueue` (which is a
min-heap) at `django_queue/backends/memory/provider.py:102`
(`self._priority_items.put_nowait((-int(priority), value))`). Keeping "higher
wins" consistent between the raw and tracked paths avoids two different
priority conventions in the same library.

Default `0` so every existing FIFO/stack caller, test, and stored entry is
unaffected: `QueueEntry.create()` and `AsyncQueue.aenqueue()` gain an optional
`priority` parameter defaulting to `0`, and `to_dict`/`from_dict` need no
change since both already iterate `fields(self)` generically
(`django_queue/entries.py:207`, `:214`).

**Alternative considered**: a separate `PriorityQueueEntry` subclass adding
the field, mirroring the existing `ENTRY_CLASS` extension mechanism
("Construct configured entry subclasses" in `queue-entries` spec). Rejected:
priority ordering is a backend-selection concern (which queue class you
configure), not a per-deployment customisation one, and every
consumer — including a non-priority queue's `QueueEntry` — benefits from a
uniform field to inspect rather than an `isinstance` check.

### Add tracked-entry priority storage, parallel to the existing pending store, not reusing the raw priority store
Add `apush_priority(entry_id, priority)` / `apop_priority()` to both
providers:

- **Redis** (`QueueProviderRedis`): a second ZSET,
  `f"{self._queue_name}:entries:pending:priority"`, storing entry-ID members
  scored by priority — same `zadd`/`zrevrange`+`zrem` shape as
  `aadd_priority`/`aget_priority` (`provider.py:506-527`), but keyed
  separately from `_entry_pending_name` (the plain list) and from
  `self._queue_name` (the raw priority store, which holds encoded values, not
  UUIDs, and must keep working unmodified for direct `add`/`aadd` callers).
- **Memory** (`QueueProviderMemory`): a second `queue.PriorityQueue`,
  `self._pending_priority`, storing `(-priority, entry_id)` tuples — the same
  negation trick as `_priority_items` (`provider.py:102`), but holding entry
  IDs like `self._pending` rather than raw values like `_priority_items`.

**Alternative considered**: reuse the existing raw priority store
(`_queue_name` ZSET / `_priority_items`) for entry IDs too, distinguishing by
value shape. Rejected: the raw store's values are opaque encoded
payloads (`self.encode(value, self._encoding)`) that the raw `aget`/`apoll`
path decodes and returns directly; mixing entry-ID members into the same
structure would make the raw API's `size`/`clear`/`peek` observe tracked
dispatch state (and vice versa) and break the "raw and tracked are
independent" invariant the proposal commits to keeping. A second, parallel
structure costs one extra Redis key / one extra in-memory queue and keeps the
two paths genuinely independent, matching how `_entry_pending_name` already
sits alongside the untouched plain-list raw store today.

### Route priority subclasses through an overridable hook, not a rewritten `aenqueue`/`adequeue`
Rather than duplicating `AsyncQueue.aenqueue()`/`adequeue()` in each priority
subclass, factor the pending-store push/pop calls in the base implementation
behind two small protected hooks:

```python
# base.py, inside AsyncQueue
async def _apush_entry(self, entry: QueueEntry) -> None:
    await self._provider.apush(entry.id)


async def _apop_entry(self) -> QueueEntry:
    return await self._provider.apop()
```

`aenqueue`/`adequeue` call `self._apush_entry(entry)` /
`self._apop_entry()` instead of the provider methods directly.
`RedisAsyncPriorityQueue`/`MemoryAsyncPriorityQueue` override just these two
hooks to call `self._provider.apush_priority(entry.id, entry.priority)` /
`self._provider.apop_priority()` (then `afind` to materialise the
`QueueEntry`, matching `apop`'s existing "pop ID, then look up" shape at
`provider.py:720-728`). Every other `AsyncQueue` method (`afind`, `alist`,
`aprune`, lifecycle transitions, worker creation) is untouched and shared.

**Alternative considered**: give `RedisAsyncPriorityQueue`/
`MemoryAsyncPriorityQueue` a full override of `aenqueue`/`adequeue`.
Rejected: `aenqueue` also does JSON validation, timestamps the entry,
`astore`s it, and sends the `entry_enqueued` signal
(`base.py:261-273`) — duplicating all of that in each priority subclass
to change one line risks the copies drifting, which is exactly the kind of
duplication `RedisAsyncPriorityQueueJson`'s existing `aadd` override (wrapping
`super().aadd()` rather than reimplementing it) already avoids on the raw
path.

### Entry-tracked `adequeue()` stays non-blocking; keep the raw path's blocking `apoll` as-is
`AsyncQueue.adequeue()` today is "best effort": `apop()` raises
`QueueEmptyException` immediately when nothing is pending (`base.py:183`),
and `AsyncQueueWorker` is what retries/waits (`worker.py:338` context). The
priority hook's `_apop_entry()` follows the same contract — no
timeout/retries parameter, matching `apop`'s signature exactly. The raw
path's blocking `apoll(timeout, retries)` (`redispqueue.py:20`) is untouched
and irrelevant here, since it operates on the separate raw priority store.

## Risks / Trade-offs

- **Two ZSETs / two priority queues per priority backend instance** (raw +
  tracked) → doubles Redis keys and in-memory structures for a priority
  queue. Mitigation: this is the direct, minimal consequence of keeping the
  raw and tracked APIs independent (a proposal commitment); the alternative
  (shared storage) trades a small memory/key cost for a real correctness
  hazard between two APIs users may use side by side.
- **`adelete`/`aexpire`-style cleanup doesn't reach the new pending-priority
  ZSET** → `AsyncQueue` has no equivalent of `EventQueue`'s `adelete`
  (`AsyncQueue` never removes an entry outside `aprune`, and `aprune`
  requires a terminal, already-dispatched entry that is by definition no
  longer in *either* pending store), so this mirrors the existing
  `_entry_pending_name` cleanup surface exactly — no new gap introduced. The
  existing `adiscard` (used only on the `QUEUED`→`FAILED` transition in
  `_areplace_entry`, `base.py:395-399`) is `apush`/`apop`-store specific and
  needs a matching `adiscard_priority`, called only in the priority hook's
  override path.
- **`entry.priority` default `0` on records written before this change** →
  restoring a pre-change durable entry through `from_dict` uses the
  dataclass field default, so old records read back as priority `0`
  (lowest), which is the correct interpretation (they predate priority
  entirely and were dispatched FIFO). No migration needed.

## Migration Plan

No data migration: `priority` is a new dataclass field with a default, and
`from_dict` already tolerates absent optional fields via the field default
(same mechanism `timeout_seconds` uses today). The new pending-priority
ZSET/`PriorityQueue` starts empty on deploy; no backfill from the existing
plain pending store is needed because in-flight entries drain through
whichever store they were pushed to before the deploy — the two stores are
never cross-read.

Rollout is a single coordinated release (library + both call sites already in
this repo); no feature flag needed since the change is additive and
backward-compatible (default priority `0` reproduces today's FIFO ordering
exactly for every existing caller).
