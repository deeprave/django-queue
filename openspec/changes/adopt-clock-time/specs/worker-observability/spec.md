## MODIFIED Requirements

### Requirement: Expose worker identity and health
The worker SHALL generate a stable UUIDv7 ID at construction and expose an
immutable snapshot of its running state, run start time, active entry ID, active
queue name, registered queue aliases, dispatch count, and terminal outcome
counters for succeeded, failed, and cancelled entries. A terminal-outcome
counter MUST advance only after the worker has confirmed the corresponding entry
state was persisted.

The run start time SHALL come from the worker's clock, which defaults to local
time and is supplied by the queue when the queue creates the worker, so that a
worker's recorded time and the timestamps of the entries it dispatches share one
basis. A snapshot SHALL hold it as a `ClockTime`. A structured log record SHALL
carry its count of seconds, since a record must be serialisable, matching the
form entry timestamps are stored in.

A snapshot SHALL also report how long the worker has been running, as a count of
seconds measured on the worker's own clock when the snapshot is taken, so a
reader needs no second source of time to interpret it. Once the worker has
stopped, that duration SHALL report how long it ran rather than continuing to
grow, and it SHALL be absent if the clock has moved back behind the run start.
Structured log records SHALL carry that duration, and a terminal-outcome record
SHALL also carry how long the entry it describes waited and ran.

#### Scenario: Inspect a running worker
- **WHEN** an operator reads a worker snapshot during dispatch
- **THEN** it includes the worker ID, running state, run start time, how long it
  has been running, active entry ID, active queue name, registered queue
  aliases, dispatch count, and terminal outcome counters

#### Scenario: Report no duration when the clock moves behind the start
- **WHEN** a worker's clock is recalibrated to an instant preceding its run
  start and a snapshot is read
- **THEN** the running duration is absent rather than negative

#### Scenario: Report a stopped worker's run length
- **WHEN** a worker has left its dispatch loop and a snapshot is read twice, at
  different moments
- **THEN** both report the same duration, being how long the worker ran

#### Scenario: Report an entry's durations when its outcome is recorded
- **WHEN** a worker records an entry's terminal outcome
- **THEN** the structured record for that outcome carries how long the entry
  waited before dispatch and how long its handler ran

#### Scenario: Persist a terminal outcome
- **WHEN** a worker confirms an entry's terminal outcome was recorded
- **THEN** its next snapshot clears the active entry ID and increments exactly
  the corresponding terminal-outcome counter

#### Scenario: Terminal outcome cannot be confirmed
- **WHEN** a worker cannot confirm either the requested terminal outcome or its
  safe persistence-failure outcome
- **THEN** it does not increment a terminal-outcome counter for that entry

#### Scenario: Share a time basis with dispatched entries
- **WHEN** a queue whose clock differs from local UTC creates a worker, and that
  worker dispatches an entry
- **THEN** the worker's run start time and the entry's `dispatched_at` come from
  that same queue clock, and the run start time never follows the dispatch time
  of an entry the worker dispatched
