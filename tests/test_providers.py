import asyncio
from typing import runtime_checkable
from uuid import uuid4

import pytest

import django_queue
from django_queue import QueueProvider
from django_queue.backends.exceptions import QueueClaimConflictError
from django_queue.backends.memory.provider import QueueProviderMemory
from django_queue.backends.redis.provider import QueueProviderRedis
from django_queue.entries import QueueEntry


def test_providers_implement_the_minimal_public_provider_contract():
    assert runtime_checkable(QueueProvider)
    assert isinstance(QueueProviderMemory(), QueueProvider)
    assert isinstance(
        QueueProviderRedis("redis://localhost:6379/0", entry_class=QueueEntry),
        QueueProvider,
    )
    assert hasattr(QueueProvider, "aclose")
    assert not hasattr(QueueProvider, "clock")
    assert not hasattr(QueueProvider, "aclaim")
    assert not hasattr(QueueProvider, "astore")
    assert "AsyncQueueProvider" not in django_queue.__all__
    assert "EventQueueProvider" not in django_queue.__all__
    assert "QueueProviderMemory" not in django_queue.__all__
    assert "QueueProviderRedis" not in django_queue.__all__


def test_redis_provider_claims_and_removes_an_owned_entry(redis_client):
    async def exercise():
        provider = QueueProviderRedis(
            redis_client,
            queue_name=f"provider-contract-{uuid4().hex}",
            entry_class=QueueEntry,
        )
        try:
            entry = QueueEntry.create(queue="events", payload={"event": "sent"})
            await provider.astore(entry)
            await provider.apush(entry.id)

            worker_id = uuid4()
            assert await provider.aclaim(worker_id) == entry
            assert await provider.aremove(entry.id, worker_id)
        finally:
            await provider.aclose()

    asyncio.run(exercise())


def test_memory_provider_does_not_replace_an_active_claim_with_a_duplicate_id():
    async def exercise():
        provider = QueueProviderMemory()
        entry = QueueEntry.create(queue="events", payload={"event": "sent"})
        first_worker = uuid4()
        await provider.astore(entry)
        await provider.apush(entry.id)
        assert await provider.aclaim(first_worker) == entry

        await provider.apush(entry.id)
        with pytest.raises(QueueClaimConflictError):
            await provider.aclaim(uuid4())
        assert not await provider.aremove(entry.id, uuid4())
        assert await provider.aremove(entry.id, first_worker)

    asyncio.run(exercise())
