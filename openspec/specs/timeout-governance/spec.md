# timeout-governance Specification

## Purpose
TBD - created by archiving change add-timeout-governance. Update Purpose after archive.

## Requirements

### Requirement: Resolve a per-entry execution budget
The system SHALL resolve one execution budget for every dispatched entry, in
precedence order: the worker's configured override, then the budget carried on
the entry, then the queue's configured default, then 600 seconds. A budget MUST
be a finite, strictly positive number of seconds, wherever it was supplied from.
The system SHALL reject a budget at the point it is supplied rather than when it
is applied: one that is not a number with a type error, and one that is zero,
negative, infinite, or NaN with a value error. A configuration setting SHALL
instead raise the configuration error class its layer already uses, naming the
alias, since a settings fault is misconfiguration whichever way the value is
wrong. There SHALL be no value meaning "unbounded", and infinity is such a
value.

#### Scenario: Fall back to the built-in default
- **WHEN** a worker dispatches an entry whose queue, entry, and worker all
  specify no budget
- **THEN** it enforces a budget of 600 seconds

#### Scenario: Prefer the entry's budget over the queue default
- **WHEN** a queue defines a default budget and an entry was enqueued with its
  own budget
- **THEN** the worker enforces the entry's budget

#### Scenario: Prefer the worker override over the entry
- **WHEN** a worker defines a budget override and dispatches an entry that
  carries its own budget
- **THEN** the worker enforces its override

#### Scenario: Reject a budget that is not a number
- **WHEN** an `enqueue` call or a worker override supplies a budget that is not
  a number
- **THEN** the system raises a type error naming the offending value

#### Scenario: Reject a queue setting that is not a valid budget
- **WHEN** an alias `TIMEOUT` setting is not a number, or is zero, negative,
  infinite, or NaN
- **THEN** initialising the settings raises the configuration error class,
  naming the alias and the offending value, rather than a type or value error

#### Scenario: Reject a budget that is not finite and positive
- **WHEN** an `enqueue` call or a worker override supplies a numeric budget that
  is zero, negative, infinite, or NaN
- **THEN** the system raises a value error naming the offending value

#### Scenario: Reject a budget restored from a durable record
- **WHEN** an entry is restored from a stored record carrying a budget that is
  not finite and positive
- **THEN** restoration fails rather than dispatching under it

### Requirement: Accept a budget when enqueueing an entry
The entry-oriented enqueue operation SHALL accept an optional `timeout_seconds` keyword
naming that entry's execution budget in seconds, and MUST persist it on the
entry record. The item-oriented `add` operation creates no entry and SHALL NOT
accept a budget.

#### Scenario: Enqueue with an explicit budget
- **WHEN** a caller enqueues a payload with a `timeout_seconds` keyword
- **THEN** the returned entry records that budget in its durable representation

#### Scenario: Enqueue without a budget
- **WHEN** a caller enqueues a payload with no `timeout_seconds` keyword
- **THEN** the entry records no budget and resolution falls to the queue default

### Requirement: Abandon a handler that exceeds its budget
A worker SHALL bound each handler with the resolved budget, measured from the
point the entry is marked running. When the budget expires the worker MUST
cancel the handler, record the entry as `timeout`, and continue dispatching
subsequent entries on that queue.

#### Scenario: Time out a hung handler
- **WHEN** a handler neither returns nor extends its budget before the budget
  expires
- **THEN** the worker cancels it, stores the entry with status `timeout` and a
  non-null `finished_at`, and dispatches the next entry

#### Scenario: Leave a handler within its budget untouched
- **WHEN** a handler returns before its budget expires
- **THEN** the worker records the handler's own outcome and never marks the
  entry `timeout`

#### Scenario: A handler raises the deadline's own exception class
- **WHEN** a handler raises `TimeoutError` of its own, within its budget
- **THEN** the worker records the entry `failed` with that error, since only the
  budget actually expiring means the handler never answered

### Requirement: Count timeout outcomes separately
A worker SHALL count entries it abandoned on budget expiry separately from
succeeded, failed, and cancelled outcomes, expose that count on its snapshot,
and include it in its structured lifecycle records.

#### Scenario: Report a timeout in the worker snapshot
- **WHEN** a worker has abandoned one entry on budget expiry
- **THEN** its snapshot reports a timeout count of one, leaves its cancelled
  count unchanged, and its terminal-outcome record carries the updated count

### Requirement: Extend a budget from a live handler
The system SHALL provide a heartbeat call that a handler invokes to assert
progress, and which restarts that entry's execution budget from the moment of
the call. It MUST raise when invoked outside an active dispatch. It SHALL be
usable from anywhere within the handler's own call stack.

The call SHALL extend the execution budget only. It does not verify that the
worker still holds the entry, because no ownership boundary exists to verify
against: delivery is best effort and no other worker can reclaim an entry that
is being dispatched. Ownership validation is added when a lease exists to
validate against, and until then a heartbeat SHALL NOT be documented or
described as a liveness or ownership guarantee.

#### Scenario: Extend a long-running handler
- **WHEN** a handler calls the heartbeat before its budget expires and then
  continues working
- **THEN** the worker grants a fresh full budget from that call and does not
  time the handler out

#### Scenario: Heartbeat outside a dispatch
- **WHEN** the heartbeat is called where no entry is being dispatched
- **THEN** it raises an error rather than silently succeeding

#### Scenario: Heartbeat from a nested call
- **WHEN** a handler calls the heartbeat from a function it has called, at any
  depth below the handler itself
- **THEN** the active dispatch's budget is extended

### Requirement: Document the heartbeat as an assertion of progress
The system SHALL document that a heartbeat is made when a handler approaches its
budget and needs a further allotment, having made progress worth reporting, and
SHALL document that it is not a keepalive to be called on a timer or in a loop.
The system SHALL NOT enforce a minimum interval, since an honest frequent
heartbeat cannot be distinguished from a dishonest one.

#### Scenario: Document the caller's obligation
- **WHEN** the heartbeat is documented for application authors
- **THEN** the documentation states that a handler which heartbeats on a
  schedule has disabled its own budget

### Requirement: Resolve an event lifetime
For an event queue, `timeout_seconds` SHALL resolve as an explicit entry
value, then queue `TIMEOUT`, then 60 seconds. Each resolved lifetime MUST be
finite and strictly positive. It governs an event only while it is unclaimed;
an active listener is governed by its claim lease instead. An event queue SHALL
NOT accept a task-worker execution-budget override.

#### Scenario: Use the event lifetime default
- **WHEN** an event queue enqueues without an explicit lifetime or queue `TIMEOUT`
- **THEN** the event expires after 60 seconds if it remains unconsumed

#### Scenario: Prefer the explicit lifetime
- **WHEN** an event queue enqueue supplies a positive `timeout_seconds`
- **THEN** expiry uses that value instead of the queue default
