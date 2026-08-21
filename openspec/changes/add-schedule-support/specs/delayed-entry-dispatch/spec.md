## Purpose

Define durable delayed availability for identified AsyncQueue entries without
reserving a worker or changing the entry's lifecycle record.

## ADDED Requirements

### Requirement: Schedule an identified entry for future availability
An identified AsyncQueue backend that supports delayed dispatch SHALL persist a
future entry without making it eligible for dispatch before its supplied
availability instant. An entry with no availability instant, or an instant at
or before the queue's authoritative current time, SHALL be eligible immediately.
The entry SHALL remain `queued` until it is normally dispatched.

#### Scenario: Enqueue future work
- **WHEN** a caller enqueues an identified entry with a future availability instant
- **THEN** the entry is durable, remains `queued`, and no worker can claim it before that instant

#### Scenario: Enqueue immediately available work
- **WHEN** a caller omits the availability instant or supplies one at or before the queue's current time
- **THEN** the entry is eligible for ordinary dispatch immediately

### Requirement: Promote due work without reserving a worker
When scheduled work becomes due, a backend SHALL make it eligible for its
ordinary dispatch order before a worker claims it. Future work SHALL not hold a
claim, worker, execution slot, timer, or coroutine while waiting.

#### Scenario: Other work is available while an entry is future-dated
- **WHEN** a worker checks a queue containing both a future entry and an immediately available entry
- **THEN** it can dispatch the immediately available entry without claiming the future entry

#### Scenario: Scheduled work becomes due
- **WHEN** a worker checks the queue at or after an entry's availability instant
- **THEN** the entry becomes eligible through the normal claim and lifecycle path

### Requirement: Preserve priority when scheduled work becomes eligible
A priority-enabled AsyncQueue backend SHALL insert a due scheduled entry into
the same priority order used for immediately enqueued entries. Equal priorities
SHALL retain the backend's ordinary arrival-order guarantee.

#### Scenario: Promote a due high-priority entry
- **WHEN** a delayed high-priority entry becomes due while lower-priority work is pending
- **THEN** the high-priority entry is selected according to the normal priority ordering

### Requirement: Remove scheduled membership with an entry
Removing a scheduled entry, or transitioning it from `queued` to a terminal
outcome before dispatch, SHALL remove its scheduled representation so it cannot
later be dispatched.

#### Scenario: Delete future work
- **WHEN** a caller deletes a future scheduled entry
- **THEN** the entry is not subsequently made eligible for dispatch

#### Scenario: Fail future work before dispatch
- **WHEN** a queued future entry reaches a terminal pre-dispatch failure path
- **THEN** it is not subsequently made eligible for dispatch
