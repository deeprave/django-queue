-------------------------- MODULE redis_claim_leases --------------------------
EXTENDS FiniteSets, TLC

(*******************************************************************************
This is a broker-neutral, finite model of the Redis claim-lease protocol.
It models atomic protocol actions, not Redis commands, clock calibration,
payloads, handler business logic, or Python execution details.

OpenSpec coverage is recorded in redis_claim_leases.coverage.md.
*******************************************************************************)

CONSTANTS Entries, Workers

Terminal == {"succeeded", "failed", "cancelled", "timeout"}
Statuses == {"absent", "queued", "running"} \cup Terminal
NoOwner == "no-owner"
Unsettled == "unsettled"

VARIABLES lifecycle, pending, owner, expired, crashed, settledBy

vars == <<lifecycle, pending, owner, expired, crashed, settledBy>>

Init ==
    /\ lifecycle = [entry \in Entries |-> "absent"]
    /\ pending = {}
    /\ owner = [entry \in Entries |-> NoOwner]
    /\ expired = {}
    /\ crashed = {}
    /\ settledBy = [entry \in Entries |-> Unsettled]

Enqueue(entry) ==
    /\ lifecycle[entry] = "absent"
    /\ lifecycle' = [lifecycle EXCEPT ![entry] = "queued"]
    /\ pending' = pending \cup {entry}
    /\ UNCHANGED <<owner, expired, crashed, settledBy>>

Claim(entry, worker) ==
    /\ entry \in pending
    /\ lifecycle[entry] = "queued"
    /\ owner[entry] = NoOwner
    /\ worker \notin crashed
    /\ pending' = pending \ {entry}
    /\ owner' = [owner EXCEPT ![entry] = worker]
    /\ UNCHANGED <<lifecycle, expired, crashed, settledBy>>

Renew(entry, worker) ==
    /\ owner[entry] = worker
    /\ worker \notin crashed
    /\ entry \in expired
    /\ expired' = expired \ {entry}
    /\ UNCHANGED <<lifecycle, pending, owner, crashed, settledBy>>

MarkRunning(entry, worker) ==
    /\ lifecycle[entry] = "queued"
    /\ owner[entry] = worker
    /\ worker \notin crashed
    /\ lifecycle' = [lifecycle EXCEPT ![entry] = "running"]
    /\ UNCHANGED <<pending, owner, expired, crashed, settledBy>>

Settle(entry, worker, outcome) ==
    /\ lifecycle[entry] = "running"
    /\ owner[entry] = worker
    /\ worker \notin crashed
    /\ outcome \in Terminal
    /\ lifecycle' = [lifecycle EXCEPT ![entry] = outcome]
    /\ owner' = [owner EXCEPT ![entry] = NoOwner]
    /\ expired' = expired \ {entry}
    /\ settledBy' = [settledBy EXCEPT ![entry] = worker]
    /\ UNCHANGED <<pending, crashed>>

Crash(worker) ==
    /\ worker \notin crashed
    /\ crashed' = crashed \cup {worker}
    /\ UNCHANGED <<lifecycle, pending, owner, expired, settledBy>>

Expire(entry) ==
    /\ owner[entry] # NoOwner
    /\ entry \notin expired
    /\ expired' = expired \cup {entry}
    /\ UNCHANGED <<lifecycle, pending, owner, crashed, settledBy>>

RecoverExpired(entry) ==
    /\ entry \in expired
    /\ owner[entry] # NoOwner
    /\ lifecycle[entry] \in {"queued", "running"}
    /\ lifecycle' = [lifecycle EXCEPT ![entry] = "queued"]
    /\ pending' = pending \cup {entry}
    /\ owner' = [owner EXCEPT ![entry] = NoOwner]
    /\ expired' = expired \ {entry}
    /\ UNCHANGED <<crashed, settledBy>>

Next ==
    \/ \E entry \in Entries : Enqueue(entry)
    \/ \E entry \in Entries, worker \in Workers : Claim(entry, worker)
    \/ \E entry \in Entries, worker \in Workers : Renew(entry, worker)
    \/ \E entry \in Entries, worker \in Workers : MarkRunning(entry, worker)
    \/ \E entry \in Entries, worker \in Workers, outcome \in Terminal :
        Settle(entry, worker, outcome)
    \/ \E worker \in Workers : Crash(worker)
    \/ \E entry \in Entries : Expire(entry)
    \/ \E entry \in Entries : RecoverExpired(entry)

TypeOK ==
    /\ lifecycle \in [Entries -> Statuses]
    /\ pending \subseteq Entries
    /\ owner \in [Entries -> (Workers \cup {NoOwner})]
    /\ expired \subseteq Entries
    /\ crashed \subseteq Workers
    /\ settledBy \in [Entries -> (Workers \cup {Unsettled})]

PendingIsUnclaimedQueued ==
    \A entry \in Entries :
        entry \in pending <=> /\ lifecycle[entry] = "queued" /\ owner[entry] = NoOwner

NonterminalIsVisibleOrClaimed ==
    \A entry \in Entries :
        lifecycle[entry] \in {"queued", "running"} =>
            entry \in pending \/ owner[entry] # NoOwner

RunningHasOwner ==
    \A entry \in Entries : lifecycle[entry] = "running" => owner[entry] # NoOwner

TerminalHasNoClaim ==
    \A entry \in Entries : lifecycle[entry] \in Terminal =>
        /\ owner[entry] = NoOwner
        /\ entry \notin pending
        /\ entry \notin expired

SettlementIsOwnedTerminal ==
    \A entry \in Entries :
        settledBy[entry] # Unsettled => lifecycle[entry] \in Terminal

RecoveredEntryIsUnsettled ==
    \A entry \in Entries :
        lifecycle[entry] = "queued" /\ settledBy[entry] # Unsettled => FALSE

Spec == Init /\ [][Next]_vars

=============================================================================
