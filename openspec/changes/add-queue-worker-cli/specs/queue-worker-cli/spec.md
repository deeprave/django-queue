## ADDED Requirements

### Requirement: Run a configured worker
The CLI SHALL import a configured worker factory, run its worker until cancelled,
and return a non-zero exit status for configuration errors.

#### Scenario: Graceful termination
- **WHEN** the worker process receives SIGTERM
- **THEN** it cancels and awaits the worker before exiting successfully
