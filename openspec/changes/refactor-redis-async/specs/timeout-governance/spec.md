## ADDED Requirements

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
