import pytest
from django_queue.backends import RedisQueue, QueueFullException, QueueEmptyException


@pytest.fixture
def redis_queue(redis_client):
    queue = RedisQueue(redis_client, queue_name="test_queue", maxsize=5)
    queue.clear()
    return queue


def test_init(redis_client):
    queue = RedisQueue(redis_client, queue_name="test_queue")
    assert queue._redis is not None
    assert queue._redis.ping() is True
    assert queue._queue_name == "test_queue"
    assert queue._maxsize == 0


def test_capacity(redis_queue):
    assert redis_queue.capacity() == 5


def test_add(redis_queue):
    redis_queue.add("item1", "item2")
    assert redis_queue.size() == 2


def test_add_overflow(redis_queue):
    redis_queue.add("item1", "item2", "item3", "item4", "item5")
    with pytest.raises(QueueFullException):
        redis_queue.add("item6")


def test_get(redis_queue):
    redis_queue.add("item1")
    item = redis_queue.get()
    assert item == "item1"


def test_lifo_order(redis_queue):
    redis_queue.add("item1", "item2", "item3")
    assert redis_queue.get() == "item3"
    assert redis_queue.get() == "item2"
    assert redis_queue.get() == "item1"


def test_lifo_edge_case_empty_queue(redis_queue):
    with pytest.raises(QueueEmptyException):
        redis_queue.get()


def test_lifo_with_one_item(redis_queue):
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
