## ADDED Requirements

### Requirement: Use canonical registry terminology
The configured queue registry SHALL expose only the canonical naming vocabulary
for its public API, metadata, documentation, and configuration examples.
Registry terminology SHALL NOT repeat queue, worker, or backend context where
the receiver already establishes it.

#### Scenario: Configure and retrieve a queue service
- **WHEN** an application follows the documented registry API
- **THEN** it uses only canonical names and no superseded spelling is accepted
