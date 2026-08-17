## ADDED Requirements

### Requirement: Use canonical lifecycle-record operation names
AsyncQueue backend contracts SHALL use the canonical names defined by the API
naming capability for lifecycle-record operations and their asynchronous
counterparts. A qualifier SHALL remain only where it distinguishes a
lifecycle-record operation from an existing raw-value queue operation.

#### Scenario: Implement a custom backend after the naming cleanup
- **WHEN** an application implements an AsyncQueue backend
- **THEN** it implements only the canonical synchronous and asynchronous
  lifecycle-record operation names

#### Scenario: Call a canonical lifecycle-record operation
- **WHEN** application code calls a canonical lifecycle-record operation
- **THEN** the backend performs the same queue behavior previously associated
  with the superseded operation

#### Scenario: Provide the retained record collection
- **WHEN** observation or administration needs current retained records
- **THEN** an AsyncQueue provides `alist` without a redundant `_entries` suffix
