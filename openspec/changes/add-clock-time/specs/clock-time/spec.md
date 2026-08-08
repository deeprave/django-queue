## ADDED Requirements

### Requirement: Represent an instant exactly
The system SHALL provide an immutable instant value holding whole seconds and
whole microseconds since the Unix epoch. It MUST reject a microsecond component
outside `[0, 1_000_000)` and a component that is not a whole number, at
construction rather than on use. Two instants describing the same moment MUST
compare equal regardless of how each was constructed.

#### Scenario: Reject a microsecond component that cannot describe an instant
- **WHEN** an instant is constructed with a microsecond component of 1000000 or
  more, or below zero
- **THEN** construction fails

#### Scenario: Reject a component that is not a whole number
- **WHEN** an instant is constructed with a fractional second or microsecond
  component
- **THEN** construction fails

### Requirement: Construct an instant from each source the system receives
The value SHALL be constructible from a float count of seconds since the epoch,
from a pair of whole second and microsecond counts, and from a timezone-aware
datetime. A naive datetime does not identify an instant and MUST be rejected.
Constructions describing the same moment MUST produce equal values.

#### Scenario: Construct from each supported source
- **WHEN** the same moment is expressed as a float, as a second and microsecond
  pair, and as an aware datetime, and an instant is constructed from each
- **THEN** the three instants are equal

#### Scenario: Reject a datetime without a zone
- **WHEN** an instant is constructed from a naive datetime
- **THEN** construction fails

### Requirement: Convert an instant to the forms consumers need
The value SHALL convert to a float count of seconds since the epoch and to a
timezone-aware UTC datetime. Converting to a float and back MUST yield an equal
instant.

#### Scenario: Round-trip through a float
- **WHEN** an instant is converted to a float count of seconds and a new instant
  is constructed from that float
- **THEN** the two instants are equal

#### Scenario: Convert to a datetime
- **WHEN** an instant is converted to a datetime
- **THEN** the result is timezone-aware and describes the same moment

### Requirement: Order instants and measure between them
Instants SHALL order chronologically. Subtracting one instant from another SHALL
yield the count of seconds between them, and adding or subtracting a count of
seconds SHALL yield another instant. Adding two instants SHALL NOT be supported.

#### Scenario: Order instants within the same second
- **WHEN** instants sharing a second but differing in microseconds are sorted
- **THEN** they order by microsecond, ahead of any instant in a later second

#### Scenario: Measure elapsed time
- **WHEN** an earlier instant is subtracted from a later one
- **THEN** the result is the count of seconds between them

#### Scenario: Shift an instant by a duration
- **WHEN** a count of seconds is added to an instant
- **THEN** the result is an instant that many seconds later

#### Scenario: Refuse to add two instants
- **WHEN** two instants are added
- **THEN** the operation is unsupported rather than yielding a value
