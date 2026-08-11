## ADDED Requirements

### Requirement: Represent an instant exactly
The system SHALL provide an immutable instant value holding whole seconds and
whole microseconds since the Unix epoch. It MUST reject a microsecond component
outside `[0, 1_000_000)`, a negative second component, and a component that is
not a whole number, at construction rather than on use. A boolean MUST be
rejected as a component even though it is a whole number, since a flag does not
describe a moment. The epoch is therefore the earliest instant the value
represents. Two instants describing the same moment MUST compare equal
regardless of how each was constructed.

#### Scenario: Reject a microsecond component that cannot describe an instant
- **WHEN** an instant is constructed with a microsecond component of 1000000 or
  more, or below zero
- **THEN** construction fails

#### Scenario: Reject a component that is not a whole number
- **WHEN** an instant is constructed with a fractional second or microsecond
  component
- **THEN** construction fails

#### Scenario: Reject a boolean component
- **WHEN** an instant is constructed with a boolean as either component, or from
  a boolean count of seconds
- **THEN** construction fails

#### Scenario: Reject an instant before the epoch
- **WHEN** an instant is constructed with a negative second component, or from a
  negative count of seconds, or from a datetime preceding the epoch
- **THEN** construction fails

### Requirement: Construct an instant from each source the system receives
The value SHALL be constructible from a float count of seconds since the epoch,
from a pair of whole second and microsecond counts, and from a timezone-aware
datetime. A naive datetime does not identify an instant and MUST be rejected, as
MUST a count of seconds that is not finite. Constructions describing the same
moment MUST produce equal values.

Construction from a datetime MUST preserve the microseconds that datetime holds
at every magnitude the datetime itself represents, so it MUST NOT be routed
through a form less precise than either.

#### Scenario: Construct from each supported source
- **WHEN** the same moment is expressed as a float, as a second and microsecond
  pair, and as an aware datetime, and an instant is constructed from each
- **THEN** the three instants are equal

#### Scenario: Reject a datetime without a zone
- **WHEN** an instant is constructed from a naive datetime
- **THEN** construction fails

#### Scenario: Reject a count of seconds that is not finite
- **WHEN** an instant is constructed from a count of seconds that is NaN or
  infinite
- **THEN** construction fails

#### Scenario: Read a datetime without losing its microseconds
- **WHEN** an instant far beyond the present is converted to a datetime and an
  instant is constructed back from it
- **THEN** the two instants are equal

### Requirement: Convert an instant to the forms consumers need
The value SHALL convert to a float count of seconds since the epoch and to a
timezone-aware UTC datetime, through named calls in both cases. Converting to a
count of seconds and back MUST yield an equal instant for any instant a queue
can observe — a float resolves finer than one microsecond up to roughly 2^33
seconds since the epoch, and the guarantee is bounded by that. The value MUST
NOT convert to a number implicitly, so that a context expecting a duration
cannot silently accept an instant.

#### Scenario: Round-trip through a count of seconds
- **WHEN** an instant is converted to a float count of seconds and a new instant
  is constructed from that float
- **THEN** the two instants are equal

#### Scenario: Refuse to become a number implicitly
- **WHEN** an instant is used where a number is expected, without an explicit
  conversion
- **THEN** the operation fails rather than coercing

#### Scenario: Convert to a datetime
- **WHEN** an instant is converted to a datetime
- **THEN** the result is timezone-aware and describes the same moment

### Requirement: Order instants and measure between them
Instants SHALL order chronologically. Subtracting one instant from another SHALL
yield the count of seconds between them, and adding or subtracting a count of
seconds SHALL yield another instant, which MUST itself satisfy the constraints
on an instant. Addition SHALL accept the duration on either side, since the
order of operands does not change what is meant. A duration that is not finite
MUST be rejected. Adding two instants SHALL NOT be supported.

#### Scenario: Order instants within the same second
- **WHEN** instants sharing a second but differing in microseconds are sorted
- **THEN** they order by microsecond, ahead of any instant in a later second

#### Scenario: Measure elapsed time
- **WHEN** an earlier instant is subtracted from a later one
- **THEN** the result is the count of seconds between them

#### Scenario: Shift an instant by a duration
- **WHEN** a count of seconds is added to an instant, with the duration on
  either side of the operator
- **THEN** both give the same instant, that many seconds later

#### Scenario: Refuse a duration that is not finite
- **WHEN** an instant is shifted by a duration that is NaN or infinite
- **THEN** the operation fails rather than yielding a value

#### Scenario: Refuse to add two instants
- **WHEN** two instants are added
- **THEN** the operation is unsupported rather than yielding a value
