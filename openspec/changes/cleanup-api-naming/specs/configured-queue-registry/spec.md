## ADDED Requirements

### Requirement: Use canonical registry terminology
The configured queue registry SHALL expose only the canonical naming vocabulary
for its public API, metadata, documentation, and configuration examples.
Registry terminology SHALL NOT repeat queue, worker, or backend context where
the receiver already establishes it.

#### Scenario: Configure and retrieve a queue service
- **WHEN** an application follows the documented registry API
- **THEN** it uses only canonical names and no superseded spelling is accepted

### Requirement: Derive configured queue identity from its alias
The `QUEUES` mapping alias SHALL be a configured queue's only application
supplied identity. Documentation SHALL state the supported settings explicitly,
and SHALL NOT describe `queue_name` as a valid setting.

#### Scenario: Configure a queue
- **WHEN** an application defines a queue under a `QUEUES` mapping alias
- **THEN** that alias is its queue name and any lower-case `queue_name` option
  is not a supported configuration setting
