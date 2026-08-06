## Purpose

Define Django's validated registry of named queue services.

## Requirements

### Requirement: Initialise configured queues

The Django app SHALL validate and initialise every `QUEUES` alias idempotently
during application setup without starting a worker.

#### Scenario: Access a configured service

- **WHEN** Django initialisation completes with valid `QUEUES`
- **THEN** application code can retrieve each configured queue by alias
