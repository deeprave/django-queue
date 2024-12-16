import pytest
from django_queue.backends import RedisStack, QueueFullException, QueueEmptyException


@pytest.fixture
def redis_stack(redis_client):
    stack = RedisStack(redis_client, queue_name="test_stack", maxsize=5)
    stack.clear()
    return stack


def test_init(redis_client):
    stack = RedisStack(redis_client, queue_name="test_stack")
    assert stack._redis is not None
    assert stack._redis.ping() is True
    assert stack._queue_name == "test_stack"
    assert stack._maxsize == 0
    assert stack.stack


def test_capacity(redis_stack):
    assert redis_stack.capacity() == 5


def test_add(redis_stack):
    redis_stack.add("item1", "item2")
    assert redis_stack.size() == 2


def test_add_overflow(redis_stack):
    redis_stack.add("item1", "item2", "item3", "item4", "item5")
    with pytest.raises(QueueFullException):
        redis_stack.add("item6")


def test_get(redis_stack):
    redis_stack.add("item1")
    item = redis_stack.get()
    assert item == "item1"


def test_fifo_order(redis_stack):
    redis_stack.add("item1", "item2", "item3")
    assert redis_stack.get() == "item3"
    assert redis_stack.get() == "item2"
    assert redis_stack.get() == "item1"


def test_fifo_edge_case_empty_queue(redis_stack):
    with pytest.raises(QueueEmptyException):
        redis_stack.get()


def test_fifo_with_one_item(redis_stack):
    redis_stack.add("only_item")
    assert redis_stack.get() == "only_item"
    with pytest.raises(QueueEmptyException):
        redis_stack.get()


def test_get_empty(redis_stack):
    with pytest.raises(QueueEmptyException):
        redis_stack.get()


def test_peek(redis_stack):
    redis_stack.add("item1")
    item = redis_stack.peek()
    assert item == "item1"


def test_peek_empty(redis_stack):
    with pytest.raises(QueueEmptyException):
        redis_stack.peek()


def test_size(redis_stack):
    redis_stack.add("item1", "item2")
    size = redis_stack.size()
    assert size == 2
