## MODIFIED Requirements

### Requirement: Expose worker identity and health
The worker SHALL generate a stable UUIDv7 ID at construction and expose an
immutable snapshot of its running state, run start time, active entry ID, active
queue name, registered queue aliases, dispatch count, and terminal outcome
counters for succeeded, failed, cancelled, and timed-out entries. A
terminal-outcome counter MUST advance only after the worker has confirmed the
corresponding entry state was persisted. The run start time SHALL come from the
worker's clock, which defaults to local time and is supplied by the queue when
the queue creates the worker, so that a worker's recorded time and the
timestamps of the entries it dispatches share one basis. It SHALL be reported as
a float count of seconds since the Unix epoch, in snapshots and in structured
log records alike, matching the representation used for entry timestamps.

#### Scenario: Inspect a running worker
- **WHEN** an operator reads a worker snapshot during dispatch
- **THEN** it includes the worker ID, running state, run start time, active
  entry ID, active queue name, registered queue aliases, dispatch count, and
  terminal outcome counters

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
