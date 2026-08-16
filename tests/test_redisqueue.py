import asyncio
from unittest.mock import AsyncMock

import pytest

from django_queue.backends import (
    QueueEmptyException,
    QueueFullException,
)
from django_queue.backends.exceptions import (
    InvalidQueueBackendError,
    QueueEncodingException,
)
from django_queue.backends.redis import RedisAsyncQueue, RedisAsyncQueueWorker
from django_queue.backends.redis.provider import QueueProviderRedis
from django_queue.entries import QueueEntry
from django_queue.worker import AsyncQueueWorker


@pytest.fixture
def redis_queue(redis_url):
    queue = RedisAsyncQueue(redis_url, queue_name="test_queue", maxsize=5)
    queue.clear()
    return queue


def test_init(redis_url):
    queue = RedisAsyncQueue(redis_url, queue_name="test_queue")
    assert queue.queue_name == "test_queue"
    assert queue.capacity == 0


def test_uses_a_redis_specific_default_worker():
    queue = RedisAsyncQueue("redis://localhost:6379/0")

    assert queue.resolve_worker_class("tasks") is RedisAsyncQueueWorker


def test_rejects_the_generic_worker_for_a_redis_queue(redis_url):
    queue = RedisAsyncQueue(redis_url, queue_name="test-worker-type")

    async def handle(entry):
        return entry.payload

    with pytest.raises(TypeError, match="requires a redis worker"):
        AsyncQueueWorker({"tasks": queue}, {"tasks": handle})


def test_rejects_a_generic_worker_override_for_a_redis_queue(redis_url):
    queue = RedisAsyncQueue(redis_url, queue_name="test-worker-override")
    queue.worker_class = AsyncQueueWorker

    with pytest.raises(InvalidQueueBackendError, match="requires a redis worker"):
        queue.resolve_worker_class("tasks")


def test_rejects_a_spoofed_redis_worker_override(redis_url):
    class SpoofedRedisWorker(AsyncQueueWorker):
        provider_kind = "redis"

    queue = RedisAsyncQueue(redis_url, queue_name="test-worker-override")
    queue.worker_class = SpoofedRedisWorker

    with pytest.raises(InvalidQueueBackendError, match="not compatible"):
        queue.resolve_worker_class("tasks")


def test_redis_worker_claims_through_its_provider(redis_url, mocker):
    async def exercise():
        queue = RedisAsyncQueue(redis_url, queue_name="test-worker-provider")

        async def handle(entry):
            return entry.payload

        worker = RedisAsyncQueueWorker({"tasks": queue}, {"tasks": handle})
        entry = QueueEntry.create(queue="tasks", payload="work")
        mocker.patch.object(queue._provider, "arecover", AsyncMock(return_value=(0, 0)))
        mocker.patch.object(queue._provider, "aclaim", AsyncMock(return_value=entry))
        mocker.patch.object(queue._provider, "arenew", AsyncMock(return_value=True))

        assert await worker._next_entry(queue) == (
            entry,
            worker.resolve_budget(queue, entry) + worker._cancellation_grace_period,
        )
        queue._provider.aclaim.assert_awaited_once_with(
            worker._worker_id, queue.default_claim_lease_seconds
        )
        assert not hasattr(queue, "_aclaim")
        await queue.aclose()

    asyncio.run(exercise())


def test_capacity(redis_queue):
    assert redis_queue.capacity == 5


def test_add_overflow(redis_queue):
    redis_queue.add("item1", "item2", "item3", "item4", "item5")
    with pytest.raises(QueueFullException):
        redis_queue.add("item6")


def test_fifo_order(redis_queue):
    redis_queue.add("item1", "item2", "item3")
    assert redis_queue.get() == "item1"
    assert redis_queue.get() == "item2"
    assert redis_queue.get() == "item3"


def test_fifo_with_one_item(redis_queue):
    redis_queue.add("only_item")
    assert redis_queue.get() == "only_item"
    with pytest.raises(QueueEmptyException):
        redis_queue.get()


def test_get_empty(redis_queue):
    with pytest.raises(QueueEmptyException):
        redis_queue.get()


def test_peek(redis_queue):
    redis_queue.add("item1")
    item = redis_queue.peek()
    assert item == "item1"


def test_peek_empty(redis_queue):
    with pytest.raises(QueueEmptyException):
        redis_queue.peek()


def test_size(redis_queue):
    redis_queue.add("item1", "item2")
    size = redis_queue.size()
    assert size == 2


def test_decode_returns_text_from_a_decoding_url(redis_url):
    """A decoding URL yields text rather than bytes."""
    queue = RedisAsyncQueue(
        f"{redis_url}?decode_responses=true", queue_name="test-decoding"
    )
    queue.clear()

    queue.add("item1")

    assert queue.get() == "item1"


def test_decode_rejects_a_value_that_is_neither_text_nor_bytes():
    with pytest.raises(QueueEncodingException, match="not int"):
        QueueProviderRedis.decode(12345, "utf-8")


def test_get_reports_empty_when_another_consumer_wins_the_race(redis_queue, mocker):
    """size() and pop() are not atomic; a None pop means the queue drained."""

    async def exercise():
        await redis_queue.aadd("item1")
        mocker.patch.object(
            redis_queue._provider._async_redis(), "lpop", AsyncMock(return_value=None)
        )
        with pytest.raises(QueueEmptyException):
            await redis_queue.aget()
        await redis_queue.aclose()

    asyncio.run(exercise())


def test_poll_reports_empty_when_the_blocking_pop_returns_nothing(redis_queue, mocker):
    async def exercise():
        mocker.patch.object(
            redis_queue._provider._async_redis(), "blpop", AsyncMock(return_value=None)
        )
        with pytest.raises(QueueEmptyException):
            await redis_queue.apoll()
        await redis_queue.aclose()

    asyncio.run(exercise())


def test_poll_does_not_accept_priority_timeout_arguments(redis_queue):
    with pytest.raises(TypeError):
        redis_queue.poll(timeout=1)
