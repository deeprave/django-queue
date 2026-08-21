"""Redis implementation of the queue-provider protocol.

This module deliberately contains the Redis connection, key, script, and
claim machinery.  Queue classes build task or event lifecycle semantics on
top of this provider; they do not need to know how Redis represents them.
"""

from __future__ import annotations

import asyncio
import codecs
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import redis
import redis.asyncio as async_redis
from asgiref.sync import async_to_sync

from django_queue.backends.exceptions import (
    InvalidQueueBackendError,
    QueueClaimConflictError,
    QueueEmptyException,
    QueueEncodingException,
    QueueEntryExpiredError,
    QueueEntryMissingError,
    QueueEntryNotFoundError,
    QueueFullException,
    QueueValueError,
)
from django_queue.clock import (
    MICROSECONDS_PER_SECOND,
    QueueClock,
    QueueClockError,
    RedisQueueClock,
)
from django_queue.entries import QueueEntry, QueueEntryStatus, validate_budget

logger = logging.getLogger(__name__)

_PRIORITY_SEQUENCE_SPACE = 2**32

# This queue packs priority and an arrival-order sequence number into one
# ZSET score: `priority * _PRIORITY_SEQUENCE_SPACE - sequence`. A double's
# exact-integer range is 2**53, so `priority` must stay within `2**53 //
# 2**32 == 2**21` in magnitude or the packing loses precision -- silently,
# not by raising, since the corrupted value is still a valid float. This
# bound is intentionally far below that edge: no realistic dispatch-priority
# use needs six-figure values, and staying well clear of the edge leaves
# headroom without requiring callers to reason about IEEE 754 exactness.
#
# Enforced here, at the Redis priority provider's own write points
# (apush_priority, astore_and_push_priority), not in QueueEntry itself --
# a plain (non-priority) AsyncQueue/EventQueue MUST ignore `priority`
# entirely per spec, and QueueEntry has no way to know which backend an
# entry is destined for, so it must never reject a value on this backend's
# behalf.
MAX_PRIORITY_MAGNITUDE = 100_000


def validate_redis_priority_magnitude(priority: int) -> int:
    """Return *priority*, or raise if it would break this queue's ZSET
    score-packing exactness (see `MAX_PRIORITY_MAGNITUDE` above)."""
    if abs(priority) > MAX_PRIORITY_MAGNITUDE:
        raise ValueError(
            f"Queue entry priority must be within +/-{MAX_PRIORITY_MAGNITUDE} "
            f"for a priority-variant Redis queue, not {priority!r}"
        )
    return priority


_CLAIM_SCRIPT = b"""
    local now = redis.call("TIME")
    local now_us = tonumber(now[1]) * 1000000 + tonumber(now[2])
    local earliest = redis.call("ZRANGEBYSCORE", KEYS[7], "-inf", now_us, "WITHSCORES", "LIMIT", 0, 1)
    if #earliest > 0 then
        local scheduled = redis.call("ZRANGEBYSCORE", KEYS[7], earliest[2], earliest[2])
        for index = 1, #scheduled do
            local raw_entry = redis.call("GET", KEYS[5] .. scheduled[index])
            local ok, entry = pcall(cjson.decode, raw_entry)
            if ok and type(entry) == "table" and entry.status == "queued" then
                if ARGV[3] == "1" then redis.call("LPUSH", KEYS[1], scheduled[index])
                else redis.call("RPUSH", KEYS[1], scheduled[index]) end
                redis.call("ZREM", KEYS[7], scheduled[index])
                break
            end
            redis.call("ZREM", KEYS[7], scheduled[index])
        end
    end
    local delayed = redis.call("ZRANGEBYSCORE", KEYS[2], "-inf", now_us)
    for index = 1, #delayed do
        if ARGV[3] == "1" then
            redis.call("LPUSH", KEYS[1], delayed[index])
        else
            redis.call("RPUSH", KEYS[1], delayed[index])
        end
        redis.call("ZREM", KEYS[2], delayed[index])
    end
    local entry_id
    if ARGV[3] == "1" then
        entry_id = redis.call("RPOP", KEYS[1])
    else
        entry_id = redis.call("LPOP", KEYS[1])
    end
    if not entry_id then
        return {"empty", ""}
    end
    local remaining
    if ARGV[4] == "1" then
        local expiry_deadline = redis.call("ZSCORE", KEYS[6], entry_id)
        if not expiry_deadline or tonumber(expiry_deadline) <= now_us then
            redis.call("DEL", KEYS[5] .. entry_id)
            redis.call("ZREM", KEYS[2], entry_id)
            redis.call("ZREM", KEYS[4], entry_id)
            redis.call("ZREM", KEYS[6], entry_id)
            return {"expired", entry_id}
        end
        remaining = tonumber(expiry_deadline) - now_us
        redis.call("ZREM", KEYS[6], entry_id)
    end
    local claim_key = KEYS[3] .. entry_id
    local deadline = now_us + tonumber(ARGV[2])
    local claim = cjson.encode({
        worker_id = ARGV[1],
        claimed_at = {seconds = tonumber(now[1]), microseconds = tonumber(now[2])},
        lease_deadline = deadline
    })
    if remaining then
        claim = cjson.encode({
            worker_id = ARGV[1],
            claimed_at = {seconds = tonumber(now[1]), microseconds = tonumber(now[2])},
            lease_deadline = deadline,
            unclaimed_remaining_us = remaining
        })
    end
    if redis.call("SET", claim_key, claim, "NX") then
        redis.call("ZADD", KEYS[4], deadline, entry_id)
        return {"claimed", entry_id}
    end
    if ARGV[3] == "1" then
        redis.call("RPUSH", KEYS[1], entry_id)
    else
        redis.call("LPUSH", KEYS[1], entry_id)
    end
    return {"conflict", entry_id}
"""

# Identical to _CLAIM_SCRIPT except the pop step: a priority-variant queue's
# tracked entries live in the priority ZSET (KEYS[7]), never the plain list,
# so this is the only claim path that can ever find them there. Tries the
# plain list first (delayed-retry recoveries always land back there,
# regardless of variant -- see the loop above -- and should not be starved
# by fresh priority pushes), falling back to the highest-scored priority
# ZSET member (same ZREVRANGE/ZREM shape as apop_priority) only when the
# plain list is empty. Everything from claim_key onward is unchanged.
_CLAIM_SCRIPT_WITH_PRIORITY = b"""
    local now = redis.call("TIME")
    local now_us = tonumber(now[1]) * 1000000 + tonumber(now[2])
    local earliest = redis.call("ZRANGEBYSCORE", KEYS[9], "-inf", now_us, "WITHSCORES", "LIMIT", 0, 1)
    if #earliest > 0 then
        local scheduled = redis.call("ZRANGEBYSCORE", KEYS[9], earliest[2], earliest[2])
        local selected_id
        local selected_priority
        for index = 1, #scheduled do
            local raw_entry = redis.call("GET", KEYS[5] .. scheduled[index])
            local ok, entry = pcall(cjson.decode, raw_entry)
            if ok and type(entry) == "table" and entry.status == "queued" then
                local priority = tonumber(entry.priority) or 0
                if not selected_id or priority > selected_priority then
                    selected_id, selected_priority = scheduled[index], priority
                end
            else
                redis.call("ZREM", KEYS[9], scheduled[index])
            end
        end
        if selected_id then
            local sequence = redis.call("INCR", KEYS[8])
            redis.call("ZADD", KEYS[7], selected_priority * ARGV[5] - sequence, selected_id)
            redis.call("ZREM", KEYS[9], selected_id)
        end
    end
    local delayed = redis.call("ZRANGEBYSCORE", KEYS[2], "-inf", now_us)
    for index = 1, #delayed do
        if ARGV[3] == "1" then
            redis.call("LPUSH", KEYS[1], delayed[index])
        else
            redis.call("RPUSH", KEYS[1], delayed[index])
        end
        redis.call("ZREM", KEYS[2], delayed[index])
    end
    local entry_id
    local priority_score
    if ARGV[3] == "1" then
        entry_id = redis.call("RPOP", KEYS[1])
    else
        entry_id = redis.call("LPOP", KEYS[1])
    end
    if not entry_id then
        local top = redis.call("ZREVRANGE", KEYS[7], 0, 0, "WITHSCORES")
        if #top > 0 then
            entry_id = top[1]
            priority_score = top[2]
            redis.call("ZREM", KEYS[7], entry_id)
            -- The sequence key is reset only once this claim attempt
            -- reaches a genuinely terminal outcome for this entry (below,
            -- at "expired" and "claimed") -- not here. A conflict requeues
            -- this same entry with its original, pre-reset score; resetting
            -- now would let a later, unrelated push get a small sequence
            -- number that outranks this still-pending entry's now-stale
            -- one at the same priority tier, corrupting arrival order for
            -- an entry that was never actually removed.
        end
    end
    if not entry_id then
        return {"empty", ""}
    end
    local remaining
    if ARGV[4] == "1" then
        local expiry_deadline = redis.call("ZSCORE", KEYS[6], entry_id)
        if not expiry_deadline or tonumber(expiry_deadline) <= now_us then
            redis.call("DEL", KEYS[5] .. entry_id)
            redis.call("ZREM", KEYS[2], entry_id)
            redis.call("ZREM", KEYS[4], entry_id)
            redis.call("ZREM", KEYS[6], entry_id)
            if priority_score and redis.call("ZCARD", KEYS[7]) == 0 then
                redis.call("SET", KEYS[8], 0, "XX")
            end
            return {"expired", entry_id}
        end
        remaining = tonumber(expiry_deadline) - now_us
        redis.call("ZREM", KEYS[6], entry_id)
    end
    local claim_key = KEYS[3] .. entry_id
    local deadline = now_us + tonumber(ARGV[2])
    local claim = cjson.encode({
        worker_id = ARGV[1],
        claimed_at = {seconds = tonumber(now[1]), microseconds = tonumber(now[2])},
        lease_deadline = deadline
    })
    if remaining then
        claim = cjson.encode({
            worker_id = ARGV[1],
            claimed_at = {seconds = tonumber(now[1]), microseconds = tonumber(now[2])},
            lease_deadline = deadline,
            unclaimed_remaining_us = remaining
        })
    end
    if redis.call("SET", claim_key, claim, "NX") then
        redis.call("ZADD", KEYS[4], deadline, entry_id)
        if priority_score and redis.call("ZCARD", KEYS[7]) == 0 then
            redis.call("SET", KEYS[8], 0, "XX")
        end
        return {"claimed", entry_id}
    end
    if priority_score then
        redis.call("ZADD", KEYS[7], priority_score, entry_id)
    elseif ARGV[3] == "1" then
        redis.call("RPUSH", KEYS[1], entry_id)
    else
        redis.call("LPUSH", KEYS[1], entry_id)
    end
    return {"conflict", entry_id}
"""

_DEQUEUE_EVENT_SCRIPT = b"""
    local now = redis.call("TIME")
    local now_us = tonumber(now[1]) * 1000000 + tonumber(now[2])
    local delayed = redis.call("ZRANGEBYSCORE", KEYS[2], "-inf", now_us)
    for index = 1, #delayed do
        if ARGV[1] == "1" then
            redis.call("LPUSH", KEYS[1], delayed[index])
        else
            redis.call("RPUSH", KEYS[1], delayed[index])
        end
        redis.call("ZREM", KEYS[2], delayed[index])
    end
    local pending_count = redis.call("LLEN", KEYS[1])
    for _ = 1, pending_count do
        local entry_id
        if ARGV[1] == "1" then
            entry_id = redis.call("RPOP", KEYS[1])
        else
            entry_id = redis.call("LPOP", KEYS[1])
        end
        if not entry_id then break end
        local claim_key = KEYS[3] .. entry_id
        if redis.call("GET", claim_key) then
            if ARGV[1] == "1" then
                redis.call("LPUSH", KEYS[1], entry_id)
            else
                redis.call("RPUSH", KEYS[1], entry_id)
            end
        else
            local entry_key = KEYS[5] .. entry_id
            local raw_entry = redis.call("GET", entry_key)
            local expiry_deadline = redis.call("ZSCORE", KEYS[6], entry_id)
            if raw_entry and expiry_deadline and tonumber(expiry_deadline) > now_us then
                redis.call("DEL", entry_key)
                redis.call("ZREM", KEYS[2], entry_id)
                redis.call("ZREM", KEYS[4], entry_id)
                redis.call("ZREM", KEYS[6], entry_id)
                redis.call("LREM", KEYS[1], 0, entry_id)
                return {"dequeued", raw_entry}
            end
            redis.call("DEL", entry_key)
            redis.call("ZREM", KEYS[2], entry_id)
            redis.call("ZREM", KEYS[4], entry_id)
            redis.call("ZREM", KEYS[6], entry_id)
            redis.call("LREM", KEYS[1], 0, entry_id)
        end
    end
    return {"empty", ""}
"""

_RENEW_SCRIPT = b"""
    local raw = redis.call("GET", KEYS[1])
    if not raw then return 0 end
    local ok, claim = pcall(cjson.decode, raw)
    if not ok or type(claim) ~= "table" or claim.worker_id ~= ARGV[1] then return 0 end
    local now = redis.call("TIME")
    local now_us = tonumber(now[1]) * 1000000 + tonumber(now[2])
    if type(claim.lease_deadline) ~= "number" or claim.lease_deadline <= now_us then return 0 end
    local deadline = now_us + tonumber(ARGV[2])
    claim.lease_deadline = deadline
    redis.call("SET", KEYS[1], cjson.encode(claim))
    redis.call("ZADD", KEYS[2], deadline, ARGV[3])
    return 1
"""

_RELEASE_SCRIPT = b"""
    local raw = redis.call("GET", KEYS[1])
    if not raw then return 0 end
    local ok, claim = pcall(cjson.decode, raw)
    if not ok or type(claim) ~= "table" or claim.worker_id ~= ARGV[1] then return 0 end
    local now = redis.call("TIME")
    local now_us = tonumber(now[1]) * 1000000 + tonumber(now[2])
    local available_at = now_us + tonumber(ARGV[3])
    redis.call("DEL", KEYS[1])
    redis.call("ZREM", KEYS[2], ARGV[2])
    redis.call("ZADD", KEYS[3], available_at, ARGV[2])
    if type(claim.unclaimed_remaining_us) == "number" then
        redis.call("ZADD", KEYS[4], now_us + claim.unclaimed_remaining_us, ARGV[2])
    end
    return 1
"""

# arelease always parks the released entry on the plain delayed set --
# correct for a plain FIFO/stack queue, but _CLAIM_SCRIPT_WITH_PRIORITY
# promotes the plain delayed/pending list to claimable status
# unconditionally, before ever checking the priority ZSET. A released
# low-priority entry could therefore jump ahead of a genuinely
# higher-priority entry still waiting in the ZSET -- a real priority
# inversion. This variant re-inserts the released entry into the priority
# ZSET instead, scored the same way apush_priority/_CLAIM_SCRIPT_WITH_PRIORITY
# encode it (priority * sequence-space - sequence). The entry's priority is
# not carried on the claim JSON, so it is read off the durable record
# (KEYS[6] .. entry_id); guarded with `or 0` in case a record somehow lacks
# the field (see the same defensive pattern in _RECOVER_SCRIPT_WITH_PRIORITY).
# The requested delay (ARGV[3] on the plain _RELEASE_SCRIPT) is deliberately
# not honoured here: the priority ZSET has no "not yet due" concept the way
# the delayed set does, and the only caller (RedisAsyncQueueWorker._mark_running,
# via queue.arelease) passes an effectively-instant delay anyway.
_RELEASE_SCRIPT_WITH_PRIORITY = b"""
    local raw = redis.call("GET", KEYS[1])
    if not raw then return 0 end
    local ok, claim = pcall(cjson.decode, raw)
    if not ok or type(claim) ~= "table" or claim.worker_id ~= ARGV[1] then return 0 end
    local raw_entry = redis.call("GET", KEYS[6] .. ARGV[2])
    local entry_ok, entry = pcall(cjson.decode, raw_entry)
    local priority = (entry_ok and type(entry) == "table") and tonumber(entry.priority) or 0
    local now = redis.call("TIME")
    local now_us = tonumber(now[1]) * 1000000 + tonumber(now[2])
    redis.call("DEL", KEYS[1])
    redis.call("ZREM", KEYS[2], ARGV[2])
    local sequence = redis.call("INCR", KEYS[7])
    local score = priority * ARGV[3] - sequence
    redis.call("ZADD", KEYS[5], score, ARGV[2])
    if type(claim.unclaimed_remaining_us) == "number" then
        redis.call("ZADD", KEYS[4], now_us + claim.unclaimed_remaining_us, ARGV[2])
    end
    return 1
"""

_REMOVE_SCRIPT = b"""
    local raw = redis.call("GET", KEYS[1])
    if not raw then return 0 end
    local ok, claim = pcall(cjson.decode, raw)
    if not ok or type(claim) ~= "table" or claim.worker_id ~= ARGV[1] then return 0 end
    redis.call("DEL", KEYS[1])
    redis.call("ZREM", KEYS[2], ARGV[2])
    redis.call("ZREM", KEYS[3], ARGV[2])
    redis.call("LREM", KEYS[4], 0, ARGV[2])
    redis.call("ZREM", KEYS[6], ARGV[2])
    return redis.call("DEL", KEYS[5])
"""

_EXPIRE_SCRIPT = b"""
    if redis.call("GET", KEYS[1]) then return 0 end
    local now = redis.call("TIME")
    local now_us = tonumber(now[1]) * 1000000 + tonumber(now[2])
    local deadline = redis.call("ZSCORE", KEYS[6], ARGV[1])
    if not deadline or tonumber(deadline) > now_us then return 0 end
    local removed = redis.call("DEL", KEYS[2])
    if removed == 0 then return 0 end
    redis.call("LREM", KEYS[3], 0, ARGV[1])
    redis.call("ZREM", KEYS[4], ARGV[1])
    redis.call("ZREM", KEYS[5], ARGV[1])
    redis.call("ZREM", KEYS[6], ARGV[1])
    return 1
"""

_ACK_SCRIPT = b"""
    local raw_claim = redis.call("GET", KEYS[1])
    if not raw_claim then return 0 end
    local ok, claim = pcall(cjson.decode, raw_claim)
    if not ok or type(claim) ~= "table" or claim.worker_id ~= ARGV[1] then return 0 end
    redis.call("ZREM", KEYS[2], ARGV[2])
    return redis.call("DEL", KEYS[1])
"""

_MARK_RUNNING_SCRIPT = b"""
    local raw_claim = redis.call("GET", KEYS[1])
    if not raw_claim then return 0 end
    local ok, claim = pcall(cjson.decode, raw_claim)
    if not ok or type(claim) ~= "table" or claim.worker_id ~= ARGV[1] then return 0 end
    local raw_entry = redis.call("GET", KEYS[2])
    local entry_ok, entry = pcall(cjson.decode, raw_entry)
    if not entry_ok or type(entry) ~= "table" or entry.status ~= "queued" then return 0 end
    redis.call("SET", KEYS[2], ARGV[2])
    return 1
"""

_SETTLE_SCRIPT = b"""
    local raw_claim = redis.call("GET", KEYS[1])
    if not raw_claim then return 0 end
    local ok, claim = pcall(cjson.decode, raw_claim)
    if not ok or type(claim) ~= "table" or claim.worker_id ~= ARGV[1] then return 0 end
    local raw_entry = redis.call("GET", KEYS[3])
    local entry_ok, entry = pcall(cjson.decode, raw_entry)
    if not entry_ok or type(entry) ~= "table" or entry.status ~= "running" then return 0 end
    redis.call("SET", KEYS[3], ARGV[3])
    redis.call("ZREM", KEYS[2], ARGV[2])
    return redis.call("DEL", KEYS[1])
"""

_RECOVER_SCRIPT = b"""
    local now = redis.call("TIME")
    local deadline = tonumber(now[1]) * 1000000 + tonumber(now[2])
    local ids = redis.call("ZRANGEBYSCORE", KEYS[1], "-inf", deadline, "LIMIT", 0, ARGV[2])
    local recovered = 0
    local discarded = 0
    for index = 1, #ids do
        local entry_id = ids[index]
        local claim_key = KEYS[2] .. entry_id
        local raw_claim = redis.call("GET", claim_key)
        local ok, claim = pcall(cjson.decode, raw_claim)
        local lease_deadline = ok and type(claim) == "table" and tonumber(claim.lease_deadline)
        if not lease_deadline or lease_deadline <= deadline then
            local raw_entry = redis.call("GET", KEYS[4] .. entry_id)
            local entry_ok, entry = pcall(cjson.decode, raw_entry)
            if entry_ok and type(entry) == "table" and (entry.status == "queued" or entry.status == "running") then
                entry.status = "queued"
                entry.dispatched_at = cjson.null
                entry.finished_at = cjson.null
                entry.result = cjson.null
                entry.error = cjson.null
                redis.call("SET", KEYS[4] .. entry_id, cjson.encode(entry))
                if type(claim.unclaimed_remaining_us) == "number" then
                    redis.call("ZADD", KEYS[5], deadline + claim.unclaimed_remaining_us, entry_id)
                    if ARGV[1] == "1" then redis.call("LPUSH", KEYS[3], entry_id)
                    else redis.call("RPUSH", KEYS[3], entry_id) end
                    recovered = recovered + 1
                else
                    if ARGV[1] == "1" then redis.call("LPUSH", KEYS[3], entry_id)
                    else redis.call("RPUSH", KEYS[3], entry_id) end
                    recovered = recovered + 1
                end
            else
                discarded = discarded + 1
            end
            redis.call("DEL", claim_key)
            redis.call("ZREM", KEYS[1], entry_id)
        else
            redis.call("ZADD", KEYS[1], lease_deadline, entry_id)
        end
    end
    return {recovered, discarded}
"""

# Identical to _RECOVER_SCRIPT except where a recovered entry is redelivered:
# a priority-variant queue's entries only ever come from and return to the
# priority ZSET (KEYS[6]), never the plain pending list -- pushing a
# recovered priority entry to the plain list the way _RECOVER_SCRIPT does
# would silently drop it back to FIFO dispatch, losing the priority
# ordering it was originally enqueued with. The decoded entry record
# already carries its own `priority` field (from QueueEntry.to_dict), so no
# extra argument is needed to know what score to give it; a fresh sequence
# number (same INCR-and-fold-into-score shape as apush_priority) keeps it
# correctly ordered against whatever else is currently pending.
# `entry.priority` is guarded with `or 0` (same defensive pattern as
# _RELEASE_SCRIPT_WITH_PRIORITY) in case a record was ever written by
# something other than QueueEntry.to_dict() -- a hand-crafted record, a
# future migration/backfill script -- and so lacks the field; without the
# guard, `tonumber(nil)` yields nil and the multiplication crashes with
# "attempt to perform arithmetic on a nil value" instead of just treating
# the entry as unprioritised.
_RECOVER_SCRIPT_WITH_PRIORITY = b"""
    local now = redis.call("TIME")
    local deadline = tonumber(now[1]) * 1000000 + tonumber(now[2])
    local ids = redis.call("ZRANGEBYSCORE", KEYS[1], "-inf", deadline, "LIMIT", 0, ARGV[2])
    local recovered = 0
    local discarded = 0
    for index = 1, #ids do
        local entry_id = ids[index]
        local claim_key = KEYS[2] .. entry_id
        local raw_claim = redis.call("GET", claim_key)
        local ok, claim = pcall(cjson.decode, raw_claim)
        local lease_deadline = ok and type(claim) == "table" and tonumber(claim.lease_deadline)
        if not lease_deadline or lease_deadline <= deadline then
            local raw_entry = redis.call("GET", KEYS[4] .. entry_id)
            local entry_ok, entry = pcall(cjson.decode, raw_entry)
            if entry_ok and type(entry) == "table" and (entry.status == "queued" or entry.status == "running") then
                entry.status = "queued"
                entry.dispatched_at = cjson.null
                entry.finished_at = cjson.null
                entry.result = cjson.null
                entry.error = cjson.null
                redis.call("SET", KEYS[4] .. entry_id, cjson.encode(entry))
                if type(claim.unclaimed_remaining_us) == "number" then
                    redis.call("ZADD", KEYS[5], deadline + claim.unclaimed_remaining_us, entry_id)
                end
                local sequence = redis.call("INCR", KEYS[7])
                local priority = tonumber(entry.priority) or 0
                local score = priority * ARGV[3] - sequence
                redis.call("ZADD", KEYS[6], score, entry_id)
                recovered = recovered + 1
            else
                discarded = discarded + 1
            end
            redis.call("DEL", claim_key)
            redis.call("ZREM", KEYS[1], entry_id)
        else
            redis.call("ZADD", KEYS[1], lease_deadline, entry_id)
        end
    end
    return {recovered, discarded}
"""

_PRUNE_SCRIPT = b"""
    local raw_entry = redis.call("GET", KEYS[1])
    if not raw_entry then return 0 end
    local ok, entry = pcall(cjson.decode, raw_entry)
    if not ok or type(entry) ~= "table" then return 0 end
    if entry.status ~= "succeeded" and entry.status ~= "failed"
        and entry.status ~= "cancelled" and entry.status ~= "timeout" then return -1 end
    redis.call("LREM", KEYS[2], 0, ARGV[1])
    if redis.call("DEL", KEYS[1]) == 0 then return 0 end
    return raw_entry
"""

_PUSH_PRIORITY_SCRIPT = b"""
    local sequence = redis.call("INCR", KEYS[2])
    local score = tonumber(ARGV[1]) - sequence
    redis.call("ZADD", KEYS[1], score, ARGV[2])
    return sequence
"""

_POP_PRIORITY_SCRIPT = b"""
    local top = redis.call("ZREVRANGE", KEYS[1], 0, 0)
    if #top == 0 then return false end
    redis.call("ZREM", KEYS[1], top[1])
    if redis.call("ZCARD", KEYS[1]) == 0 then
        redis.call("SET", KEYS[2], 0, "XX")
    end
    return top[1]
"""

_DISCARD_PRIORITY_SCRIPT = b"""
    redis.call("ZREM", KEYS[1], ARGV[1])
    if redis.call("ZCARD", KEYS[1]) == 0 then
        redis.call("SET", KEYS[2], 0, "XX")
    end
"""

# astore()/astore_event() and apush()/apush_priority() were previously two
# (or, for an event with a timeout, three) separate round-trips -- a crash
# or connection loss between them left a durable entry record with no
# pending-store index pointing to it: a silent, permanent orphan, never
# claimed, never dequeued, discoverable only by afind()ing its exact ID.
# The reverse (an index entry with no record) is already self-healing --
# apop/aclaim's subsequent afind() raises a named, caught exception -- but
# this direction had no such safety net. One script per store+push
# combination closes the window entirely.
_STORE_AND_PUSH_SCRIPT = b"""
    redis.call("SET", KEYS[1], ARGV[1])
    if ARGV[2] == "1" then
        redis.call("LPUSH", KEYS[2], ARGV[3])
    else
        redis.call("RPUSH", KEYS[2], ARGV[3])
    end
"""

_STORE_AND_PUSH_PRIORITY_SCRIPT = b"""
    redis.call("SET", KEYS[1], ARGV[1])
    local sequence = redis.call("INCR", KEYS[3])
    local score = tonumber(ARGV[3]) - sequence
    redis.call("ZADD", KEYS[2], score, ARGV[2])
"""

_STORE_AVAILABLE_SCRIPT = b"""
    local now = redis.call("TIME")
    local now_us = tonumber(now[1]) * 1000000 + tonumber(now[2])
    redis.call("SET", KEYS[1], ARGV[1])
    if tonumber(ARGV[3]) > now_us then
        redis.call("ZADD", KEYS[3], ARGV[3], ARGV[2])
    elseif ARGV[5] == "1" then
        local sequence = redis.call("INCR", KEYS[5])
        redis.call("ZADD", KEYS[4], tonumber(ARGV[4]) - sequence, ARGV[2])
    elseif ARGV[6] == "1" then
        redis.call("LPUSH", KEYS[2], ARGV[2])
    else
        redis.call("RPUSH", KEYS[2], ARGV[2])
    end
"""

_PROMOTE_SCHEDULED_SCRIPT = b"""
    local now = redis.call("TIME")
    local now_us = tonumber(now[1]) * 1000000 + tonumber(now[2])
    local earliest = redis.call("ZRANGEBYSCORE", KEYS[1], "-inf", now_us, "WITHSCORES", "LIMIT", 0, 1)
    if #earliest == 0 then return false end
    local ids = redis.call("ZRANGEBYSCORE", KEYS[1], earliest[2], earliest[2])
    for index = 1, #ids do
        local raw_entry = redis.call("GET", KEYS[2] .. ids[index])
        local ok, entry = pcall(cjson.decode, raw_entry)
        if ok and type(entry) == "table" and entry.status == "queued" then
            if ARGV[1] == "1" then redis.call("LPUSH", KEYS[3], ids[index])
            else redis.call("RPUSH", KEYS[3], ids[index]) end
            redis.call("ZREM", KEYS[1], ids[index])
            return ids[index]
        end
        redis.call("ZREM", KEYS[1], ids[index])
    end
    return false
"""

_PROMOTE_SCHEDULED_PRIORITY_SCRIPT = b"""
    local now = redis.call("TIME")
    local now_us = tonumber(now[1]) * 1000000 + tonumber(now[2])
    local earliest = redis.call("ZRANGEBYSCORE", KEYS[1], "-inf", now_us, "WITHSCORES", "LIMIT", 0, 1)
    if #earliest == 0 then return false end
    local ids = redis.call("ZRANGEBYSCORE", KEYS[1], earliest[2], earliest[2])
    local selected_id
    local selected_priority
    for index = 1, #ids do
        local raw_entry = redis.call("GET", KEYS[2] .. ids[index])
        local ok, entry = pcall(cjson.decode, raw_entry)
        if ok and type(entry) == "table" and entry.status == "queued" then
            local priority = tonumber(entry.priority) or 0
            if not selected_id or priority > selected_priority then
                selected_id, selected_priority = ids[index], priority
            end
        else
            redis.call("ZREM", KEYS[1], ids[index])
        end
    end
    if not selected_id then return false end
    local sequence = redis.call("INCR", KEYS[4])
    redis.call("ZADD", KEYS[3], selected_priority * tonumber(ARGV[1]) - sequence, selected_id)
    redis.call("ZREM", KEYS[1], selected_id)
    return selected_id
"""

_STORE_AND_DISCARD_SCRIPT = b"""
    redis.call("SET", KEYS[1], ARGV[1])
    redis.call("LREM", KEYS[2], 0, ARGV[2])
    redis.call("ZREM", KEYS[3], ARGV[2])
    redis.call("ZREM", KEYS[5], ARGV[2])
    if redis.call("ZCARD", KEYS[3]) == 0 then redis.call("SET", KEYS[4], 0, "XX") end
"""

_STORE_EVENT_AND_PUSH_SCRIPT = b"""
    redis.call("SET", KEYS[1], ARGV[1])
    redis.call("ZADD", KEYS[2], ARGV[4], ARGV[3])
    if ARGV[2] == "1" then
        redis.call("LPUSH", KEYS[3], ARGV[3])
    else
        redis.call("RPUSH", KEYS[3], ARGV[3])
    end
"""

# adelete()'s contract is "remove entry_id from every store it could be
# sitting in" -- previously six separate, non-atomic Redis calls (DEL, LREM,
# three ZREMs, plus adiscard_priority's own script). A crash partway through
# left a partial clear: some stores cleaned, others not, for a caller
# (EventQueue.aclear()) that has no way to resume or detect which entries
# were only half-removed. One script closes the window the same way every
# other multi-key mutation in this provider already does.
_DELETE_SCRIPT = b"""
    redis.call("DEL", KEYS[1])
    redis.call("LREM", KEYS[2], 0, ARGV[1])
    redis.call("ZREM", KEYS[3], ARGV[1])
    redis.call("ZREM", KEYS[4], ARGV[1])
    redis.call("ZREM", KEYS[5], ARGV[1])
    redis.call("DEL", KEYS[6])
    redis.call("ZREM", KEYS[7], ARGV[1])
    redis.call("ZREM", KEYS[9], ARGV[1])
    if redis.call("ZCARD", KEYS[7]) == 0 then
        redis.call("SET", KEYS[8], 0, "XX")
    end
"""


@dataclass(frozen=True, slots=True)
class _Scripts:
    claim: Any
    claim_priority: Any
    dequeue_event: Any
    renew: Any
    release: Any
    release_priority: Any
    remove: Any
    expire: Any
    ack: Any
    mark_running: Any
    settle: Any
    recover: Any
    recover_priority: Any
    prune: Any
    push_priority: Any
    pop_priority: Any
    discard_priority: Any
    store_and_push: Any
    store_and_push_priority: Any
    store_scheduled: Any
    promote_scheduled: Any
    promote_scheduled_priority: Any
    store_and_discard: Any
    store_event_and_push: Any
    delete: Any


class _RedisClockFacade:
    def __init__(self, provider: QueueProviderRedis) -> None:
        self._provider = provider

    def now(self):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and (clock := self._provider._clocks_by_loop.get(loop)):
            return clock.now()
        if loop is not None:
            raise QueueClockError(
                "Redis queue clock is not calibrated; await queue.clock.anow() first"
            )
        return async_to_sync(self._anow_and_close)()

    async def anow(self):
        return await self._provider._async_clock().anow()

    async def _anow_and_close(self):
        try:
            return await self.anow()
        finally:
            await self._provider.aclose()


class QueueProviderRedis:
    """Store entry records and ownership state in Redis.

    The provider accepts the same connection and queue identity options as the
    Redis queues, but exposes only record/claim operations.  It is deliberately
    independent of async-queue lifecycle transitions.
    """

    @staticmethod
    def encode(value: str, encoding: str) -> bytes:
        """Encode a Redis value with a queue-specific error."""
        try:
            return value.encode(encoding)
        except UnicodeEncodeError as exc:
            raise QueueEncodingException from exc

    @staticmethod
    def decode(value: object, encoding: str) -> str:
        """Decode a Redis value with a queue-specific error."""
        if isinstance(value, str):
            return value
        if isinstance(value, bytes | bytearray | memoryview):
            try:
                return bytes(value).decode(encoding)
            except UnicodeDecodeError as exc:
                raise QueueEncodingException from exc
        raise QueueEncodingException(
            f"Queue value must be text or bytes, not {type(value).__name__}"
        )

    def __init__(
        self,
        redis_url: str,
        options: dict | None = None,
        *,
        entry_class: type[QueueEntry],
        **kwargs,
    ) -> None:
        if not isinstance(redis_url, str):
            raise InvalidQueueBackendError("Redis queues require a Redis URL")
        options = {} if options is None else options
        options = options | kwargs
        try:
            connection_kwargs = redis.connection.parse_url(redis_url)
        except (AttributeError, ValueError) as exc:
            raise InvalidQueueBackendError(f"Redis URL is invalid: {exc}") from exc
        try:
            self._encoding = codecs.lookup(options.get("encoding", "utf-8")).name
        except (LookupError, TypeError) as exc:
            raise InvalidQueueBackendError("Queue encoding is invalid") from exc
        if (
            connection_kwargs.get("decode_responses", False)
            and self._encoding != "utf-8"
        ):
            raise InvalidQueueBackendError(
                "A Redis client with decode_responses cannot use a non-UTF-8 queue encoding"
            )
        self._redis_url = redis_url
        self.entry_class = entry_class
        self._queue_name = options.get("queue_name", f"queue_{uuid.uuid4().hex}")
        self._stack = bool(options.get("stack", False))
        self._maxsize = options.get("maxsize", 0)
        self._connection_encoding = connection_kwargs.get("encoding", "utf-8")
        self._entry_pending_name = f"{self._queue_name}:entries:pending"
        self._entry_pending_priority_name = (
            f"{self._queue_name}:entries:pending:priority"
        )
        self._entry_pending_priority_sequence_name = (
            f"{self._queue_name}:entries:pending:priority:sequence"
        )
        self._entry_delayed_name = f"{self._queue_name}:entries:delayed"
        self._entry_scheduled_name = f"{self._queue_name}:entries:scheduled"
        self._entry_claim_prefix = f"{self._queue_name}:entries:claims:"
        self._entry_claim_deadlines_name = f"{self._queue_name}:entries:claim-leases"
        self._entry_unclaimed_deadlines_name = (
            f"{self._queue_name}:entries:unclaimed-leases"
        )
        self._async_redis_by_loop: dict[asyncio.AbstractEventLoop, Any] = {}
        self._async_scripts_by_loop: dict[asyncio.AbstractEventLoop, _Scripts] = {}
        self._clocks_by_loop: dict[asyncio.AbstractEventLoop, RedisQueueClock] = {}
        self._clock: QueueClock = _RedisClockFacade(self)

    @property
    def clock(self) -> QueueClock:
        return self._clock

    @property
    def queue_name(self) -> str:
        return self._queue_name

    @property
    def lifecycle_channel(self) -> str:
        """Return the backend-owned channel for async-queue lifecycle snapshots."""
        return f"{self._queue_name}:entries:lifecycle"

    @property
    def capacity(self) -> int:
        return self._maxsize

    @property
    def stack(self) -> bool:
        return self._stack

    async def aadd(self, *items: str) -> None:
        if not items:
            return
        client = self._async_redis()
        current_size = await client.llen(self._queue_name)
        if self._maxsize and current_size + len(items) > self._maxsize:
            raise QueueFullException
        await client.rpush(
            self._queue_name,
            *(self.encode(item, self._encoding) for item in items if item is not None),
        )

    async def aget(self) -> str:
        client = self._async_redis()
        item = (
            await client.rpop(self._queue_name)
            if self._stack
            else await client.lpop(self._queue_name)
        )
        if item is None:
            raise QueueEmptyException
        return self.decode(item, self._encoding)

    async def apoll(self) -> str:
        client = self._async_redis()
        item = (
            await client.brpop([self._queue_name], 0)
            if self._stack
            else await client.blpop([self._queue_name], 0)
        )
        if not item:
            raise QueueEmptyException
        return self.decode(item[1], self._encoding)

    async def apeek(self) -> str:
        client = self._async_redis()
        values = (
            await client.lrange(self._queue_name, -1, -1)
            if self._stack
            else await client.lrange(self._queue_name, 0, 0)
        )
        if not values:
            raise QueueEmptyException
        return self.decode(values[0], self._encoding)

    async def asize(self) -> int:
        return await self._async_redis().llen(self._queue_name)

    async def aclear(self) -> None:
        await self._async_redis().delete(self._queue_name)

    async def aclear_records(self) -> None:
        """Remove all Redis state owned by this queue's retained entries.

        This is deliberately provider-local: it supports the bundled demo's
        fixture reset without exposing Redis keys through a queue facade.
        """
        client = self._async_redis()
        keys = [self._queue_name]
        async for key in client.scan_iter(match=f"{self._queue_name}:entries:*"):
            keys.append(key)
        await client.delete(*keys)

    async def aadd_priority(self, *items) -> None:
        for priority, value in items:
            if self._maxsize and await self.asize_priority() >= self._maxsize:
                raise QueueFullException
            await self._async_redis().zadd(
                self._queue_name,
                {self.encode(value, self._encoding): priority},
                nx=True,
            )

    async def aget_priority(self) -> str:
        if item := await self._async_redis().zrevrange(
            self._queue_name, 0, 0, withscores=False
        ):
            member = item[0]
            if not isinstance(member, bytes | bytearray | memoryview | str):
                raise QueueEncodingException(
                    f"Queue value must be text or bytes, not {type(member).__name__}"
                )
            await self._async_redis().zrem(self._queue_name, member)
            return self.decode(member, self._encoding)
        raise QueueEmptyException

    async def apoll_priority(self, timeout: int = 0, retries: int = 10) -> str:
        """Remove the highest-priority item, waiting for each attempt."""
        attempt = retries
        while retries == 0 or attempt > 0:
            attempt -= 1
            try:
                return await self.aget_priority()
            except QueueEmptyException:
                if timeout <= 0:
                    raise
                if item := await self._async_redis().bzpopmax(
                    [self._queue_name], timeout=timeout
                ):
                    return self.decode(item[1], self._encoding)
        raise QueueEmptyException

    async def apeek_priority(self) -> str:
        if item := await self._async_redis().zrevrange(
            self._queue_name, 0, 0, withscores=False
        ):
            return self.decode(item[0], self._encoding)
        raise QueueEmptyException

    async def asize_priority(self) -> int:
        return await self._async_redis().zcard(self._queue_name)

    async def aclear_priority(self) -> None:
        await self._async_redis().delete(self._queue_name)

    def _entry_key(self, entry_id: uuid.UUID) -> str:
        return f"{self._queue_name}:entries:{entry_id}"

    def _claim_key(self, entry_id: uuid.UUID) -> bytes:
        return self.encode(
            self._entry_claim_prefix, self._connection_encoding
        ) + self.encode(str(entry_id), "ascii")

    def _register_script(self, client: Any, script: bytes) -> Any:
        registered = client.register_script(script)
        registered.sha = self.encode(registered.sha, "ascii")
        return registered

    def _async_redis(self) -> Any:
        loop = asyncio.get_running_loop()
        if client := self._async_redis_by_loop.get(loop):
            return client
        client = async_redis.from_url(self._redis_url)
        self._async_redis_by_loop[loop] = client
        self._async_scripts_by_loop[loop] = _Scripts(
            claim=self._register_script(client, _CLAIM_SCRIPT),
            claim_priority=self._register_script(client, _CLAIM_SCRIPT_WITH_PRIORITY),
            dequeue_event=self._register_script(client, _DEQUEUE_EVENT_SCRIPT),
            renew=self._register_script(client, _RENEW_SCRIPT),
            release=self._register_script(client, _RELEASE_SCRIPT),
            release_priority=self._register_script(
                client, _RELEASE_SCRIPT_WITH_PRIORITY
            ),
            remove=self._register_script(client, _REMOVE_SCRIPT),
            expire=self._register_script(client, _EXPIRE_SCRIPT),
            ack=self._register_script(client, _ACK_SCRIPT),
            mark_running=self._register_script(client, _MARK_RUNNING_SCRIPT),
            settle=self._register_script(client, _SETTLE_SCRIPT),
            recover=self._register_script(client, _RECOVER_SCRIPT),
            recover_priority=self._register_script(
                client, _RECOVER_SCRIPT_WITH_PRIORITY
            ),
            prune=self._register_script(client, _PRUNE_SCRIPT),
            push_priority=self._register_script(client, _PUSH_PRIORITY_SCRIPT),
            pop_priority=self._register_script(client, _POP_PRIORITY_SCRIPT),
            discard_priority=self._register_script(client, _DISCARD_PRIORITY_SCRIPT),
            store_and_push=self._register_script(client, _STORE_AND_PUSH_SCRIPT),
            store_and_push_priority=self._register_script(
                client, _STORE_AND_PUSH_PRIORITY_SCRIPT
            ),
            store_scheduled=self._register_script(client, _STORE_AVAILABLE_SCRIPT),
            promote_scheduled=self._register_script(client, _PROMOTE_SCHEDULED_SCRIPT),
            promote_scheduled_priority=self._register_script(
                client, _PROMOTE_SCHEDULED_PRIORITY_SCRIPT
            ),
            store_and_discard=self._register_script(client, _STORE_AND_DISCARD_SCRIPT),
            store_event_and_push=self._register_script(
                client, _STORE_EVENT_AND_PUSH_SCRIPT
            ),
            delete=self._register_script(client, _DELETE_SCRIPT),
        )
        return client

    async def aobserve(self, on_snapshot) -> None:
        """Receive and decode lifecycle snapshots through provider-owned Pub/Sub."""
        client = async_redis.from_url(self._redis_url)
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        try:
            await pubsub.subscribe(self.lifecycle_channel)
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    entry = self.entry_class.from_dict(json.loads(message["data"]))
                except Exception:
                    logger.exception(
                        "Ignoring invalid queue lifecycle snapshot",
                        extra={"queue": self._queue_name},
                    )
                    continue
                on_snapshot(entry)
        finally:
            await pubsub.aclose()
            await client.aclose()

    async def apublish(self, entry: QueueEntry) -> None:
        await self._async_redis().publish(
            self.lifecycle_channel, json.dumps(entry.to_dict())
        )

    def _async_clock(self) -> RedisQueueClock:
        loop = asyncio.get_running_loop()
        if clock := self._clocks_by_loop.get(loop):
            return clock
        clock = RedisQueueClock(self._async_redis(), asynchronous=True)
        self._clocks_by_loop[loop] = clock
        return clock

    async def astore(self, entry: QueueEntry) -> None:
        if entry.status is QueueEntryStatus.TERMINATED:
            raise TypeError("Terminated queue entry snapshots cannot be stored")
        await self._async_redis().set(
            self._entry_key(entry.id),
            self.encode(json.dumps(entry.to_dict()), "ascii"),
        )

    async def astore_event(self, entry: QueueEntry) -> None:
        if entry.timeout_seconds is None:
            raise ValueError("Event entries require a resolved lifetime")
        client = self._async_redis()
        await client.set(
            self._entry_key(entry.id),
            self.encode(json.dumps(entry.to_dict()), "ascii"),
        )
        await client.zadd(
            self._entry_unclaimed_deadlines_name,
            {
                str(entry.id): round(
                    (entry.queued_at + entry.timeout_seconds).to_timestamp()
                    * MICROSECONDS_PER_SECOND
                )
            },
        )

    async def astore_and_push(self, entry: QueueEntry) -> None:
        """Atomically store a new entry and add it to the plain pending list.

        See `_STORE_AND_PUSH_SCRIPT`'s comment: `astore()` followed by a
        separate `apush()` leaves a window where a crash or connection loss
        between the two calls durably stores the entry with no pending-store
        index pointing to it -- a silent, permanent orphan.
        """
        if entry.status is QueueEntryStatus.TERMINATED:
            raise TypeError("Terminated queue entry snapshots cannot be stored")
        self._async_redis()
        await self._async_scripts_by_loop[asyncio.get_running_loop()].store_and_push(
            keys=(self._entry_key(entry.id), self._entry_pending_name),
            args=(
                self.encode(json.dumps(entry.to_dict()), "ascii"),
                b"1" if self._stack else b"0",
                self.encode(str(entry.id), "ascii"),
            ),
        )

    async def astore_and_push_priority(self, entry: QueueEntry) -> None:
        """Like `astore_and_push`, but for a priority-variant queue's own
        pending store -- see `apush_priority` for the score encoding."""
        if entry.status is QueueEntryStatus.TERMINATED:
            raise TypeError("Terminated queue entry snapshots cannot be stored")
        validate_redis_priority_magnitude(entry.priority)
        self._async_redis()
        await self._async_scripts_by_loop[
            asyncio.get_running_loop()
        ].store_and_push_priority(
            keys=(
                self._entry_key(entry.id),
                self._entry_pending_priority_name,
                self._entry_pending_priority_sequence_name,
            ),
            args=(
                self.encode(json.dumps(entry.to_dict()), "ascii"),
                self.encode(str(entry.id), "ascii"),
                self.encode(str(entry.priority * _PRIORITY_SEQUENCE_SPACE), "ascii"),
            ),
        )

    async def astore_available(
        self, entry: QueueEntry, available_at, *, priority: bool
    ) -> None:
        """Atomically store an entry and choose scheduled or immediate membership."""
        if entry.status is QueueEntryStatus.TERMINATED:
            raise TypeError("Terminated queue entry snapshots cannot be stored")
        if priority:
            validate_redis_priority_magnitude(entry.priority)
        self._async_redis()
        await self._async_scripts_by_loop[asyncio.get_running_loop()].store_scheduled(
            keys=(
                self._entry_key(entry.id),
                self._entry_pending_name,
                self._entry_scheduled_name,
                self._entry_pending_priority_name,
                self._entry_pending_priority_sequence_name,
            ),
            args=(
                self.encode(json.dumps(entry.to_dict()), "ascii"),
                self.encode(str(entry.id), "ascii"),
                self.encode(
                    str(
                        available_at.seconds * MICROSECONDS_PER_SECOND
                        + available_at.microseconds
                    ),
                    "ascii",
                ),
                self.encode(str(entry.priority * _PRIORITY_SEQUENCE_SPACE), "ascii"),
                b"1" if priority else b"0",
                b"1" if self._stack else b"0",
            ),
        )

    async def astore_and_discard(self, entry: QueueEntry) -> None:
        self._async_redis()
        await self._async_scripts_by_loop[asyncio.get_running_loop()].store_and_discard(
            keys=(
                self._entry_key(entry.id),
                self._entry_pending_name,
                self._entry_pending_priority_name,
                self._entry_pending_priority_sequence_name,
                self._entry_scheduled_name,
            ),
            args=(
                self.encode(json.dumps(entry.to_dict()), "ascii"),
                self.encode(str(entry.id), "ascii"),
            ),
        )

    async def astore_event_and_push(self, entry: QueueEntry) -> None:
        """Atomically store a new event, index its unclaimed deadline, and
        add it to the plain pending list -- the event-queue equivalent of
        `astore_and_push`, closing the same class of window across all
        three of `astore_event`'s writes plus the outer `apush`."""
        if entry.timeout_seconds is None:
            raise ValueError("Event entries require a resolved lifetime")
        self._async_redis()
        await self._async_scripts_by_loop[
            asyncio.get_running_loop()
        ].store_event_and_push(
            keys=(
                self._entry_key(entry.id),
                self._entry_unclaimed_deadlines_name,
                self._entry_pending_name,
            ),
            args=(
                self.encode(json.dumps(entry.to_dict()), "ascii"),
                b"1" if self._stack else b"0",
                self.encode(str(entry.id), "ascii"),
                self.encode(
                    str(
                        round(
                            (entry.queued_at + entry.timeout_seconds).to_timestamp()
                            * MICROSECONDS_PER_SECOND
                        )
                    ),
                    "ascii",
                ),
            ),
        )

    async def afind(self, entry_id: uuid.UUID) -> QueueEntry:
        raw = await self._async_redis().get(self._entry_key(entry_id))
        if raw is None:
            raise QueueEntryNotFoundError(entry_id)
        return self.entry_class.from_dict(json.loads(raw))

    async def adelete(self, entry_id: uuid.UUID) -> None:
        """Atomically remove entry_id from every store it could be sitting
        in -- the durable record, the plain pending list, the delayed set,
        both claim-lease ZSETs, the claim key, and the priority pending
        ZSET (with its sequence counter reset if that ZSET just emptied).

        No caller relies on the priority-store cleanup today -- adelete is
        only reached via EventQueue.aclear(), which never touches the
        priority pending store -- but leaving it out would silently orphan
        an entry if a future caller (e.g. AsyncQueue gaining its own
        adelete/aremove) ever reached here while the entry was still queued
        in a priority backend. See `_DELETE_SCRIPT`.
        """
        entry_id_value = self.encode(str(entry_id), "ascii")
        self._async_redis()
        await self._async_scripts_by_loop[asyncio.get_running_loop()].delete(
            keys=(
                self._entry_key(entry_id),
                self._entry_pending_name,
                self._entry_delayed_name,
                self._entry_claim_deadlines_name,
                self._entry_unclaimed_deadlines_name,
                self._claim_key(entry_id),
                self._entry_pending_priority_name,
                self._entry_pending_priority_sequence_name,
                self._entry_scheduled_name,
            ),
            args=(entry_id_value,),
        )

    async def aexpire(self, entry_id: uuid.UUID) -> bool:
        """Atomically delete an event only when it has no active claim."""
        self._async_redis()
        return bool(
            await self._async_scripts_by_loop[asyncio.get_running_loop()].expire(
                keys=(
                    self._claim_key(entry_id),
                    self._entry_key(entry_id),
                    self._entry_pending_name,
                    self._entry_delayed_name,
                    self._entry_claim_deadlines_name,
                    self._entry_unclaimed_deadlines_name,
                ),
                args=(self.encode(str(entry_id), "ascii"),),
            )
        )

    async def aexpire_due(self) -> list[uuid.UUID]:
        now = await self.clock.anow()
        raw_ids = await self._async_redis().zrangebyscore(
            self._entry_unclaimed_deadlines_name,
            "-inf",
            round(now.to_timestamp() * MICROSECONDS_PER_SECOND),
        )
        expired = []
        for raw_entry_id in raw_ids:
            entry_id = uuid.UUID(self.decode(raw_entry_id, "ascii"))
            if await self.aexpire(entry_id):
                expired.append(entry_id)
        return expired

    async def alist(self) -> list[QueueEntry]:
        client = self._async_redis()
        keys = []
        match = f"{self._queue_name}:entries:????????-????-????-????-????????????"
        async for key in client.scan_iter(match=match):
            keys.append(key)
        if not keys:
            return []
        return [
            self.entry_class.from_dict(json.loads(raw))
            for raw in await client.mget(keys)
            if raw is not None
        ]

    async def apush(self, entry_id: uuid.UUID) -> None:
        await self._async_redis().rpush(
            self._entry_pending_name, self.encode(str(entry_id), "ascii")
        )

    async def apromote_scheduled(self) -> None:
        """Move due scheduled IDs into FIFO membership before direct dequeue."""
        self._async_redis()
        await self._async_scripts_by_loop[asyncio.get_running_loop()].promote_scheduled(
            keys=(
                self._entry_scheduled_name,
                self.encode(f"{self._queue_name}:entries:", self._connection_encoding),
                self._entry_pending_name,
            ),
            args=(b"1" if self._stack else b"0",),
        )

    async def apromote_scheduled_priority(self) -> None:
        """Move due scheduled IDs into priority membership before direct dequeue."""
        self._async_redis()
        await self._async_scripts_by_loop[
            asyncio.get_running_loop()
        ].promote_scheduled_priority(
            keys=(
                self._entry_scheduled_name,
                self.encode(f"{self._queue_name}:entries:", self._connection_encoding),
                self._entry_pending_priority_name,
                self._entry_pending_priority_sequence_name,
            ),
            args=(self.encode(str(_PRIORITY_SEQUENCE_SPACE), "ascii"),),
        )

    async def apop(self) -> QueueEntry:
        raw_entry_id = (
            await self._async_redis().rpop(self._entry_pending_name)
            if self._stack
            else await self._async_redis().lpop(self._entry_pending_name)
        )
        if raw_entry_id is None:
            raise QueueEmptyException
        return await self.afind(uuid.UUID(self.decode(raw_entry_id, "ascii")))

    async def adiscard(self, entry_id: uuid.UUID) -> None:
        await self._async_redis().lrem(
            self._entry_pending_name, 0, self.encode(str(entry_id), "ascii")
        )

    async def adiscard_scheduled(self, entry_id: uuid.UUID) -> None:
        await self._async_redis().zrem(
            self._entry_scheduled_name, self.encode(str(entry_id), "ascii")
        )

    async def apush_priority(self, entry_id: uuid.UUID, priority: int) -> None:
        # A ZSET score alone cannot express "arrival order within equal
        # priority": ties break on member value (the encoded entry ID), not
        # insertion order. Folding this queue's own monotonic sequence into
        # the low bits of the score (subtracted, so an earlier sequence number
        # yields a higher score) makes zrevrange return equal-priority
        # entries oldest-first, matching apop's plain-FIFO tie-break and the
        # memory backend's PriorityQueue (a stable, insertion-ordered heap
        # for equal keys).
        #
        # INCR and ZADD run inside one Lua script rather than as two
        # separate round-trips: `sequence` resets to 0 whenever the priority
        # ZSET drains empty (see apop_priority/adiscard_priority), so that
        # -- unlike an ever-growing counter -- it never runs out of the
        # headroom a double's 53-bit exact-integer range gives it. If the
        # INCR and the ZADD were separate calls, a push that reserved a
        # sequence number just before the queue drained (and reset) could
        # still land its ZADD afterwards, using a now-stale, oversized
        # sequence value that sorts after every entry pushed since the
        # reset -- silently breaking arrival order for exactly the entries
        # this mechanism exists to protect. One atomic script closes that
        # window entirely.
        validate_redis_priority_magnitude(priority)
        self._async_redis()
        scripts = self._async_scripts_by_loop[asyncio.get_running_loop()]
        await scripts.push_priority(
            keys=(
                self._entry_pending_priority_name,
                self._entry_pending_priority_sequence_name,
            ),
            args=(
                self.encode(str(priority * _PRIORITY_SEQUENCE_SPACE), "ascii"),
                self.encode(str(entry_id), "ascii"),
            ),
        )

    async def apop_priority(self) -> QueueEntry:
        self._async_redis()
        scripts = self._async_scripts_by_loop[asyncio.get_running_loop()]
        member = await scripts.pop_priority(
            keys=(
                self._entry_pending_priority_name,
                self._entry_pending_priority_sequence_name,
            )
        )
        if not member:
            raise QueueEmptyException
        return await self.afind(uuid.UUID(self.decode(member, "ascii")))

    async def adiscard_priority(self, entry_id: uuid.UUID) -> None:
        self._async_redis()
        scripts = self._async_scripts_by_loop[asyncio.get_running_loop()]
        await scripts.discard_priority(
            keys=(
                self._entry_pending_priority_name,
                self._entry_pending_priority_sequence_name,
            ),
            args=(self.encode(str(entry_id), "ascii"),),
        )

    async def ahas_pending(self) -> bool:
        client = self._async_redis()
        return bool(
            await client.llen(self._entry_pending_name)
            or await client.zcard(self._entry_delayed_name)
            or await client.zcard(self._entry_scheduled_name)
            or await client.zcard(self._entry_pending_priority_name)
        )

    async def aclaim(
        self, worker_id: uuid.UUID, lease_seconds: float | None = None
    ) -> QueueEntry:
        return await self._aclaim(worker_id, lease_seconds, expire_unclaimed=False)

    async def aclaim_unexpired(
        self, worker_id: uuid.UUID, lease_seconds: float | None = None
    ) -> QueueEntry:
        return await self._aclaim(worker_id, lease_seconds, expire_unclaimed=True)

    async def aclaim_priority(
        self, worker_id: uuid.UUID, lease_seconds: float | None = None
    ) -> QueueEntry:
        """Like `aclaim`, but for a priority-variant queue's own claim path.

        `_CLAIM_SCRIPT` only ever looks at the plain pending list, so it can
        never see an entry a priority backend pushed via `apush_priority`
        into the separate priority ZSET. `_CLAIM_SCRIPT_WITH_PRIORITY` tries
        the plain list first (delayed-retry recoveries still land there,
        regardless of variant) and falls back to the priority ZSET only when
        it is empty, so a priority queue still recovers/claims correctly.
        """
        return await self._aclaim_priority(
            worker_id, lease_seconds, expire_unclaimed=False
        )

    async def aclaim_priority_unexpired(
        self, worker_id: uuid.UUID, lease_seconds: float | None = None
    ) -> QueueEntry:
        return await self._aclaim_priority(
            worker_id, lease_seconds, expire_unclaimed=True
        )

    async def adequeue(self) -> QueueEntry:
        """Atomically remove and return the next unclaimed live event."""
        client = self._async_redis()
        outcome, raw_entry = await self._async_scripts_by_loop[
            asyncio.get_running_loop()
        ].dequeue_event(
            keys=(
                self._entry_pending_name,
                self._entry_delayed_name,
                self.encode(self._entry_claim_prefix, self._connection_encoding),
                self._entry_claim_deadlines_name,
                self.encode(f"{self._queue_name}:entries:", self._connection_encoding),
                self._entry_unclaimed_deadlines_name,
            ),
            args=(b"1" if self._stack else b"0",),
            client=client,
        )
        outcome = self.decode(outcome, "ascii")
        if outcome == "empty":
            raise QueueEmptyException
        if outcome != "dequeued":
            raise QueueValueError(f"Unknown Redis event dequeue outcome: {outcome!r}")
        return self.entry_class.from_dict(json.loads(raw_entry))

    async def _aclaim(
        self,
        worker_id: uuid.UUID,
        lease_seconds: float | None,
        *,
        expire_unclaimed: bool,
    ) -> QueueEntry:
        lease_seconds = (
            600.0 if lease_seconds is None else validate_budget(lease_seconds)
        )
        client = self._async_redis()
        scripts = self._async_scripts_by_loop[asyncio.get_running_loop()]
        outcome, raw_entry_id = await scripts.claim(
            keys=(
                self._entry_pending_name,
                self._entry_delayed_name,
                self.encode(self._entry_claim_prefix, self._connection_encoding),
                self._entry_claim_deadlines_name,
                self.encode(f"{self._queue_name}:entries:", self._connection_encoding),
                self._entry_unclaimed_deadlines_name,
                self._entry_scheduled_name,
            ),
            args=(
                self.encode(str(worker_id), "ascii"),
                self.encode(
                    str(round(lease_seconds * MICROSECONDS_PER_SECOND)), "ascii"
                ),
                b"1" if self._stack else b"0",
                b"1" if expire_unclaimed else b"0",
            ),
            client=client,
        )
        outcome = self.decode(outcome, "ascii")
        if outcome == "empty":
            raise QueueEmptyException
        entry_id = uuid.UUID(self.decode(raw_entry_id, "ascii"))
        if outcome == "conflict":
            raise QueueClaimConflictError(entry_id)
        if outcome == "expired":
            raise QueueEntryExpiredError(entry_id)
        if outcome != "claimed":
            raise QueueValueError(f"Unknown Redis claim outcome: {outcome!r}")
        try:
            return await self.afind(entry_id)
        except QueueEntryNotFoundError as exc:
            raise QueueEntryMissingError(entry_id) from exc

    async def _aclaim_priority(
        self,
        worker_id: uuid.UUID,
        lease_seconds: float | None,
        *,
        expire_unclaimed: bool,
    ) -> QueueEntry:
        lease_seconds = (
            600.0 if lease_seconds is None else validate_budget(lease_seconds)
        )
        client = self._async_redis()
        scripts = self._async_scripts_by_loop[asyncio.get_running_loop()]
        outcome, raw_entry_id = await scripts.claim_priority(
            keys=(
                self._entry_pending_name,
                self._entry_delayed_name,
                self.encode(self._entry_claim_prefix, self._connection_encoding),
                self._entry_claim_deadlines_name,
                self.encode(f"{self._queue_name}:entries:", self._connection_encoding),
                self._entry_unclaimed_deadlines_name,
                self._entry_pending_priority_name,
                self._entry_pending_priority_sequence_name,
                self._entry_scheduled_name,
            ),
            args=(
                self.encode(str(worker_id), "ascii"),
                self.encode(
                    str(round(lease_seconds * MICROSECONDS_PER_SECOND)), "ascii"
                ),
                b"1" if self._stack else b"0",
                b"1" if expire_unclaimed else b"0",
                self.encode(str(_PRIORITY_SEQUENCE_SPACE), "ascii"),
            ),
            client=client,
        )
        outcome = self.decode(outcome, "ascii")
        if outcome == "empty":
            raise QueueEmptyException
        entry_id = uuid.UUID(self.decode(raw_entry_id, "ascii"))
        if outcome == "conflict":
            raise QueueClaimConflictError(entry_id)
        if outcome == "expired":
            raise QueueEntryExpiredError(entry_id)
        if outcome != "claimed":
            raise QueueValueError(f"Unknown Redis claim outcome: {outcome!r}")
        try:
            return await self.afind(entry_id)
        except QueueEntryNotFoundError as exc:
            raise QueueEntryMissingError(entry_id) from exc

    async def arenew(
        self, entry_id: uuid.UUID, worker_id: uuid.UUID, lease_seconds: float
    ) -> bool:
        validate_budget(lease_seconds)
        self._async_redis()
        return bool(
            await self._async_scripts_by_loop[asyncio.get_running_loop()].renew(
                keys=(self._claim_key(entry_id), self._entry_claim_deadlines_name),
                args=(
                    self.encode(str(worker_id), "ascii"),
                    self.encode(
                        str(round(lease_seconds * MICROSECONDS_PER_SECOND)), "ascii"
                    ),
                    self.encode(str(entry_id), "ascii"),
                ),
            )
        )

    async def arelease(
        self, entry_id: uuid.UUID, worker_id: uuid.UUID, delay_seconds: float
    ) -> bool:
        validate_budget(delay_seconds)
        self._async_redis()
        return bool(
            await self._async_scripts_by_loop[asyncio.get_running_loop()].release(
                keys=(
                    self._claim_key(entry_id),
                    self._entry_claim_deadlines_name,
                    self._entry_delayed_name,
                    self._entry_unclaimed_deadlines_name,
                ),
                args=(
                    self.encode(str(worker_id), "ascii"),
                    self.encode(str(entry_id), "ascii"),
                    self.encode(
                        str(round(delay_seconds * MICROSECONDS_PER_SECOND)), "ascii"
                    ),
                ),
            )
        )

    async def arelease_priority(
        self, entry_id: uuid.UUID, worker_id: uuid.UUID, delay_seconds: float
    ) -> bool:
        """Like `arelease`, but redelivers via the entry's priority score
        instead of the plain delayed set -- see `_RELEASE_SCRIPT_WITH_PRIORITY`.

        `delay_seconds` is accepted for signature compatibility with
        `arelease` but not honoured: the priority ZSET has no "not yet due"
        concept the way the delayed set does.
        """
        validate_budget(delay_seconds)
        self._async_redis()
        return bool(
            await self._async_scripts_by_loop[
                asyncio.get_running_loop()
            ].release_priority(
                keys=(
                    self._claim_key(entry_id),
                    self._entry_claim_deadlines_name,
                    self._entry_delayed_name,
                    self._entry_unclaimed_deadlines_name,
                    self._entry_pending_priority_name,
                    self.encode(
                        f"{self._queue_name}:entries:", self._connection_encoding
                    ),
                    self._entry_pending_priority_sequence_name,
                ),
                args=(
                    self.encode(str(worker_id), "ascii"),
                    self.encode(str(entry_id), "ascii"),
                    self.encode(str(_PRIORITY_SEQUENCE_SPACE), "ascii"),
                ),
            )
        )

    async def aremove(self, entry_id: uuid.UUID, worker_id: uuid.UUID) -> bool:
        self._async_redis()
        return bool(
            await self._async_scripts_by_loop[asyncio.get_running_loop()].remove(
                keys=(
                    self._claim_key(entry_id),
                    self._entry_claim_deadlines_name,
                    self._entry_delayed_name,
                    self._entry_pending_name,
                    self._entry_key(entry_id),
                    self._entry_unclaimed_deadlines_name,
                ),
                args=(
                    self.encode(str(worker_id), "ascii"),
                    self.encode(str(entry_id), "ascii"),
                ),
            )
        )

    async def aack(self, entry_id: uuid.UUID, worker_id: uuid.UUID) -> bool:
        self._async_redis()
        return bool(
            await self._async_scripts_by_loop[asyncio.get_running_loop()].ack(
                keys=(self._claim_key(entry_id), self._entry_claim_deadlines_name),
                args=(
                    self.encode(str(worker_id), "ascii"),
                    self.encode(str(entry_id), "ascii"),
                ),
            )
        )

    async def amark_running(self, worker_id: uuid.UUID, entry: QueueEntry) -> bool:
        self._async_redis()
        return bool(
            await self._async_scripts_by_loop[asyncio.get_running_loop()].mark_running(
                keys=(self._claim_key(entry.id), self._entry_key(entry.id)),
                args=(
                    self.encode(str(worker_id), "ascii"),
                    self.encode(json.dumps(entry.to_dict()), "ascii"),
                ),
            )
        )

    async def asettle(self, worker_id: uuid.UUID, entry: QueueEntry) -> bool:
        self._async_redis()
        return bool(
            await self._async_scripts_by_loop[asyncio.get_running_loop()].settle(
                keys=(
                    self._claim_key(entry.id),
                    self._entry_claim_deadlines_name,
                    self._entry_key(entry.id),
                ),
                args=(
                    self.encode(str(worker_id), "ascii"),
                    self.encode(str(entry.id), "ascii"),
                    self.encode(json.dumps(entry.to_dict()), "ascii"),
                ),
            )
        )

    async def arecover(self, batch_size: int) -> tuple[int, int]:
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("Recovery batch size must be a positive integer")
        self._async_redis()
        recovered, discarded = await self._async_scripts_by_loop[
            asyncio.get_running_loop()
        ].recover(
            keys=(
                self._entry_claim_deadlines_name,
                self.encode(self._entry_claim_prefix, self._connection_encoding),
                self._entry_pending_name,
                self.encode(f"{self._queue_name}:entries:", self._connection_encoding),
                self._entry_unclaimed_deadlines_name,
            ),
            args=(
                b"1" if self._stack else b"0",
                self.encode(str(batch_size), "ascii"),
            ),
        )
        return int(recovered), int(discarded)

    async def arecover_priority(self, batch_size: int) -> tuple[int, int]:
        """Like `arecover`, but redelivers a recovered entry via its
        priority score instead of the plain pending list -- see
        `_RECOVER_SCRIPT_WITH_PRIORITY`."""
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("Recovery batch size must be a positive integer")
        self._async_redis()
        recovered, discarded = await self._async_scripts_by_loop[
            asyncio.get_running_loop()
        ].recover_priority(
            keys=(
                self._entry_claim_deadlines_name,
                self.encode(self._entry_claim_prefix, self._connection_encoding),
                self._entry_pending_name,
                self.encode(f"{self._queue_name}:entries:", self._connection_encoding),
                self._entry_unclaimed_deadlines_name,
                self._entry_pending_priority_name,
                self._entry_pending_priority_sequence_name,
            ),
            args=(
                b"1" if self._stack else b"0",
                self.encode(str(batch_size), "ascii"),
                self.encode(str(_PRIORITY_SEQUENCE_SPACE), "ascii"),
            ),
        )
        return int(recovered), int(discarded)

    async def aprune(self, entry_id: uuid.UUID) -> QueueEntry:
        entry = await self.afind(entry_id)
        if QueueEntryStatus.TERMINATED not in entry.status.next_state():
            raise ValueError("Only terminal queue entries can be pruned")
        self._async_redis()
        outcome = await self._async_scripts_by_loop[asyncio.get_running_loop()].prune(
            keys=(self._entry_key(entry_id), self._entry_pending_name),
            args=(self.encode(str(entry_id), "ascii"),),
        )
        if outcome == 0:
            raise QueueEntryNotFoundError(entry_id)
        if outcome == -1:
            raise ValueError("Only terminal queue entries can be pruned")
        return self.entry_class.from_dict(json.loads(outcome))

    async def aclose(self) -> None:
        loop = asyncio.get_running_loop()
        if clock := self._clocks_by_loop.pop(loop, None):
            await clock.aclose()
        self._async_scripts_by_loop.pop(loop, None)
        if client := self._async_redis_by_loop.pop(loop, None):
            await client.aclose(close_connection_pool=True)
