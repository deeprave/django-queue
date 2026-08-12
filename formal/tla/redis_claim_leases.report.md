# Redis claim-lease TLC assessment

## Purpose

This report records the first bounded formal assessment of the generic
reliable-delivery protocol introduced by OpenSpec change
`add-redis-lease-recovery`. The model is broker-neutral: it represents protocol
state transitions, not Redis commands, Lua scripts, or Python implementation
details.

Related artefacts:

- [Model](redis_claim_leases.tla)
- [TLC configuration](redis_claim_leases.cfg)
- [OpenSpec coverage mapping](redis_claim_leases.coverage.md)

## Approved protocol decisions

- Recovery releases an expired claim for either a `queued` or `running` entry
  and returns the existing entry ID to pending work.
- Recovering a `queued` entry leaves it `queued`; recovering a `running` entry
  resets it to `queued` before returning it to pending work.
- Non-claim-lease backends are best effort and may lose work after dequeue.
- Redis claim-lease backends are at least once: recovery can cause duplicate
  handler execution.

The final delivery distinction will be added to the resolved
`async-queue-workers` specification when the active change is synced.

## Scope

The model covers two entries and two workers. It represents:

- enqueueing and claiming an entry;
- owned renewal and the running transition;
- owned atomic terminal settlement;
- worker crash, lease expiry, and recovery;
- the atomic race between renewal and recovery;
- rejection of stale-worker renewal, dispatch, and settlement.

The model checks that pending work is unclaimed and queued, nonterminal work is
visible or claimed, running work has an owner, terminal work has no claim, and
settlement cannot be followed by recovery.

It deliberately excludes Redis command/script mechanics, payloads, handler
business logic, priorities, capacity, TTL cleanup, clock calibration, Django
configuration, and persistence/client-error handling. It has no liveness
property: eventual recovery needs explicit fairness assumptions and is deferred.

## TLC execution

Run from the repository root:

```sh
formal/run-tlc -config formal/tla/redis_claim_leases.cfg \
  formal/tla/redis_claim_leases.tla
```

The wrapper uses the Java runtime and `tla2tools.jar` bundled with the local
TLA+ Toolbox application.

### Result

Run on 2026-08-12:

| Item | Result |
| --- | --- |
| TLC | 2.19, 8 August 2024 |
| Java | Toolbox-bundled AdoptOpenJDK 14.0.1 |
| Invariants | No error found |
| Generated states | 4,465 |
| Distinct states | 1,296 |
| Complete graph depth | 11 |
| Elapsed time | 4 seconds |

No counterexample was produced, so no counterexample triage was required.

## Interpretation

The successful run establishes that the selected safety properties hold for
this finite model and configuration. It does **not** prove the Redis
implementation or the complete application.

The next useful formal increment is a liveness model with explicit fairness:
an expired claim eventually returns to pending visibility while recovery
continues to run.
