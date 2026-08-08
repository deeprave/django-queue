## MODIFIED Requirements

### Requirement: Stop workers cooperatively on cancellation
The asynchronous worker SHALL continue dispatching until cancelled. On
`asyncio.CancelledError`, it MUST stop accepting new entries, allow an active
handler its configured grace period, then cancel it if necessary, set its
`running` state to `False`, and propagate cancellation to its caller. A handler
that completes within its grace period SHALL have its entry recorded as
`cancelled`; a handler that must be cancelled because the grace period expired
SHALL have its entry recorded as `timeout`.

#### Scenario: Cancel an idle worker
- **WHEN** a caller cancels a running worker while no entry is being handled
- **THEN** the worker completes cancellation, reports `running` as `False`, and
  does not dispatch another entry

#### Scenario: Cancel an active handler after its grace period
- **WHEN** a caller cancels a worker while a handler is active beyond its
  configured grace period
- **THEN** the worker cancels that handler, records its entry as `timeout`,
  reports `running` as `False`, and does not dispatch another entry
