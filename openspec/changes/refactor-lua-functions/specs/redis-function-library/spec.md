## Purpose

Define the deployment and invocation contract for Redis-backed queue server
operations implemented through a durable Redis Function library.

## ADDED Requirements

### Requirement: Use a compatible Redis Function deployment
The Redis queue backend SHALL require Redis 7 or later and SHALL report an
actionable backend error when its required Function library cannot be loaded or
invoked because of server version or ACL configuration.

#### Scenario: Redis Functions are unavailable
- **WHEN** a Redis queue connects to a server that does not support required
  Function operations
- **THEN** the backend raises an error identifying the version or permission
  prerequisite

### Requirement: Preserve atomic provider operations
Each Redis provider operation that changes queue state SHALL execute as one
atomic server-side function invocation. Shared server-side logic SHALL not
require the client to compose multiple invocations.

#### Scenario: Invoke a multi-index mutation
- **WHEN** a queue operation changes an entry record and one or more queue
  indexes
- **THEN** observers cannot see a partially applied mutation
