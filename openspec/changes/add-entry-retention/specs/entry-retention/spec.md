## ADDED Requirements

### Requirement: Expire terminal entries
The system SHALL support an optional retention duration for succeeded and failed
entries and MUST NOT expire queued or running entries under that policy.

#### Scenario: Expire a completed entry
- **WHEN** a terminal entry exceeds its configured retention duration
- **THEN** lookup reports that the entry does not exist
