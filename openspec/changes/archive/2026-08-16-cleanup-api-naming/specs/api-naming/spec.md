## ADDED Requirements

### Requirement: Use concise context-aware API names
Queue, worker, provider, and registry interfaces SHALL use the shortest action
name that remains unambiguous in that interface's established context. An API
SHALL NOT retain an `_entry` suffix, queue/worker prefix, or transport-specific
prefix solely to repeat information already supplied by its receiver or module.

#### Scenario: Operation has an unambiguous context
- **WHEN** an interface operates only on one kind of queue record
- **THEN** its canonical operation name omits redundant target nouns

#### Scenario: Operation would collide with a distinct raw-value operation
- **WHEN** removing a qualifier would make two different public operations use
  the same name
- **THEN** the canonical name retains the smallest qualifier necessary to
  distinguish them

### Requirement: Keep ownership internals out of the public queue API
Public queue operations SHALL NOT accept or expose worker IDs, claim owners,
provider instances, or transport-specific coordination values. Queue workers
and providers SHALL use private implementation hooks for ownership operations.

#### Scenario: Application dequeues a lifecycle record
- **WHEN** application code calls a public lifecycle-record dequeue operation
- **THEN** it does not provide or receive a worker or claim identifier

### Requirement: Remove superseded spellings completely
After a canonical name is adopted, the package SHALL NOT expose the superseded
callable, import alias, configuration key, or documentation spelling.

#### Scenario: Application uses an old spelling
- **WHEN** application code imports or invokes a removed spelling
- **THEN** the package does not provide a compatibility alias or fallback

### Requirement: Keep raw values distinct from lifecycle records
Raw queue-value operations SHALL use `add`, `get`, `poll`, `peek`, `size`, and
`clear`. Retained lifecycle-record operations SHALL use `enqueue`, `find`,
`dequeue`, `has_pending`, `alist`, and `prune`, with asynchronous counterparts
where applicable. A queue API SHALL NOT use redundant `_entry` or `_entries`
suffixes for those lifecycle operations.

#### Scenario: Inspect a retained lifecycle record
- **WHEN** application code retrieves an identified retained record
- **THEN** it calls `find` or `afind`, rather than a raw-value operation

#### Scenario: Publish an observed lifecycle record
- **WHEN** a worker publishes one immutable record to lifecycle observers
- **THEN** it calls `apublish`
