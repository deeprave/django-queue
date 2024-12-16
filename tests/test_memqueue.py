import pytest
from django_queue.backends import MemoryQueue, QueueFullException, QueueEmptyException


@pytest.fixture
def empty_queue():
    return MemoryQueue()


@pytest.fixture
def filled_queue():
    queue = MemoryQueue()
    for i in range(3):
        queue.add(i)
    return queue


def test_init_default():
    queue = MemoryQueue()
    assert queue.size() == 0
    assert not queue.stack


def test_capacity():
    maxsize = 10
    queue = MemoryQueue(maxsize=maxsize)
    assert queue.capacity == maxsize

    maxsize = 0
    queue = MemoryQueue(maxsize=maxsize)
    assert queue.capacity == maxsize

    maxsize = 100
    queue = MemoryQueue(maxsize=maxsize)
    assert queue.capacity == maxsize


def test_init_with_maxsize():
    queue = MemoryQueue(maxsize=5)
    assert queue.size() == 0
    assert queue.capacity == 5


def test_add_item(empty_queue):
    empty_queue.add(1)
    assert empty_queue.size() == 1


def test_get_item(filled_queue):
    item = filled_queue.get()
    assert item == 0
    assert filled_queue.size() == 2


def test_peek_item(filled_queue):
    item = filled_queue.peek()
    assert item == 0
    assert filled_queue.size() == 3


def test_peek_empty_queue(empty_queue):
    with pytest.raises(QueueEmptyException):
        empty_queue.peek()


def test_size(filled_queue):
    assert filled_queue.size() == 3


def test_add_and_get_multiple_items():
    queue = MemoryQueue()
    items = [1, 2, 3, 4, 5]
    queue.add(*items)

    assert queue.size() == 5

    for expected_item in items:
        assert queue.get() == expected_item

    assert queue.size() == 0


def test_add_to_full_queue():
    queue = MemoryQueue(maxsize=2)
    queue.add(1)
    queue.add(2)
    with pytest.raises(QueueFullException):
        queue.add(3)


def test_get_from_empty_queue(empty_queue):
    with pytest.raises(QueueEmptyException):
        empty_queue.get()


def test_peek_peek_on_empty_queue(empty_queue):
    with pytest.raises(QueueEmptyException):
        assert empty_queue.peek() is None


def test_peek_does_not_remove_item(filled_queue):
    initial_size = filled_queue.size()
    for _ in range(3):
        filled_queue.peek()
    assert filled_queue.size() == initial_size


def test_fifo_order():
    queue = MemoryQueue()
    items = ["a", "b", "c"]
    queue.add(*items)

    for expected_item in items:
        assert queue.get() == expected_item
