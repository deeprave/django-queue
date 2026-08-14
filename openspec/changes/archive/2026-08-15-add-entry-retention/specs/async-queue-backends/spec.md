## ADDED Requirements

### Requirement: Prune a retained AsyncQueue entry
`AsyncQueue` SHALL expose `aprune_entry(entry_id)` and its synchronous
counterpart `prune_entry(entry_id)` for removing one retained terminal entry.
`BaseQueue` and `EventQueue` SHALL NOT expose these entry-retention operations.
Scheduled cleanup and explicit pruning SHALL use the same removal behavior.

#### Scenario: Prune from synchronous application code
- **WHEN** synchronous application code prunes one terminal AsyncQueue entry
- **THEN** it observes the same removal and exception behavior as
  `aprune_entry`

### Requirement: Report an identified AsyncQueue entry that does not exist
AsyncQueue entry lookup and explicit pruning SHALL raise
`QueueEntryNotFoundError` when the requested retained entry ID has no durable
record. `QueueEmptyException` SHALL remain reserved for queue-dequeue
operations, and `QueueEntryMissingError` SHALL remain specific to
reliable-delivery claim settlement.

#### Scenario: Look up an absent entry
- **WHEN** a caller retrieves an AsyncQueue entry ID whose retained record does
  not exist
- **THEN** the backend raises `QueueEntryNotFoundError`
