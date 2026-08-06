## ADDED Requirements

### Requirement: Recover expired claims
The system SHALL return a Redis entry with an expired unacknowledged lease to
pending visibility for another worker to claim.

#### Scenario: Recover a crashed worker claim
- **WHEN** a claim lease expires without acknowledgement
- **THEN** a recovery operation makes its entry pending again

### Requirement: Provide at-least-once delivery
The Redis worker SHALL acknowledge successful or failed terminal handling and
MUST document that recovered entries can execute more than once.

#### Scenario: Acknowledge terminal work
- **WHEN** a handler records a terminal outcome
- **THEN** its claim is acknowledged and is not recovered
