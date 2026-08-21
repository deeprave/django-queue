## 1. Public AsyncQueue API

- [ ] 1.1 Add `available_at: ClockTime | None = None` to identified
  `AsyncQueue.aenqueue()` and synchronous `enqueue()` signatures, preserving
  existing immediate enqueue behaviour.
- [ ] 1.2 Route the argument through the entry enqueue hooks and ensure
  non-delayed queue variants accept and ignore it where required.
- [ ] 1.3 Document the queue-facing `available_at` contract and its intended
  translation from an upstream absolute scheduling instant.

## 2. Redis scheduled-entry storage

- [ ] 2.1 Add the `{queue}:entries:scheduled` ZSET naming and provider helpers
  for scheduled-entry membership.
- [ ] 2.2 Implement a Lua-backed enqueue path that atomically writes the entry
  record and selects immediate pending membership or future scheduled
  membership using Redis-authoritative time.
- [ ] 2.3 Extend ordinary and priority claim scripts to promote due scheduled
  IDs before their existing claim selection, using durable entry priority for
  priority queues.
- [ ] 2.4 Keep scheduled promotion distinct from delayed-release and lease
  recovery behaviour.
- [ ] 2.5 Include scheduled entries in Redis pending-work checks so workers
  remain active for future-only queues.

## 3. Lifecycle and cleanup correctness

- [ ] 3.1 Extend atomic Redis deletion to remove scheduled membership alongside
  durable records, pending indexes, delayed-release state, and claim state.
- [ ] 3.2 Remove scheduled membership in queued-to-terminal pre-dispatch
  cleanup paths.
- [ ] 3.3 Verify recovery, release, cancellation, and explicit deletion cannot
  leave a scheduled ID eligible after its entry record is gone.

## 4. Memory backend parity

- [ ] 4.1 Add local scheduled-entry tracking to the memory provider using its
  queue clock.
- [ ] 4.2 Promote due memory entries into ordinary FIFO or priority selection
  before claiming.
- [ ] 4.3 Include scheduled memory entries in pending-work and cleanup paths.

## 5. Tests and verification

- [ ] 5.1 Test immediate, future, and already-due `available_at` values on
  memory AsyncQueue backends.
- [ ] 5.2 Test Redis atomic delayed enqueue so a future entry cannot be claimed
  before its due instant.
- [ ] 5.3 Test due promotion, normal and priority ordering, equal-priority
  ties, and future-only pending-work reporting on Redis.
- [ ] 5.4 Test explicit deletion and queued pre-dispatch terminal transitions
  remove scheduled entries.
- [ ] 5.5 Run the focused queue/provider/worker tests, then the full lint,
  formatting, type, and test suite.
