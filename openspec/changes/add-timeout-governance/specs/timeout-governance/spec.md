## ADDED Requirements

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
