import asyncio
import json
import logging
import threading
from dataclasses import replace
from uuid import uuid4

import pytest
import redis
import redis.asyncio as async_redis
from redis.backoff import ConstantBackoff
from redis.retry import Retry

from django_queue.backends import (
    RedisPriorityQueue,
    RedisQueue,
    RedisQueueJson,
    RedisStack,
)
from django_queue.backends.exceptions import (
    InvalidQueueBackendError,
    QueueClaimConflictError,
    QueueEmptyException,
    QueueEntryMissingError,
)
from django_queue.clock import ClockTime, QueueClockError
from django_queue.entries import QueueEntryStatus
from django_queue.worker import AsyncQueueWorker, QueuePersistenceError
from tests.helpers import CustomQueueEntry


@pytest.fixture
def redis_entry_queue(redis_client):
    return RedisQueue(redis_client, queue_name=f"entry-contract-{uuid4().hex}")


class TestRedisQueueEntries:
    def test_uses_distinct_clients_for_a_worker_loop_and_sync_bridge(
        self, redis_client
    ):
        queue = RedisQueue(redis_client, queue_name=f"entry-loops-{uuid4().hex}")
        clients_by_loop = {}
        original_async_redis = queue._async_redis

        def record_client():
            loop = asyncio.get_running_loop()
            client = original_async_redis()
            clients_by_loop[loop] = client
            return client

        queue._async_redis = record_client
        worker_ready = threading.Event()
        release_worker = threading.Event()
        worker_failure = []

        async def handle(entry):
            return entry.payload

        def run_worker_loop():
            async def exercise():
                entry_id = await queue.aenqueue({"from": "worker"})
                worker = AsyncQueueWorker(
                    {"requests": queue}, {"requests": handle}, idle_delay=0.001
                )
                worker_task = asyncio.create_task(worker.run())
                while (
                    await queue.aget_entry(entry_id)
                ).status is not QueueEntryStatus.SUCCEEDED:
                    await asyncio.sleep(0.001)
                worker_ready.set()
                await asyncio.to_thread(release_worker.wait)
                worker_task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await worker_task
                await queue.aclose()

            try:
                asyncio.run(exercise())
            except Exception as exc:  # noqa: BLE001 - surface thread failures in this test
                worker_failure.append(exc)

        worker_thread = threading.Thread(target=run_worker_loop)
        worker_thread.start()
        try:
            assert worker_ready.wait(timeout=1)
            assert not worker_failure
            worker_client = next(iter(queue._async_redis_by_loop.values()))

            queue.enqueue({"from": "bridge"})

            assert len(clients_by_loop) == 2
            assert len(set(clients_by_loop.values())) == 2
            assert list(queue._async_redis_by_loop.values()) == [worker_client]
        finally:
            release_worker.set()
            worker_thread.join(timeout=1)

        assert not worker_thread.is_alive()
        assert not worker_failure

    def test_uses_tls_when_cloning_a_sync_tls_client(self):
        source = redis.Redis(host="localhost", ssl=True)
        queue = RedisQueue(source, queue_name=f"entry-tls-{uuid4().hex}")

        async def exercise():
            client = queue._async_redis()
            assert client.connection_pool.connection_class is async_redis.SSLConnection
            await queue.aclose()

        asyncio.run(exercise())
        source.close()

    def test_translates_a_sync_retry_policy_when_cloning_a_client(self):
        source = redis.Redis(retry=Retry(ConstantBackoff(0.1), 3))
        queue = RedisQueue(source, queue_name=f"entry-retry-{uuid4().hex}")

        async def exercise():
            retry = queue._async_redis().connection_pool.connection_kwargs["retry"]
            assert isinstance(retry, async_redis.retry.Retry)
            assert retry._retries == 3
            source_retry = source.connection_pool.connection_kwargs["retry"]
            assert retry._backoff.compute(1) == source_retry._backoff.compute(1)
            await queue.aclose()

        asyncio.run(exercise())
        source.close()

    def test_rejects_a_sync_retry_policy_without_cloneable_attributes(self):
        retry = Retry(ConstantBackoff(0.1), 3)
        del retry._backoff
        source = redis.Redis(retry=retry)
        queue = RedisQueue(source, queue_name=f"entry-retry-{uuid4().hex}")

        async def exercise():
            queue._async_redis()

        # The client is created before Redis is contacted, so unsupported
        # retry configuration is reported without requiring a live server.
        with pytest.raises(
            InvalidQueueBackendError, match="retry policy is unsupported"
        ):
            asyncio.run(exercise())
        source.close()

    def test_url_configuration_does_not_construct_a_sync_redis_client(self):
        queue = RedisQueue("redis://localhost:6379/0")

        assert queue._redis is None

    def test_clock_now_requires_async_calibration_on_an_active_loop(
        self, redis_entry_queue
    ):
        async def exercise():
            with pytest.raises(QueueClockError, match="await queue.clock.anow"):
                redis_entry_queue.clock.now()

        asyncio.run(exercise())

    def test_awaited_entry_lifecycle_and_loop_local_client_reuse(self, redis_client):
        queue = RedisQueue(redis_client, queue_name=f"entry-async-{uuid4().hex}")

        async def exercise():
            entry_id = await queue.aenqueue({"request_id": 42})
            assert (await queue.adequeue_entry()).id == entry_id
            assert (
                await queue.amark_running(entry_id)
            ).status is QueueEntryStatus.RUNNING
            completed = await queue.amark_succeeded(entry_id, {"ok": True})
            assert completed.status is QueueEntryStatus.SUCCEEDED
            first_client = queue._async_redis()
            first_scripts = queue._async_scripts_by_loop.copy()
            await queue.aclose()
            assert not queue._async_redis_by_loop
            assert not queue._async_scripts_by_loop
            assert queue._async_redis() is not first_client
            assert queue._async_scripts_by_loop != first_scripts
            await queue.aclose()

        asyncio.run(exercise())

    def test_persists_an_identified_entry_separately_from_raw_queue_values(
        self, redis_entry_queue
    ):
        redis_entry_queue.add("raw-value")

        entry_id = redis_entry_queue.enqueue({"request_id": 42})

        entry = redis_entry_queue.get_entry(entry_id)
        assert entry.id == entry_id
        assert entry.queue == redis_entry_queue.queue_name
        assert entry.status is QueueEntryStatus.QUEUED
        assert entry.payload == {"request_id": 42}
        assert redis_entry_queue.get() == "raw-value"

    def test_synchronous_entry_api_closes_each_bridge_loop_client(
        self, redis_entry_queue
    ):
        redis_entry_queue.enqueue("work")

        assert not redis_entry_queue._async_redis_by_loop

    def test_synchronous_clock_reads_close_each_bridge_loop_client(
        self, redis_entry_queue
    ):
        redis_entry_queue.clock.now()
        redis_entry_queue.clock.now()

        assert not redis_entry_queue._async_redis_by_loop
        assert not redis_entry_queue._clocks_by_loop

    def test_dequeues_an_entry_atomically_from_pending_work(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")

        entry = redis_entry_queue.dequeue_entry()

        assert entry.id == entry_id
        with pytest.raises(QueueEmptyException):
            redis_entry_queue.dequeue_entry()

    def test_dequeues_entries_in_fifo_order(self, redis_entry_queue):
        first_id = redis_entry_queue.enqueue("first")
        latest_id = redis_entry_queue.enqueue("latest")

        assert redis_entry_queue.dequeue_entry().id == first_id
        assert redis_entry_queue.dequeue_entry().id == latest_id

    def test_claims_one_entry_for_exactly_one_competing_worker(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")
        first_worker = uuid4()
        second_worker = uuid4()

        async def exercise():
            claims = await asyncio.gather(
                redis_entry_queue.aclaim_entry(first_worker),
                redis_entry_queue.aclaim_entry(second_worker),
                return_exceptions=True,
            )

            claimed = [claim for claim in claims if not isinstance(claim, Exception)]
            failures = [claim for claim in claims if isinstance(claim, Exception)]

            assert [claim.id for claim in claimed] == [entry_id]
            assert len(failures) == 1
            assert isinstance(failures[0], QueueEmptyException)
            assert not await redis_entry_queue.ahas_pending_entries()
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_records_owner_and_redis_time_for_a_claim(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")
        worker_id = uuid4()

        async def exercise():
            claimed = await redis_entry_queue.aclaim_entry(worker_id)
            raw_claim = await redis_entry_queue._async_redis().get(
                redis_entry_queue._entry_claim_key(entry_id)
            )

            assert claimed.id == entry_id
            claim = json.loads(raw_claim)
            assert claim["worker_id"] == str(worker_id)
            assert set(claim["claimed_at"]) == {"seconds", "microseconds"}
            assert all(isinstance(value, int) for value in claim["claimed_at"].values())
            assert isinstance(claim["lease_deadline"], (int, float))
            assert claim["lease_deadline"] > 0
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_acknowledges_a_claim_only_for_its_owner(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")
        owner_id = uuid4()

        async def exercise():
            await redis_entry_queue.aclaim_entry(owner_id)

            assert not await redis_entry_queue.aacknowledge_claim(entry_id, uuid4())
            assert await redis_entry_queue._async_redis().exists(
                redis_entry_queue._entry_claim_key(entry_id)
            )
            assert await redis_entry_queue.aacknowledge_claim(entry_id, owner_id)
            assert not await redis_entry_queue._async_redis().exists(
                redis_entry_queue._entry_claim_key(entry_id)
            )
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_renews_a_claim_only_for_its_owner(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")
        owner_id = uuid4()

        async def exercise():
            await redis_entry_queue.aclaim_entry(owner_id, lease_seconds=1)

            assert not await redis_entry_queue.arenew_claim(
                entry_id, uuid4(), lease_seconds=1
            )
            assert await redis_entry_queue.arenew_claim(
                entry_id, owner_id, lease_seconds=1
            )
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_uses_a_backend_specific_default_claim_lease(self, redis_client):
        class ShortLeaseQueue(RedisQueue):
            default_claim_lease_seconds = 0.001

        queue = ShortLeaseQueue(redis_client, queue_name=f"lease-default-{uuid4().hex}")
        queue.enqueue("work")

        async def exercise():
            await queue.aclaim_entry(uuid4())
            await asyncio.sleep(0.01)

            assert await queue.arecover_expired_claims() == 1
            await queue.aclose()

        asyncio.run(exercise())

    @pytest.mark.parametrize(
        ("queue_type", "first_payload", "second_payload"),
        [(RedisQueue, "first", "second"), (RedisStack, "second", "first")],
        ids=["queue-fifo", "stack-lifo"],
    )
    def test_recovers_expired_claims_in_queue_order(
        self, redis_client, queue_type, first_payload, second_payload
    ):
        queue = queue_type(redis_client, queue_name=f"lease-order-{uuid4().hex}")
        first_id = queue.enqueue("first")
        second_id = queue.enqueue("second")

        async def exercise():
            first_claim = await queue.aclaim_entry(uuid4(), lease_seconds=0.001)
            second_claim = await queue.aclaim_entry(uuid4(), lease_seconds=0.001)
            await asyncio.sleep(0.01)

            assert await queue.arecover_expired_claims() == 2
            assert {first_claim.id, second_claim.id} == {first_id, second_id}
            assert (await queue.adequeue_entry()).payload == first_payload
            assert (await queue.adequeue_entry()).payload == second_payload
            assert (await queue.aget_entry(first_id)).id == first_id
            await queue.aclose()

        asyncio.run(exercise())

    def test_recovers_expired_claims_in_bounded_batches(self, redis_entry_queue):
        first_id = redis_entry_queue.enqueue("first")
        second_id = redis_entry_queue.enqueue("second")
        redis_entry_queue.recovery_batch_size = 1

        async def exercise():
            first_claim = await redis_entry_queue.aclaim_entry(
                uuid4(), lease_seconds=0.001
            )
            await asyncio.sleep(0.01)
            second_claim = await redis_entry_queue.aclaim_entry(
                uuid4(), lease_seconds=0.001
            )
            await asyncio.sleep(0.01)

            assert await redis_entry_queue.arecover_expired_claims() == 1
            assert (await redis_entry_queue.adequeue_entry()).id == first_claim.id
            assert await redis_entry_queue.arecover_expired_claims() == 1
            assert (await redis_entry_queue.adequeue_entry()).id == second_claim.id
            assert {first_claim.id, second_claim.id} == {first_id, second_id}
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    @pytest.mark.parametrize("batch_size", [0, -1, 1.5, True])
    def test_rejects_an_invalid_recovery_batch_size(
        self, redis_entry_queue, batch_size
    ):
        redis_entry_queue.recovery_batch_size = batch_size

        async def exercise():
            with pytest.raises(ValueError, match="Recovery batch size"):
                await redis_entry_queue.arecover_expired_claims()
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_recovers_a_renewed_non_utf8_queue_claim(self, redis_client):
        queue = RedisQueue(
            redis_client,
            queue_name=f"lease-encoding-{uuid4().hex}",
            encoding="utf-16",
        )
        entry_id = queue.enqueue("work")
        worker_id = uuid4()

        async def exercise():
            await queue.aclaim_entry(worker_id, lease_seconds=0.001)
            assert await queue.arenew_claim(entry_id, worker_id, lease_seconds=0.01)
            await asyncio.sleep(0.02)

            assert await queue.arecover_expired_claims() == 1
            assert (await queue.adequeue_entry()).id == entry_id
            await queue.aclose()

        asyncio.run(exercise())

    def test_does_not_recover_a_renewed_claim(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")
        worker_id = uuid4()

        async def exercise():
            await redis_entry_queue.aclaim_entry(worker_id, lease_seconds=0.01)
            assert await redis_entry_queue.arenew_claim(
                entry_id, worker_id, lease_seconds=1
            )
            await asyncio.sleep(0.02)

            assert await redis_entry_queue.arecover_expired_claims() == 0
            assert not await redis_entry_queue.ahas_pending_entries()
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    @pytest.mark.parametrize(
        "claim_value", [None, "not json"], ids=["missing", "malformed"]
    )
    def test_recovers_an_orphaned_expired_claim_record(
        self, redis_entry_queue, claim_value
    ):
        entry_id = redis_entry_queue.enqueue("work")

        async def exercise():
            await redis_entry_queue.aclaim_entry(uuid4())
            client = redis_entry_queue._async_redis()
            if claim_value is None:
                await client.delete(redis_entry_queue._entry_claim_key(entry_id))
            else:
                await client.set(
                    redis_entry_queue._entry_claim_key(entry_id), claim_value
                )
            await client.zadd(
                redis_entry_queue._entry_claim_deadlines_name, {str(entry_id): 0}
            )

            assert await redis_entry_queue.arecover_expired_claims() == 1
            assert (await redis_entry_queue.adequeue_entry()).id == entry_id
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_logs_a_discarded_orphaned_claim_without_an_entry(
        self, redis_entry_queue, caplog
    ):
        entry_id = redis_entry_queue.enqueue("work")

        async def exercise():
            await redis_entry_queue.aclaim_entry(uuid4())
            client = redis_entry_queue._async_redis()
            await client.delete(redis_entry_queue._entry_key(entry_id))
            await client.set(redis_entry_queue._entry_claim_key(entry_id), "not json")
            await client.zadd(
                redis_entry_queue._entry_claim_deadlines_name, {str(entry_id): 0}
            )

            with caplog.at_level(
                logging.ERROR, logger="django_queue.backends.redis.redisqueue"
            ):
                assert await redis_entry_queue.arecover_expired_claims() == 0
            assert "Discarded 1 unrecoverable expired queue claim" in caplog.text
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_marks_claimed_entry_running_only_for_its_owner(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")
        owner_id = uuid4()

        async def exercise():
            await redis_entry_queue.aclaim_entry(owner_id)

            assert (
                await redis_entry_queue.amark_claim_running(entry_id, uuid4()) is None
            )
            assert (
                await redis_entry_queue.aget_entry(entry_id)
            ).status is QueueEntryStatus.QUEUED

            running = await redis_entry_queue.amark_claim_running(entry_id, owner_id)
            assert running is not None
            assert running.status is QueueEntryStatus.RUNNING
            assert (await redis_entry_queue.aget_entry(entry_id)) == running
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_settles_a_terminal_claim_only_for_its_owner(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")
        owner_id = uuid4()

        async def exercise():
            claimed = await redis_entry_queue.aclaim_entry(owner_id)
            running = await redis_entry_queue.amark_running(entry_id)
            terminal = replace(
                running,
                status=QueueEntryStatus.SUCCEEDED,
                result="done",
                finished_at=await redis_entry_queue.clock.anow(),
            )

            assert not await redis_entry_queue.asettle_claim(uuid4(), terminal)
            assert (await redis_entry_queue.aget_entry(entry_id)) == running
            assert await redis_entry_queue.asettle_claim(owner_id, terminal)
            assert (await redis_entry_queue.aget_entry(claimed.id)) == terminal
            assert not await redis_entry_queue.aacknowledge_claim(entry_id, owner_id)
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_worker_settles_redis_claim_after_terminal_success(self, redis_client):
        queue = RedisQueue(redis_client, queue_name=f"lease-worker-{uuid4().hex}")

        async def exercise():
            entry_id = await queue.aenqueue("work")

            async def handle(entry):
                return entry.payload

            worker = AsyncQueueWorker(
                {"requests": queue}, {"requests": handle}, idle_delay=0.001
            )
            task = asyncio.create_task(worker.run())
            while (
                await queue.aget_entry(entry_id)
            ).status is not QueueEntryStatus.SUCCEEDED:
                await asyncio.sleep(0.001)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            assert await queue.arecover_expired_claims() == 0
            with pytest.raises(QueueEmptyException):
                await queue.aclaim_entry(uuid4())
            await queue.aclose()

        asyncio.run(exercise())

    def test_worker_settles_redis_claim_after_handler_failure(self, redis_client):
        queue = RedisQueue(redis_client, queue_name=f"lease-worker-{uuid4().hex}")

        async def exercise():
            entry_id = await queue.aenqueue("work")

            async def handle(entry):
                raise ValueError(entry.payload)

            worker = AsyncQueueWorker(
                {"requests": queue}, {"requests": handle}, idle_delay=0.001
            )
            task = asyncio.create_task(worker.run())
            while (
                await queue.aget_entry(entry_id)
            ).status is not QueueEntryStatus.FAILED:
                await asyncio.sleep(0.001)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            assert await queue.arecover_expired_claims() == 0
            with pytest.raises(QueueEmptyException):
                await queue.aclaim_entry(uuid4())
            await queue.aclose()

        asyncio.run(exercise())

    def test_worker_records_a_claimed_persistence_failure_and_continues(
        self, redis_client
    ):
        class FailingSettlementQueue(RedisQueue):
            fail_next_settlement = True

            async def asettle_claim(self, worker_id, entry):
                if self.fail_next_settlement:
                    self.fail_next_settlement = False
                    raise ConnectionError("Redis is unavailable")
                return await super().asettle_claim(worker_id, entry)

        queue = FailingSettlementQueue(
            redis_client, queue_name=f"lease-persistence-{uuid4().hex}"
        )

        async def exercise():
            first_id = await queue.aenqueue("first")
            second_id = await queue.aenqueue("second")

            async def handle(entry):
                return entry.payload

            worker = AsyncQueueWorker(
                {"requests": queue}, {"requests": handle}, idle_delay=0.001
            )
            task = asyncio.create_task(worker.run())
            while (
                await queue.aget_entry(second_id)
            ).status is not QueueEntryStatus.SUCCEEDED:
                await asyncio.sleep(0.001)

            first = await queue.aget_entry(first_id)
            assert first.status is QueueEntryStatus.FAILED
            assert first.error == {
                "type": "QueuePersistenceError",
                "message": "Unable to persist terminal queue outcome",
            }
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await queue.aclose()

        asyncio.run(exercise())

    def test_worker_stops_after_an_unrecoverable_claimed_persistence_failure(
        self, redis_client
    ):
        class UnrecoverableSettlementQueue(RedisQueue):
            async def asettle_claim(self, worker_id, entry):
                raise ConnectionError("Redis is unavailable")

        queue = UnrecoverableSettlementQueue(
            redis_client, queue_name=f"lease-unrecoverable-{uuid4().hex}"
        )

        async def exercise():
            await queue.aenqueue("first")
            await queue.aenqueue("second")
            handled_payloads = []

            async def handle(entry):
                handled_payloads.append(entry.payload)
                return entry.payload

            worker = AsyncQueueWorker({"requests": queue}, {"requests": handle})
            with pytest.raises(
                QueuePersistenceError, match="Unable to persist terminal queue outcome"
            ):
                await worker.run()

            assert handled_payloads == ["first"]
            assert worker.snapshot.active_entry_id is None
            await queue.aclose()

        asyncio.run(exercise())

    def test_worker_stops_when_claim_persistence_fallback_loses_ownership(
        self, redis_client
    ):
        class LostFallbackSettlementQueue(RedisQueue):
            attempts = 0

            async def asettle_claim(self, worker_id, entry):
                self.attempts += 1
                if self.attempts == 1:
                    raise ConnectionError("Redis is unavailable")
                return False

        queue = LostFallbackSettlementQueue(
            redis_client, queue_name=f"lease-lost-fallback-{uuid4().hex}"
        )

        async def exercise():
            await queue.aenqueue("work")

            async def handle(entry):
                return entry.payload

            worker = AsyncQueueWorker({"requests": queue}, {"requests": handle})
            with pytest.raises(
                QueuePersistenceError, match="Unable to persist terminal queue outcome"
            ):
                await worker.run()

            assert queue.attempts == 2
            await queue.aclose()

        asyncio.run(exercise())

    def test_worker_dispatches_a_recovered_running_entry(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")

        async def exercise():
            await redis_entry_queue.aclaim_entry(uuid4(), lease_seconds=0.001)
            running = await redis_entry_queue.amark_running(entry_id)
            await asyncio.sleep(0.01)
            assert await redis_entry_queue.arecover_expired_claims() == 1
            recovered = await redis_entry_queue.aget_entry(entry_id)
            assert recovered.status is QueueEntryStatus.QUEUED
            assert recovered.dispatched_at is None

            async def handle(entry):
                return entry.payload

            worker = AsyncQueueWorker(
                {"requests": redis_entry_queue},
                {"requests": handle},
                idle_delay=0.001,
            )
            task = asyncio.create_task(worker.run())
            while (
                await redis_entry_queue.aget_entry(entry_id)
            ).status is not QueueEntryStatus.SUCCEEDED:
                await asyncio.sleep(0.001)
            succeeded = await redis_entry_queue.aget_entry(entry_id)
            assert succeeded.dispatched_at is not None
            assert succeeded.dispatched_at > running.dispatched_at
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_worker_continues_after_losing_a_claim_during_dispatch(
        self, redis_entry_queue
    ):
        first_id = redis_entry_queue.enqueue("first")
        second_id = redis_entry_queue.enqueue("second")
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def exercise():
            async def handle(entry):
                if entry.id == first_id:
                    first_started.set()
                    await release_first.wait()
                return entry.payload

            worker = AsyncQueueWorker(
                {"requests": redis_entry_queue},
                {"requests": handle},
                idle_delay=0.001,
            )
            task = asyncio.create_task(worker.run())
            await asyncio.wait_for(first_started.wait(), timeout=1)
            await redis_entry_queue._async_redis().delete(
                redis_entry_queue._entry_claim_key(first_id)
            )
            release_first.set()
            while (
                await redis_entry_queue.aget_entry(second_id)
            ).status is not QueueEntryStatus.SUCCEEDED:
                await asyncio.sleep(0.001)
            while worker.snapshot.active_entry_id is not None:
                await asyncio.sleep(0.001)

            assert worker.running
            assert worker.snapshot.active_entry_id is None
            assert worker.snapshot.active_queue_name is None
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_worker_cancels_a_handler_when_its_claim_is_lost_during_renewal(
        self, redis_entry_queue
    ):
        entry_id = redis_entry_queue.enqueue("work", timeout_seconds=0.01)
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def exercise():
            async def handle(entry):
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise

            worker = AsyncQueueWorker(
                {"requests": redis_entry_queue},
                {"requests": handle},
                idle_delay=0.001,
                cancellation_grace_period=0.001,
            )
            task = asyncio.create_task(worker.run())
            await asyncio.wait_for(started.wait(), timeout=1)
            await redis_entry_queue._async_redis().delete(
                redis_entry_queue._entry_claim_key(entry_id)
            )
            await asyncio.wait_for(cancelled.wait(), timeout=1)

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_synchronous_claim_lease_wrappers(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")
        worker_id = uuid4()

        claimed = redis_entry_queue.claim_entry(worker_id, lease_seconds=60)
        assert claimed.id == entry_id
        assert redis_entry_queue.renew_claim(entry_id, worker_id, lease_seconds=60)
        assert redis_entry_queue.acknowledge_claim(entry_id, worker_id)
        assert redis_entry_queue.recover_expired_claims() == 0

        settled_id = redis_entry_queue.enqueue("settled")
        claimed = redis_entry_queue.claim_entry(worker_id)
        running = redis_entry_queue.mark_running(settled_id)
        terminal = replace(
            running,
            status=QueueEntryStatus.SUCCEEDED,
            result="done",
            finished_at=redis_entry_queue.clock.now(),
        )
        assert redis_entry_queue.settle_claim(worker_id, terminal)
        assert redis_entry_queue.get_entry(claimed.id) == terminal

    def test_claim_and_acknowledge_entries_with_the_configured_encoding(
        self, redis_client
    ):
        queue = RedisQueue(
            redis_client, queue_name=f"claim-encoding-{uuid4().hex}", encoding="utf-16"
        )
        entry_id = queue.enqueue("work")
        worker_id = uuid4()

        assert queue.claim_entry(worker_id).id == entry_id
        assert queue.acknowledge_claim(entry_id, worker_id)

    def test_claim_mark_and_acknowledge_stack_entries_with_a_non_utf8_client_encoding(
        self, redis_client
    ):
        client = redis.Redis(
            host=redis_client.connection_pool.connection_kwargs["host"],
            port=redis_client.connection_pool.connection_kwargs["port"],
            encoding="utf-16",
        )
        queue = RedisStack(client, queue_name=f"claim-client-encoding-{uuid4().hex}")
        queue.enqueue("first")
        entry_id = queue.enqueue("latest")
        worker_id = uuid4()

        try:
            assert queue.claim_entry(worker_id).id == entry_id
            running = queue.mark_claim_running(entry_id, worker_id)
            assert running is not None
            assert running.status is QueueEntryStatus.RUNNING
            assert queue.acknowledge_claim(entry_id, worker_id)
        finally:
            client.close()

    def test_claim_outcomes_with_a_decoding_client(self, redis_client):
        client = redis.Redis(
            host=redis_client.connection_pool.connection_kwargs["host"],
            port=redis_client.connection_pool.connection_kwargs["port"],
            decode_responses=True,
        )
        queue = RedisQueue(client, queue_name=f"claim-decoding-{uuid4().hex}")

        try:
            claimed_entry_id = queue.enqueue("claimed")
            assert queue.claim_entry(uuid4()).id == claimed_entry_id
            with pytest.raises(QueueEmptyException):
                queue.claim_entry(uuid4())

            conflicted_entry_id = queue.enqueue("conflicted")
            client.set(queue._entry_claim_key(conflicted_entry_id), "already claimed")
            with pytest.raises(QueueClaimConflictError) as raised:
                queue.claim_entry(uuid4())

            assert raised.value.entry_id == conflicted_entry_id
        finally:
            client.close()

    @pytest.mark.parametrize("redis_spec_type", ["client", "url"])
    def test_rejects_a_decoding_client_with_a_non_utf8_queue_encoding(
        self, redis_client, redis_spec_type
    ):
        host = redis_client.connection_pool.connection_kwargs["host"]
        port = redis_client.connection_pool.connection_kwargs["port"]
        client = None
        if redis_spec_type == "client":
            client = redis.Redis(host=host, port=port, decode_responses=True)
            redis_spec = client
        else:
            redis_spec = f"redis://{host}:{port}/0?decode_responses=true"

        try:
            with pytest.raises(
                InvalidQueueBackendError,
                match="decode_responses.*non-UTF-8 queue encoding",
            ):
                RedisQueue(redis_spec, encoding="utf-16")
        finally:
            if client is not None:
                client.close()

    @pytest.mark.parametrize("encoding", ["U8", "cp65001"])
    def test_accepts_utf8_encoding_aliases_with_a_decoding_client(
        self, redis_client, encoding
    ):
        client = redis.Redis(
            host=redis_client.connection_pool.connection_kwargs["host"],
            port=redis_client.connection_pool.connection_kwargs["port"],
            decode_responses=True,
        )

        try:
            RedisQueue(client, encoding=encoding)
        finally:
            client.close()

    def test_rejects_an_unknown_queue_encoding(self, redis_client):
        with pytest.raises(InvalidQueueBackendError, match="Queue encoding is invalid"):
            RedisQueue(redis_client, encoding="not-an-encoding")

    @pytest.mark.parametrize("redis_url", ["not-a-url", "http://example.com"])
    def test_rejects_an_invalid_redis_url(self, redis_url):
        with pytest.raises(InvalidQueueBackendError, match="Redis URL is invalid"):
            RedisQueue(redis_url)

    def test_does_not_acknowledge_a_malformed_claim_record(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")

        async def exercise():
            await redis_entry_queue._async_redis().set(
                redis_entry_queue._entry_claim_key(entry_id), "not json"
            )

            assert not await redis_entry_queue.aacknowledge_claim(entry_id, uuid4())
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_claiming_a_missing_entry_record_reports_its_entry_id(
        self, redis_entry_queue
    ):
        entry_id = redis_entry_queue.enqueue("work")
        worker_id = uuid4()

        async def exercise():
            await redis_entry_queue._async_redis().delete(
                redis_entry_queue._entry_key(entry_id)
            )

            with pytest.raises(QueueEntryMissingError) as raised:
                await redis_entry_queue.aclaim_entry(worker_id)

            assert raised.value.entry_id == entry_id
            assert await redis_entry_queue._async_redis().exists(
                redis_entry_queue._entry_claim_key(entry_id)
            )
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    @pytest.mark.parametrize(
        ("queue_type", "claimed_payload", "remaining_payload"),
        [(RedisQueue, "first", "latest"), (RedisStack, "latest", "first")],
        ids=["queue-fifo", "stack-lifo"],
    )
    def test_preserves_pending_entries_that_are_already_claimed(
        self, redis_client, queue_type, claimed_payload, remaining_payload
    ):
        queue = queue_type(redis_client, queue_name=f"claim-order-{uuid4().hex}")
        first_entry_id = queue.enqueue("first")
        latest_entry_id = queue.enqueue("latest")
        expected_entry_id = latest_entry_id if queue.stack else first_entry_id

        async def exercise():
            await queue._async_redis().set(
                queue._entry_claim_key(expected_entry_id), "already claimed"
            )
            with pytest.raises(QueueClaimConflictError) as raised:
                await queue.aclaim_entry(uuid4())

            assert raised.value.entry_id == expected_entry_id
            assert await queue.ahas_pending_entries()
            assert await queue._async_redis().exists(
                queue._entry_claim_key(expected_entry_id)
            )
            assert (await queue.adequeue_entry()).payload == claimed_payload
            assert (await queue.adequeue_entry()).payload == remaining_payload
            await queue.aclose()

        asyncio.run(exercise())

    def test_dequeues_entries_with_the_configured_encoding(self, redis_client):
        queue = RedisQueue(
            redis_client, queue_name=f"entry-encoding-{uuid4().hex}", encoding="utf-16"
        )
        entry_id = queue.enqueue("work")

        assert queue.dequeue_entry().id == entry_id

    def test_records_redis_timed_lifecycle_outcomes(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")

        running = redis_entry_queue.mark_running(entry_id)
        completed = redis_entry_queue.mark_succeeded(entry_id, {"ok": True})

        assert running.status is QueueEntryStatus.RUNNING
        assert isinstance(running.dispatched_at, ClockTime)
        assert completed.status is QueueEntryStatus.SUCCEEDED
        assert isinstance(completed.finished_at, ClockTime)
        assert completed.result == {"ok": True}

    def test_records_a_redis_timeout_as_a_terminal_outcome(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")
        redis_entry_queue.mark_running(entry_id)

        timed_out = redis_entry_queue.mark_timed_out(entry_id)

        assert timed_out.status is QueueEntryStatus.TIMEOUT
        assert isinstance(timed_out.finished_at, ClockTime)

    def test_persists_a_redis_execution_budget(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work", timeout_seconds=2.5)

        assert redis_entry_queue.get_entry(entry_id).timeout_seconds == 2.5

    def test_restores_redis_lifecycle_timestamps_to_equal_instants(
        self, redis_entry_queue
    ):
        """Round-trips through the durable form, not just through memory."""
        entry_id = redis_entry_queue.enqueue("work")
        running = redis_entry_queue.mark_running(entry_id)

        restored = redis_entry_queue.get_entry(entry_id)

        assert restored.queued_at == running.queued_at
        assert restored.dispatched_at == running.dispatched_at

    def test_uses_its_configured_entry_subclass_for_lifecycle_operations(
        self, redis_entry_queue
    ):
        redis_entry_queue.entry_class = CustomQueueEntry

        entry_id = redis_entry_queue.enqueue("work")
        queued = redis_entry_queue.get_entry(entry_id)
        running = redis_entry_queue.mark_running(entry_id)
        completed = redis_entry_queue.mark_succeeded(entry_id, "done")

        assert isinstance(queued, CustomQueueEntry)
        assert isinstance(running, CustomQueueEntry)
        assert isinstance(completed, CustomQueueEntry)
        assert completed.kind == "task"


@pytest.mark.parametrize(
    "queue_type",
    [RedisQueueJson, RedisStack, RedisPriorityQueue],
    ids=["json", "stack", "priority"],
)
def test_redis_queue_variants_support_identified_entries(redis_client, queue_type):
    queue = queue_type(redis_client, queue_name=f"entry-variant-{uuid4().hex}")

    entry_id = queue.enqueue({"value": "work"})
    entry = queue.dequeue_entry()
    queue.mark_running(entry_id)
    completed = queue.mark_succeeded(entry_id, {"ok": True})

    assert entry.id == entry_id
    assert completed.status is QueueEntryStatus.SUCCEEDED


@pytest.mark.parametrize(
    ("queue_type", "expected_first_payload"),
    [(RedisQueue, "first"), (RedisStack, "latest")],
    ids=["queue-fifo", "stack-lifo"],
)
def test_redis_entry_dispatch_order_matches_queue_variant(
    redis_client, queue_type, expected_first_payload
):
    queue = queue_type(redis_client, queue_name=f"entry-order-{uuid4().hex}")
    queue.enqueue("first")
    queue.enqueue("latest")

    assert queue.dequeue_entry().payload == expected_first_payload
