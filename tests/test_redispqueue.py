import pytest
from django_queue.backends import RedisPriorityQueue, QueueFullException, QueueEmptyException


@pytest.fixture
def redis_priority_queue(redis_client):
    """
    Fixture to set up and clean up a RedisPriorityQueue instance.
    """
    queue = RedisPriorityQueue(redis_client, queue_name="test_priority_queue", maxsize=5)
    queue.clear()
    return queue


def test_init(redis_client):
    """
    Test initialisation of RedisPriorityQueue.
    """
    queue = RedisPriorityQueue(redis_client, queue_name="test_priority_queue")
    assert queue._redis is not None
    assert queue._redis.ping() is True
    assert queue._queue_name == "test_priority_queue"
    assert queue._maxsize == 0  # Unlimited size by default


def test_capacity(redis_priority_queue):
    """
    Test the capacity of the priority queue.
    """
    assert redis_priority_queue.capacity() == 5


def test_add(redis_priority_queue):
    """
    Test adding items with priority.
    """
    redis_priority_queue.add((10, "item1"), (-10, "item2"), (0, "item3"))
    assert redis_priority_queue.size() == 3


def test_add_overflow(redis_priority_queue):
    """
    Test adding more items than the maximum size should raise QueueFullException.
    """
    redis_priority_queue.add((10, "item1"), (20, "item2"), (30, "item3"), (40, "item4"), (50, "item5"))
    with pytest.raises(QueueFullException):
        redis_priority_queue.add((60, "item6"))


def test_get_maintains_priority_order(redis_priority_queue):
    """
    Test `get()` retrieves items in order of priority (highest first).
    """
    redis_priority_queue.add((-100, "low_priority"), (0, "medium_priority"), (100, "high_priority"))
    assert redis_priority_queue.get() == "high_priority"
    assert redis_priority_queue.get() == "medium_priority"
    assert redis_priority_queue.get() == "low_priority"


def test_get_empty(redis_priority_queue):
    """
    Test `get()` on an empty queue raises QueueEmptyException.
    """
    with pytest.raises(QueueEmptyException):
        redis_priority_queue.get()


def test_poll_blocking(redis_priority_queue, mocker):
    """
    Test `poll()` blocks if the queue is empty and retrieves the next added item.
    """
    # Mock Redis' blpop response
    mocker.patch.object(
        redis_priority_queue._redis, "blpop", return_value=(redis_priority_queue._queue_name, b"blocked_item")
    )
    redis_priority_queue.add((10, "item1"))
    assert redis_priority_queue.poll(timeout=5) == "item1"  # Retrieve existing item first
    assert redis_priority_queue.poll(timeout=5) == "blocked_item"  # Block and retrieve added item


def test_poll_timeout(redis_priority_queue):
    """
    Test `poll()` times out gracefully if the queue remains empty.
    """
    with pytest.raises(QueueEmptyException):
        redis_priority_queue.poll(timeout=0)


def test_peek(redis_priority_queue):
    """
    Test `peek()` retrieves the highest-priority item without removing it.
    """
    redis_priority_queue.add((10, "item1"), (50, "item2"), (30, "item3"))
    assert redis_priority_queue.peek() == "item2"  # Highest priority (50)
    assert redis_priority_queue.size() == 3  # Size doesn't decrement


def test_peek_empty(redis_priority_queue):
    """
    Test `peek()` on an empty queue raises QueueEmptyException.
    """
    with pytest.raises(QueueEmptyException):
        redis_priority_queue.peek()


def test_size(redis_priority_queue):
    """
    Test the size of the queue.
    """
    redis_priority_queue.add((10, "item1"), (20, "item2"))
    assert redis_priority_queue.size() == 2
    redis_priority_queue.get()
    assert redis_priority_queue.size() == 1


def test_clear(redis_priority_queue):
    """
    Test clearing the queue.
    """
    redis_priority_queue.add((10, "item1"), (20, "item2"))
    redis_priority_queue.clear()
    assert redis_priority_queue.size() == 0


def test_priority_handling(redis_priority_queue):
    """
    Test that priorities are handled correctly, even with duplicates.
    """
    redis_priority_queue.add((5, "item0"), (10, "item1"), (10, "item2"), (20, "item3"))
    assert redis_priority_queue.get() == "item3"  # Highest priority (20)
    assert redis_priority_queue.get() == "item2"  # Last entered with priority 10
    assert redis_priority_queue.get() == "item1"  # Prev entered with priority 10
    assert redis_priority_queue.get() == "item0"  # Prev entered with priority 5


def test_negative_priority(redis_priority_queue):
    """
    Test handling of negative priority values.
    """
    redis_priority_queue.add((-50, "negative_priority"), (100, "high_priority"), (0, "neutral_priority"))
    assert redis_priority_queue.get() == "high_priority"  # Highest priority first
    assert redis_priority_queue.get() == "neutral_priority"  # Neutral priority
    assert redis_priority_queue.get() == "negative_priority"  # Lowest priority
