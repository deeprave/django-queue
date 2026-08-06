import pytest

from django_queue.backends import (
    MemoryQueue,
    MemoryStack,
    QueueEmptyException,
    QueueFullException,
)


@pytest.fixture(params=[MemoryQueue, MemoryStack], ids=["fifo", "lifo"])
def queue_type(request):
    return request.param


@pytest.fixture
def queue(queue_type):
    return queue_type()


class TestMemoryQueueConfiguration:
    def test_initializes_empty_with_its_declared_stack_mode(self, queue, queue_type):
        assert queue.size() == 0
        assert queue.stack is (queue_type is MemoryStack)

    @pytest.mark.parametrize("maxsize", [0, 5, 10, 100])
    def test_honours_configured_capacity(self, queue_type, maxsize):
        queue = queue_type(maxsize=maxsize)

        assert queue.capacity == maxsize


class TestMemoryQueueOperations:
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

        assert queue.peek() == 0
        assert queue.peek() == 0

        assert queue.size() == 3

    def test_clear_removes_all_raw_items(self, queue):
        queue.add("first", "second")

        queue.clear()

        assert queue.size() == 0

    @pytest.mark.parametrize(
        ("queue_type", "expected"),
        [(MemoryQueue, ["a", "b", "c"]), (MemoryStack, ["c", "b", "a"])],
        ids=["fifo", "lifo"],
    )
    def test_removes_items_in_configured_order(self, queue_type, expected):
        queue = queue_type()
        queue.add("a", "b", "c")

        assert [queue.get(), queue.get(), queue.get()] == expected
