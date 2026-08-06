## ADDED Requirements

### Requirement: Initialize configured queues
The Django app SHALL validate and initialize every `QUEUES` alias idempotently during application setup without starting a worker.

#### Scenario: Access a configured service
- **WHEN** Django initialization completes with valid `QUEUES`
- **THEN** application code can retrieve each configured queue by alias
