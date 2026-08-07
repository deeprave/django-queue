## 1. Queue type metadata and entry construction

- [x] 1.1 Add focused configuration tests for class-object and dotted-path
  `WORKER` and `ENTRY_CLASS` values, default resolution, invalid types, and
  metadata exclusion from strict backend constructors without extension
  instantiation during settings initialisation.
- [x] 1.2 Implement queue-level extension resolution and validation while
  preserving metadata separately from backend constructor options and exposing
  the resolved entry class through a lazy entry-factory boundary.
- [x] 1.3 Update memory and Redis entry-oriented backends to create and restore
  the resolved `QueueEntry` subclass; add lifecycle and JSON round-trip tests
  for a minimal custom entry subtype.

## 2. Lazy per-queue worker activation and ASGI lifecycle

- [x] 2.1 Extend `runqueues` to request each active alias's worker from its queue
  class without instantiation, then activate one worker only after its queue
  has pending work; add command tests for idle queues, specialised worker
  selection on activity, and invalid-worker failure at activation.
- [x] 2.2 Replace ASGI's aggregate eager worker with per-alias lazy worker
  startup triggered by local enqueue observation; add lifespan tests for idle
  aliases, one-worker-per-alias startup, queue-specific worker selection, and
  cooperative shutdown.
- [x] 2.3 Document the local-only ASGI enqueue trigger and retain the existing
  production warning and external `runqueues` guidance for shared backends.

## 3. Public contract and validation

- [x] 3.1 Export and document the optional `WORKER` and `ENTRY_CLASS` settings,
  their defaults, class/subclass contracts, and custom-entry persistence
  requirements.
- [x] 3.2 Run Ruff, focused and full pytest suites, and strict OpenSpec
  validation; mark tasks complete only after the corresponding behaviour is
  verified.
