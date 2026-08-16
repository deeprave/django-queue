import asyncio
import json
import logging
import threading
import time
from dataclasses import replace
from uuid import uuid4

import pytest
import redis

import django_queue
from django_queue import queue_observer
from django_queue.backends.exceptions import (
    InvalidQueueBackendError,
    QueueClaimConflictError,
    QueueEmptyException,
    QueueEntryMissingError,
    QueueEntryNotFoundError,
)
from django_queue.backends.redis import (
    RedisAsyncPriorityQueue,
    RedisAsyncQueue,
    RedisAsyncQueueJson,
    RedisAsyncQueueWorker,
    RedisAsyncStack,
)
from django_queue.clock import ClockTime, QueueClockError
from django_queue.entries import QueueEntryStatus
from django_queue.worker import QueuePersistenceError
from tests.helpers import CustomQueueEntry


@pytest.fixture
def redis_entry_queue(redis_client):
    return RedisAsyncQueue(redis_client, queue_name=f"entry-contract-{uuid4().hex}")


class TestRedisAsyncQueueEntries:
    def test_get_entry_reports_a_missing_retained_entry(self, redis_entry_queue):
        with pytest.raises(QueueEntryNotFoundError):
            redis_entry_queue.get_entry(uuid4())

    def test_prunes_a_terminal_entry(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")
        redis_entry_queue._mark_running(entry_id)
        redis_entry_queue._mark_succeeded(entry_id, "done")

        redis_entry_queue.prune_entry(entry_id)

        with pytest.raises(QueueEntryNotFoundError):
            redis_entry_queue.get_entry(entry_id)

    def test_prune_reports_an_absent_entry(self, redis_entry_queue):
        with pytest.raises(QueueEntryNotFoundError):
            redis_entry_queue.prune_entry(uuid4())

    def test_prune_refuses_a_non_terminal_entry(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")

        with pytest.raises(ValueError, match="terminal"):
            redis_entry_queue.prune_entry(entry_id)

        assert redis_entry_queue.get_entry(entry_id).status is QueueEntryStatus.QUEUED

    def test_pruning_preserves_an_empty_array_payload_in_terminated_snapshot(
        self, redis_entry_queue, monkeypatch
    ):
        observed = []
        entry_id = redis_entry_queue.enqueue([])
        redis_entry_queue._mark_running(entry_id)
        redis_entry_queue._mark_succeeded(entry_id, "done")

        async def publish(entry):
            observed.append(entry)

        monkeypatch.setattr(redis_entry_queue, "apublish_lifecycle_snapshot", publish)
        redis_entry_queue.prune_entry(entry_id)

        assert observed[-1].status is QueueEntryStatus.TERMINATED
        assert observed[-1].payload == []

    def test_scheduled_expiry_prunes_a_terminal_entry(self, redis_entry_queue):
        async def exercise():
            try:
                redis_entry_queue.retention_timeout = 0
                entry_id = await redis_entry_queue.aenqueue("work")
                await redis_entry_queue._amark_running(entry_id)
                await redis_entry_queue._amark_succeeded(entry_id, "done")

                assert await redis_entry_queue._aprune_expired_entries() == 1
                with pytest.raises(QueueEntryNotFoundError):
                    await redis_entry_queue.aget_entry(entry_id)
            finally:
                await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_pruning_publishes_a_terminated_snapshot_to_a_redis_observer(
        self, redis_client, monkeypatch
    ):
        queue_name = f"entry-observer-{uuid4().hex}"
        handler = django_queue.QueueHandler(
            {
                "requests": {
                    "BACKEND": "django_queue.backends.redis.RedisAsyncQueue",
                    "LOCATION": redis_client,
                    "queue_name": queue_name,
                }
            }
        )
        monkeypatch.setattr(django_queue, "queues", handler)
        queue = handler["requests"]
        terminated = threading.Event()
        snapshots = []
        subscription = queue_observer(
            "requests",
            lambda entry: (
                snapshots.append(entry),
                terminated.set()
                if entry.status is QueueEntryStatus.TERMINATED
                else None,
            ),
        )
        try:
            entry_id = queue.enqueue([])
            queue._mark_running(entry_id)
            queue._mark_succeeded(entry_id, "done")
            time.sleep(0.05)
            queue.prune_entry(entry_id)

            assert terminated.wait(1)
            assert snapshots[-1].status is QueueEntryStatus.TERMINATED
            assert snapshots[-1].payload == []
        finally:
            subscription.unsubscribe()

    def test_pre_dispatch_failure_removes_pending_work(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")

        failed = redis_entry_queue._mark_failed(entry_id, ValueError("unavailable"))

        assert failed.dispatched_at is None
        assert not redis_entry_queue.has_pending_entries()

    def test_observer_transport_accepts_a_terminated_snapshot(
        self, redis_entry_queue, monkeypatch
    ):
        entry = replace(
            redis_entry_queue.entry_class.create(
                queue=redis_entry_queue.queue_name, payload="work"
            ),
            status=QueueEntryStatus.TERMINATED,
        )

        class FakePubSub:
            def subscribe(self, channel):
                self.channel = channel

            def listen(self):
                yield {"type": "message", "data": json.dumps(entry.to_dict())}

            def close(self):
                pass

        class FakeClient:
            def pubsub(self, **kwargs):
                return FakePubSub()

            def close(self):
                pass

        monkeypatch.setattr(
            redis_entry_queue._provider, "_observer_redis_client", FakeClient
        )
        received = []

        redis_entry_queue._provider.receive_lifecycle_snapshots(received.append)

        assert received == [entry]

    def test_observer_transport_continues_to_a_valid_snapshot_after_invalid_data(
        self, redis_entry_queue, monkeypatch
    ):
        entry = redis_entry_queue.entry_class.create(
            queue=redis_entry_queue.queue_name, payload="work"
        )

        class FakePubSub:
            def subscribe(self, channel):
                self.channel = channel

            def listen(self):
                yield {"type": "message", "data": b"not-json"}
                yield {"type": "message", "data": json.dumps(entry.to_dict())}

            def close(self):
                pass

        class FakeClient:
            def pubsub(self, **kwargs):
                return FakePubSub()

            def close(self):
                pass

        def observer_client():
            return FakeClient()

        monkeypatch.setattr(
            redis_entry_queue._provider, "_observer_redis_client", observer_client
        )
        received = []

        redis_entry_queue._provider.receive_lifecycle_snapshots(received.append)

        assert received == [entry]

    def test_persists_entry_when_lifecycle_publication_fails(
        self, redis_entry_queue, monkeypatch
    ):
        async def exercise():
            client = redis_entry_queue._provider._async_redis()

            async def fail_publish(*args, **kwargs):
                raise redis.ConnectionError("unavailable")

            monkeypatch.setattr(client, "publish", fail_publish)

            async def handle(entry):
                return entry.payload

            worker = RedisAsyncQueueWorker(
                {"requests": redis_entry_queue},
                {"requests": handle},
                idle_delay=0.001,
            )
            entry_id = await redis_entry_queue.aenqueue("work")
            task = asyncio.create_task(worker.run())
            while (
                await redis_entry_queue.aget_entry(entry_id)
            ).status is not QueueEntryStatus.SUCCEEDED:
                await asyncio.sleep(0.001)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            entry = await redis_entry_queue.aget_entry(entry_id)
            await redis_entry_queue.aclose()
            return entry

        entry = asyncio.run(exercise())

        assert entry.status is QueueEntryStatus.SUCCEEDED
        assert entry.payload == "work"

    def test_uses_distinct_clients_for_a_worker_loop_and_sync_bridge(
        self, redis_client
    ):
        queue = RedisAsyncQueue(redis_client, queue_name=f"entry-loops-{uuid4().hex}")
        clients_by_loop = {}
        original_async_redis = queue._provider._async_redis

        def record_client():
            loop = asyncio.get_running_loop()
            client = original_async_redis()
            clients_by_loop[loop] = client
            return client

        queue._provider._async_redis = record_client
        worker_ready = threading.Event()
        release_worker = threading.Event()
        worker_failure = []

        async def handle(entry):
            return entry.payload

        def run_worker_loop():
            async def exercise():
                entry_id = await queue.aenqueue({"from": "worker"})
                worker = RedisAsyncQueueWorker(
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
            worker_client = next(iter(queue._provider._async_redis_by_loop.values()))

            queue.enqueue({"from": "bridge"})

            assert len(clients_by_loop) == 2
            assert len(set(clients_by_loop.values())) == 2
            assert list(queue._provider._async_redis_by_loop.values()) == [
                worker_client
            ]
        finally:
            release_worker.set()
            worker_thread.join(timeout=1)

        assert not worker_thread.is_alive()
        assert not worker_failure

    def test_clock_now_requires_async_calibration_on_an_active_loop(
        self, redis_entry_queue
    ):
        async def exercise():
            with pytest.raises(QueueClockError, match="await queue.clock.anow"):
                redis_entry_queue.clock.now()

        asyncio.run(exercise())

    def test_awaited_entry_lifecycle_and_loop_local_client_reuse(self, redis_client):
        queue = RedisAsyncQueue(redis_client, queue_name=f"entry-async-{uuid4().hex}")

        async def exercise():
            entry_id = await queue.aenqueue({"request_id": 42})
            assert (await queue.adequeue_entry()).id == entry_id
            assert (
                await queue._amark_running(entry_id)
            ).status is QueueEntryStatus.RUNNING
            completed = await queue._amark_succeeded(entry_id, {"ok": True})
            assert completed.status is QueueEntryStatus.SUCCEEDED
            first_client = queue._provider._async_redis()
            first_scripts = queue._provider._async_scripts_by_loop.copy()
            await queue.aclose()
            assert not queue._provider._async_redis_by_loop
            assert not queue._provider._async_scripts_by_loop
            assert queue._provider._async_redis() is not first_client
            assert queue._provider._async_scripts_by_loop != first_scripts
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

    def test_observer_bootstrap_lists_retained_entry_snapshots(self, redis_entry_queue):
        queued_id = redis_entry_queue.enqueue("queued")
        completed_id = redis_entry_queue.enqueue("completed")
        redis_entry_queue._mark_running(completed_id)
        redis_entry_queue._mark_succeeded(completed_id, "done")

        entries = redis_entry_queue._list_entries()

        assert {entry.id for entry in entries} == {queued_id, completed_id}
        assert {entry.status for entry in entries} == {
            QueueEntryStatus.QUEUED,
            QueueEntryStatus.SUCCEEDED,
        }

    def test_synchronous_entry_api_closes_each_bridge_loop_client(
        self, redis_entry_queue
    ):
        redis_entry_queue.enqueue("work")

        assert not redis_entry_queue._provider._async_redis_by_loop

    def test_synchronous_clock_reads_close_each_bridge_loop_client(
        self, redis_entry_queue
    ):
        redis_entry_queue.clock.now()
        redis_entry_queue.clock.now()

        assert not redis_entry_queue._provider._async_redis_by_loop
        assert not redis_entry_queue._provider._clocks_by_loop

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
                redis_entry_queue._provider.aclaim(first_worker),
                redis_entry_queue._provider.aclaim(second_worker),
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
            claimed = await redis_entry_queue._provider.aclaim(worker_id)
            raw_claim = await redis_entry_queue._provider._async_redis().get(
                redis_entry_queue._provider._claim_key(entry_id)
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
            await redis_entry_queue._provider.aclaim(owner_id)

            assert not await redis_entry_queue._provider.aack(entry_id, uuid4())
            assert await redis_entry_queue._provider._async_redis().exists(
                redis_entry_queue._provider._claim_key(entry_id)
            )
            assert await redis_entry_queue._provider.aack(entry_id, owner_id)
            assert not await redis_entry_queue._provider._async_redis().exists(
                redis_entry_queue._provider._claim_key(entry_id)
            )
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_renews_a_claim_only_for_its_owner(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")
        owner_id = uuid4()

        async def exercise():
            await redis_entry_queue._provider.aclaim(owner_id, lease_seconds=1)

            assert not await redis_entry_queue._provider.arenew(
                entry_id, uuid4(), lease_seconds=1
            )
            assert await redis_entry_queue._provider.arenew(
                entry_id, owner_id, lease_seconds=1
            )
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_does_not_renew_an_expired_claim(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")
        owner_id = uuid4()

        async def exercise():
            await redis_entry_queue._provider.aclaim(owner_id, lease_seconds=0.01)
            await asyncio.sleep(0.03)

            assert not await redis_entry_queue._provider.arenew(
                entry_id, owner_id, lease_seconds=1
            )
            assert (
                await redis_entry_queue._provider.arecover(
                    redis_entry_queue.recovery_batch_size
                )
            )[0] == 1
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_uses_a_backend_specific_default_claim_lease(self, redis_client):
        class ShortLeaseQueue(RedisAsyncQueue):
            default_claim_lease_seconds = 0.001

        queue = ShortLeaseQueue(redis_client, queue_name=f"lease-default-{uuid4().hex}")
        queue.enqueue("work")

        async def exercise():
            await queue._provider.aclaim(uuid4(), queue.default_claim_lease_seconds)
            await asyncio.sleep(0.01)

            assert (await queue._provider.arecover(queue.recovery_batch_size))[0] == 1
            await queue.aclose()

        asyncio.run(exercise())

    @pytest.mark.parametrize(
        ("queue_type", "first_payload", "second_payload"),
        [(RedisAsyncQueue, "first", "second"), (RedisAsyncStack, "second", "first")],
        ids=["queue-fifo", "stack-lifo"],
    )
    def test_recovers_expired_claims_in_queue_order(
        self, redis_client, queue_type, first_payload, second_payload
    ):
        queue = queue_type(redis_client, queue_name=f"lease-order-{uuid4().hex}")
        first_id = queue.enqueue("first")
        second_id = queue.enqueue("second")

        async def exercise():
            first_claim = await queue._provider.aclaim(uuid4(), lease_seconds=0.001)
            second_claim = await queue._provider.aclaim(uuid4(), lease_seconds=0.001)
            await asyncio.sleep(0.01)

            assert (await queue._provider.arecover(queue.recovery_batch_size))[0] == 2
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
            first_claim = await redis_entry_queue._provider.aclaim(
                uuid4(), lease_seconds=0.001
            )
            await asyncio.sleep(0.01)
            second_claim = await redis_entry_queue._provider.aclaim(
                uuid4(), lease_seconds=0.001
            )
            await asyncio.sleep(0.01)

            assert (
                await redis_entry_queue._provider.arecover(
                    redis_entry_queue.recovery_batch_size
                )
            )[0] == 1
            assert (await redis_entry_queue.adequeue_entry()).id == first_claim.id
            assert (
                await redis_entry_queue._provider.arecover(
                    redis_entry_queue.recovery_batch_size
                )
            )[0] == 1
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
                (
                    await redis_entry_queue._provider.arecover(
                        redis_entry_queue.recovery_batch_size
                    )
                )[0]
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_recovers_a_renewed_non_utf8_queue_claim(self, redis_client):
        queue = RedisAsyncQueue(
            redis_client,
            queue_name=f"lease-encoding-{uuid4().hex}",
            encoding="utf-16",
        )
        entry_id = queue.enqueue("work")
        worker_id = uuid4()

        async def exercise():
            await queue._provider.aclaim(worker_id, lease_seconds=0.001)
            assert await queue._provider.arenew(entry_id, worker_id, lease_seconds=0.01)
            await asyncio.sleep(0.02)

            assert (await queue._provider.arecover(queue.recovery_batch_size))[0] == 1
            assert (await queue.adequeue_entry()).id == entry_id
            await queue.aclose()

        asyncio.run(exercise())

    def test_does_not_recover_a_renewed_claim(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")
        worker_id = uuid4()

        async def exercise():
            await redis_entry_queue._provider.aclaim(worker_id, lease_seconds=0.01)
            assert await redis_entry_queue._provider.arenew(
                entry_id, worker_id, lease_seconds=1
            )
            await asyncio.sleep(0.02)

            assert (
                await redis_entry_queue._provider.arecover(
                    redis_entry_queue.recovery_batch_size
                )
            )[0] == 0
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
            await redis_entry_queue._provider.aclaim(uuid4())
            client = redis_entry_queue._provider._async_redis()
            if claim_value is None:
                await client.delete(redis_entry_queue._provider._claim_key(entry_id))
            else:
                await client.set(
                    redis_entry_queue._provider._claim_key(entry_id), claim_value
                )
            await client.zadd(
                redis_entry_queue._provider._entry_claim_deadlines_name,
                {str(entry_id): 0},
            )

            assert (
                await redis_entry_queue._provider.arecover(
                    redis_entry_queue.recovery_batch_size
                )
            )[0] == 1
            assert (await redis_entry_queue.adequeue_entry()).id == entry_id
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_logs_a_discarded_orphaned_claim_without_an_entry(
        self, redis_entry_queue, caplog
    ):
        entry_id = redis_entry_queue.enqueue("work")

        async def exercise():
            await redis_entry_queue._provider.aclaim(uuid4())
            client = redis_entry_queue._provider._async_redis()
            await client.delete(redis_entry_queue._provider._entry_key(entry_id))
            await client.set(
                redis_entry_queue._provider._claim_key(entry_id), "not json"
            )
            await client.zadd(
                redis_entry_queue._provider._entry_claim_deadlines_name,
                {str(entry_id): 0},
            )

            recovered, discarded = await redis_entry_queue._provider.arecover(
                redis_entry_queue.recovery_batch_size
            )
            assert recovered == 0
            assert discarded == 1
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_marks_claimed_entry_running_only_for_its_owner(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")
        owner_id = uuid4()

        async def exercise():
            await redis_entry_queue._provider.aclaim(owner_id)
            queued = await redis_entry_queue.aget_entry(entry_id)
            running = replace(
                queued,
                status=QueueEntryStatus.RUNNING,
                dispatched_at=await redis_entry_queue.clock.anow(),
            )

            assert not await redis_entry_queue._provider.amark_running(uuid4(), running)
            assert (
                await redis_entry_queue.aget_entry(entry_id)
            ).status is QueueEntryStatus.QUEUED

            assert await redis_entry_queue._provider.amark_running(owner_id, running)
            assert running.status is QueueEntryStatus.RUNNING
            assert (await redis_entry_queue.aget_entry(entry_id)) == running
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_settles_a_terminal_claim_only_for_its_owner(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")
        owner_id = uuid4()

        async def exercise():
            claimed = await redis_entry_queue._provider.aclaim(owner_id)
            running = await redis_entry_queue._amark_running(entry_id)
            terminal = replace(
                running,
                status=QueueEntryStatus.SUCCEEDED,
                result="done",
                finished_at=await redis_entry_queue.clock.anow(),
            )

            assert not await redis_entry_queue._provider.asettle(uuid4(), terminal)
            assert (await redis_entry_queue.aget_entry(entry_id)) == running
            assert await redis_entry_queue._provider.asettle(owner_id, terminal)
            assert (await redis_entry_queue.aget_entry(claimed.id)) == terminal
            assert not await redis_entry_queue._provider.aack(entry_id, owner_id)
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_worker_settles_redis_claim_after_terminal_success(self, redis_client):
        queue = RedisAsyncQueue(redis_client, queue_name=f"lease-worker-{uuid4().hex}")

        async def exercise():
            entry_id = await queue.aenqueue("work")

            async def handle(entry):
                return entry.payload

            worker = RedisAsyncQueueWorker(
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

            assert (await queue._provider.arecover(queue.recovery_batch_size))[0] == 0
            with pytest.raises(QueueEmptyException):
                await queue._provider.aclaim(uuid4())
            await queue.aclose()

        asyncio.run(exercise())

    def test_worker_settles_redis_claim_after_handler_failure(self, redis_client):
        queue = RedisAsyncQueue(redis_client, queue_name=f"lease-worker-{uuid4().hex}")

        async def exercise():
            entry_id = await queue.aenqueue("work")

            async def handle(entry):
                raise ValueError(entry.payload)

            worker = RedisAsyncQueueWorker(
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

            assert (await queue._provider.arecover(queue.recovery_batch_size))[0] == 0
            with pytest.raises(QueueEmptyException):
                await queue._provider.aclaim(uuid4())
            await queue.aclose()

        asyncio.run(exercise())

    def test_worker_records_a_claimed_persistence_failure_and_continues(
        self, redis_client
    ):
        queue = RedisAsyncQueue(
            redis_client, queue_name=f"lease-persistence-{uuid4().hex}"
        )

        async def exercise():
            original_settle = queue._provider.asettle
            fail_next_settlement = True

            async def fail_once(worker_id, entry):
                nonlocal fail_next_settlement
                if fail_next_settlement:
                    fail_next_settlement = False
                    raise ConnectionError("Redis is unavailable")
                return await original_settle(worker_id, entry)

            queue._provider.asettle = fail_once
            first_id = await queue.aenqueue("first")
            second_id = await queue.aenqueue("second")

            async def handle(entry):
                return entry.payload

            worker = RedisAsyncQueueWorker(
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
        queue = RedisAsyncQueue(
            redis_client, queue_name=f"lease-unrecoverable-{uuid4().hex}"
        )

        async def exercise():
            async def fail_settlement(worker_id, entry):
                raise ConnectionError("Redis is unavailable")

            queue._provider.asettle = fail_settlement
            await queue.aenqueue("first")
            await queue.aenqueue("second")
            handled_payloads = []

            async def handle(entry):
                handled_payloads.append(entry.payload)
                return entry.payload

            worker = RedisAsyncQueueWorker({"requests": queue}, {"requests": handle})
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
        queue = RedisAsyncQueue(
            redis_client, queue_name=f"lease-lost-fallback-{uuid4().hex}"
        )

        async def exercise():
            attempts = 0

            async def lose_settlement(worker_id, entry):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise ConnectionError("Redis is unavailable")
                return False

            queue._provider.asettle = lose_settlement
            await queue.aenqueue("work")

            async def handle(entry):
                return entry.payload

            worker = RedisAsyncQueueWorker({"requests": queue}, {"requests": handle})
            with pytest.raises(
                QueuePersistenceError, match="Unable to persist terminal queue outcome"
            ):
                await worker.run()

            assert attempts == 2
            await queue.aclose()

        asyncio.run(exercise())

    def test_worker_dispatches_a_recovered_running_entry(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")

        async def exercise():
            await redis_entry_queue._provider.aclaim(uuid4(), lease_seconds=0.001)
            running = await redis_entry_queue._amark_running(entry_id)
            await asyncio.sleep(0.01)
            assert (
                await redis_entry_queue._provider.arecover(
                    redis_entry_queue.recovery_batch_size
                )
            )[0] == 1
            recovered = await redis_entry_queue.aget_entry(entry_id)
            assert recovered.status is QueueEntryStatus.QUEUED
            assert recovered.dispatched_at is None

            async def handle(entry):
                return entry.payload

            worker = RedisAsyncQueueWorker(
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

            worker = RedisAsyncQueueWorker(
                {"requests": redis_entry_queue},
                {"requests": handle},
                idle_delay=0.001,
            )
            task = asyncio.create_task(worker.run())
            await asyncio.wait_for(first_started.wait(), timeout=1)
            await redis_entry_queue._provider._async_redis().delete(
                redis_entry_queue._provider._claim_key(first_id)
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
        self, redis_entry_queue, caplog
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

            worker = RedisAsyncQueueWorker(
                {"requests": redis_entry_queue},
                {"requests": handle},
                idle_delay=0.001,
                cancellation_grace_period=0.001,
            )
            with caplog.at_level(logging.WARNING, logger="django_queue.worker"):
                task = asyncio.create_task(worker.run())
                await asyncio.wait_for(started.wait(), timeout=1)
                await redis_entry_queue._provider._async_redis().delete(
                    redis_entry_queue._provider._claim_key(entry_id)
                )
                await asyncio.wait_for(cancelled.wait(), timeout=1)

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await redis_entry_queue.aclose()

        asyncio.run(exercise())
        assert f"Lost claim for queue entry {entry_id} during renewal" in caplog.text

    def test_claim_lease_operations_are_internal_and_asynchronous(
        self, redis_entry_queue
    ):
        entry_id = redis_entry_queue.enqueue("work")
        worker_id = uuid4()

        async def exercise():
            claimed = await redis_entry_queue._provider.aclaim(
                worker_id, lease_seconds=60
            )
            assert claimed.id == entry_id
            assert await redis_entry_queue._provider.arenew(
                entry_id, worker_id, lease_seconds=60
            )
            assert await redis_entry_queue._provider.aack(entry_id, worker_id)
            assert (
                await redis_entry_queue._provider.arecover(
                    redis_entry_queue.recovery_batch_size
                )
            )[0] == 0

            settled_id = await redis_entry_queue.aenqueue("settled")
            claimed = await redis_entry_queue._provider.aclaim(worker_id)
            running = await redis_entry_queue._amark_running(settled_id)
            terminal = replace(
                running,
                status=QueueEntryStatus.SUCCEEDED,
                result="done",
                finished_at=await redis_entry_queue.clock.anow(),
            )
            assert await redis_entry_queue._provider.asettle(worker_id, terminal)
            assert await redis_entry_queue.aget_entry(claimed.id) == terminal
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_claim_and_acknowledge_entries_with_the_configured_encoding(
        self, redis_client
    ):
        queue = RedisAsyncQueue(
            redis_client, queue_name=f"claim-encoding-{uuid4().hex}", encoding="utf-16"
        )
        entry_id = queue.enqueue("work")
        worker_id = uuid4()

        async def exercise():
            assert (await queue._provider.aclaim(worker_id)).id == entry_id
            assert await queue._provider.aack(entry_id, worker_id)
            await queue.aclose()

        asyncio.run(exercise())

    def test_claim_mark_and_acknowledge_stack_entries_with_a_non_utf8_queue_encoding(
        self, redis_url
    ):
        queue = RedisAsyncStack(
            redis_url,
            queue_name=f"claim-queue-encoding-{uuid4().hex}",
            encoding="utf-16",
        )
        queue.enqueue("first")
        entry_id = queue.enqueue("latest")
        worker_id = uuid4()

        async def exercise():
            assert (await queue._provider.aclaim(worker_id)).id == entry_id
            queued = await queue.aget_entry(entry_id)
            running = replace(
                queued,
                status=QueueEntryStatus.RUNNING,
                dispatched_at=await queue.clock.anow(),
            )
            assert await queue._provider.amark_running(worker_id, running)
            assert running.status is QueueEntryStatus.RUNNING
            assert await queue._provider.aack(entry_id, worker_id)
            await queue.aclose()

        asyncio.run(exercise())

    def test_rejects_a_decoding_url_with_a_non_utf8_queue_encoding(self, redis_url):
        with pytest.raises(
            InvalidQueueBackendError,
            match="decode_responses.*non-UTF-8 queue encoding",
        ):
            RedisAsyncQueue(f"{redis_url}?decode_responses=true", encoding="utf-16")

    @pytest.mark.parametrize("encoding", ["U8", "cp65001"])
    def test_accepts_utf8_encoding_aliases_with_a_decoding_url(
        self, redis_url, encoding
    ):
        RedisAsyncQueue(f"{redis_url}?decode_responses=true", encoding=encoding)

    def test_rejects_an_unknown_queue_encoding(self, redis_client):
        with pytest.raises(InvalidQueueBackendError, match="Queue encoding is invalid"):
            RedisAsyncQueue(redis_client, encoding="not-an-encoding")

    @pytest.mark.parametrize("redis_url", ["not-a-url", "http://example.com"])
    def test_rejects_an_invalid_redis_url(self, redis_url):
        with pytest.raises(InvalidQueueBackendError, match="Redis URL is invalid"):
            RedisAsyncQueue(redis_url)

    def test_does_not_acknowledge_a_malformed_claim_record(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")

        async def exercise():
            await redis_entry_queue._provider._async_redis().set(
                redis_entry_queue._provider._claim_key(entry_id), "not json"
            )

            assert not await redis_entry_queue._provider.aack(entry_id, uuid4())
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    def test_claiming_a_missing_entry_record_reports_its_entry_id(
        self, redis_entry_queue
    ):
        entry_id = redis_entry_queue.enqueue("work")
        worker_id = uuid4()

        async def exercise():
            await redis_entry_queue._provider._async_redis().delete(
                redis_entry_queue._provider._entry_key(entry_id)
            )

            with pytest.raises(QueueEntryMissingError) as raised:
                await redis_entry_queue._provider.aclaim(worker_id)

            assert raised.value.entry_id == entry_id
            assert await redis_entry_queue._provider._async_redis().exists(
                redis_entry_queue._provider._claim_key(entry_id)
            )
            await redis_entry_queue.aclose()

        asyncio.run(exercise())

    @pytest.mark.parametrize(
        ("queue_type", "claimed_payload", "remaining_payload"),
        [(RedisAsyncQueue, "first", "latest"), (RedisAsyncStack, "latest", "first")],
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
            await queue._provider._async_redis().set(
                queue._provider._claim_key(expected_entry_id), "already claimed"
            )
            with pytest.raises(QueueClaimConflictError) as raised:
                await queue._provider.aclaim(uuid4())

            assert raised.value.entry_id == expected_entry_id
            assert await queue.ahas_pending_entries()
            assert await queue._provider._async_redis().exists(
                queue._provider._claim_key(expected_entry_id)
            )
            assert (await queue.adequeue_entry()).payload == claimed_payload
            assert (await queue.adequeue_entry()).payload == remaining_payload
            await queue.aclose()

        asyncio.run(exercise())

    def test_treats_delayed_entries_as_pending(self, redis_client):
        queue = RedisAsyncQueue(
            redis_client, queue_name=f"delayed-pending-{uuid4().hex}"
        )
        entry_id = queue.enqueue("later")

        async def exercise():
            worker_id = uuid4()
            entry = await queue._provider.aclaim(worker_id)

            assert entry.id == entry_id
            assert await queue._provider.arelease(entry_id, worker_id, 60)
            assert await queue.ahas_pending_entries()
            await queue.aclose()

        asyncio.run(exercise())

    def test_dequeues_entries_with_the_configured_encoding(self, redis_client):
        queue = RedisAsyncQueue(
            redis_client, queue_name=f"entry-encoding-{uuid4().hex}", encoding="utf-16"
        )
        entry_id = queue.enqueue("work")

        assert queue.dequeue_entry().id == entry_id

    def test_records_redis_timed_lifecycle_outcomes(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")

        running = redis_entry_queue._mark_running(entry_id)
        completed = redis_entry_queue._mark_succeeded(entry_id, {"ok": True})

        assert running.status is QueueEntryStatus.RUNNING
        assert isinstance(running.dispatched_at, ClockTime)
        assert completed.status is QueueEntryStatus.SUCCEEDED
        assert isinstance(completed.finished_at, ClockTime)
        assert completed.result == {"ok": True}

    def test_records_a_redis_timeout_as_a_terminal_outcome(self, redis_entry_queue):
        entry_id = redis_entry_queue.enqueue("work")
        redis_entry_queue._mark_running(entry_id)

        timed_out = redis_entry_queue._mark_timed_out(entry_id)

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
        running = redis_entry_queue._mark_running(entry_id)

        restored = redis_entry_queue.get_entry(entry_id)

        assert restored.queued_at == running.queued_at
        assert restored.dispatched_at == running.dispatched_at

    def test_uses_its_configured_entry_subclass_for_lifecycle_operations(
        self, redis_entry_queue
    ):
        redis_entry_queue.entry_class = CustomQueueEntry

        entry_id = redis_entry_queue.enqueue("work")
        queued = redis_entry_queue.get_entry(entry_id)
        running = redis_entry_queue._mark_running(entry_id)
        completed = redis_entry_queue._mark_succeeded(entry_id, "done")

        assert isinstance(queued, CustomQueueEntry)
        assert isinstance(running, CustomQueueEntry)
        assert isinstance(completed, CustomQueueEntry)
        assert completed.kind == "task"


@pytest.mark.parametrize(
    "queue_type",
    [RedisAsyncQueueJson, RedisAsyncStack, RedisAsyncPriorityQueue],
    ids=["json", "stack", "priority"],
)
def test_redis_queue_variants_support_identified_entries(redis_client, queue_type):
    queue = queue_type(redis_client, queue_name=f"entry-variant-{uuid4().hex}")

    entry_id = queue.enqueue({"value": "work"})
    entry = queue.dequeue_entry()
    queue._mark_running(entry_id)
    completed = queue._mark_succeeded(entry_id, {"ok": True})

    assert entry.id == entry_id
    assert completed.status is QueueEntryStatus.SUCCEEDED


@pytest.mark.parametrize(
    ("queue_type", "expected_first_payload"),
    [(RedisAsyncQueue, "first"), (RedisAsyncStack, "latest")],
    ids=["queue-fifo", "stack-lifo"],
)
def test_redis_entry_dispatch_order_matches_queue_variant(
    redis_client, queue_type, expected_first_payload
):
    queue = queue_type(redis_client, queue_name=f"entry-order-{uuid4().hex}")
    queue.enqueue("first")
    queue.enqueue("latest")

    assert queue.dequeue_entry().payload == expected_first_payload
