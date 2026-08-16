import asyncio

import pytest

from django_queue.backends import (
    MemoryAsyncQueue,
    MemoryAsyncStack,
    QueueEmptyException,
    QueueFullException,
)
from django_queue.backends.memory import MemoryAsyncQueueWorker
from django_queue.backends.redis import RedisAsyncQueueWorker


@pytest.fixture(params=[MemoryAsyncQueue, MemoryAsyncStack], ids=["fifo", "lifo"])
def queue_type(request):
    return request.param


@pytest.fixture
def queue(queue_type):
    return queue_type()


class TestMemoryAsyncQueueConfiguration:
    def test_uses_the_default_task_worker(self):
        queue = MemoryAsyncQueue()

        assert queue.resolve_worker("tasks") is MemoryAsyncQueueWorker

    def test_rejects_a_redis_worker_for_a_memory_queue(self):
        queue = MemoryAsyncQueue(queue_name="tasks")

        async def handle(entry):
            return entry.payload

        with pytest.raises(TypeError, match="requires a generic worker"):
            RedisAsyncQueueWorker({"tasks": queue}, {"tasks": handle})

    def test_rejects_a_relabelled_redis_worker_for_a_memory_queue(self):
        class RelabelledRedisWorker(RedisAsyncQueueWorker):
            provider_kind = "generic"

        queue = MemoryAsyncQueue(queue_name="tasks")

        async def handle(entry):
            return entry.payload

        with pytest.raises(TypeError, match="not compatible"):
            RelabelledRedisWorker({"tasks": queue}, {"tasks": handle})

    def test_initializes_empty_with_its_declared_stack_mode(self, queue, queue_type):
        assert queue.size() == 0
        assert queue.stack is (queue_type is MemoryAsyncStack)

    @pytest.mark.parametrize("maxsize", [0, 5, 10, 100])
    def test_honours_configured_capacity(self, queue_type, maxsize):
        queue = queue_type(maxsize=maxsize)

        assert queue.capacity == maxsize


class TestMemoryAsyncQueueOperations:
    def test_poll_does_not_accept_priority_timeout_arguments(self, queue):
        with pytest.raises(TypeError):
            queue.poll(timeout=1)

    def test_awaiting_poll_does_not_stall_the_event_loop(self, queue):
        async def exercise():
            yielded = asyncio.Event()

            async def confirm_loop_can_run():
                await asyncio.sleep(0)
                yielded.set()

            poll_task = asyncio.create_task(queue.apoll())
            confirmation = asyncio.create_task(confirm_loop_can_run())
            await asyncio.wait_for(yielded.wait(), timeout=0.1)
            await queue.aadd("work")
            assert await asyncio.wait_for(poll_task, timeout=0.1) == "work"
            await confirmation

        asyncio.run(exercise())

    def test_cancelling_an_awaited_poll_allows_the_event_loop_to_close(self, queue):
        async def exercise():
            poll_task = asyncio.create_task(queue.apoll())
            await asyncio.sleep(0)
            poll_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await poll_task

        asyncio.run(exercise())

    def test_adds_and_removes_an_item(self, queue):
        queue.add("item")

        assert queue.get() == "item"
        assert queue.size() == 0

    def test_raises_when_getting_or_peeking_an_empty_queue(self, queue):
        with pytest.raises(QueueEmptyException):
            queue.get()
        with pytest.raises(QueueEmptyException):
            queue.peek()

    def test_rejects_items_beyond_capacity(self, queue_type):
        queue = queue_type(maxsize=2)
        queue.add("first", "second")

        with pytest.raises(QueueFullException):
            queue.add("third")

    def test_peeking_preserves_the_queue(self, queue):
        queue.add(0, 1, 2)

        expected = 2 if queue.stack else 0
        assert queue.peek() == expected
        assert queue.peek() == expected

        assert queue.size() == 3

    def test_clear_removes_all_raw_items(self, queue):
        queue.add("first", "second")

        queue.clear()

        assert queue.size() == 0

    @pytest.mark.parametrize(
        ("queue_type", "expected"),
        [(MemoryAsyncQueue, ["a", "b", "c"]), (MemoryAsyncStack, ["c", "b", "a"])],
        ids=["fifo", "lifo"],
    )
    def test_removes_items_in_configured_order(self, queue_type, expected):
        queue = queue_type()
        queue.add("a", "b", "c")

        assert [queue.get(), queue.get(), queue.get()] == expected
