import pytest
from django_queue.backends import RedisPriorityQueueJson, QueueEncodingException, QueueEmptyException


@pytest.fixture
def redis_queue(redis_client) -> RedisPriorityQueueJson:
    return RedisPriorityQueueJson(redis_spec=redis_client)


def test_add_valid_json(redis_queue):
    item = (1, {"key": "value"})
    redis_queue.add(item)
    assert redis_queue.size() == 1


def test_add_valid_string(redis_queue):
    item = (1, "test_string")
    redis_queue.add(item)
    assert redis_queue.size() == 1


def test_get_valid_json(redis_queue):
    item = (1, {"key": "value"})
    redis_queue.add(item)
    fetched_item = redis_queue.get()
    assert fetched_item == {"key": "value"}
    assert redis_queue.size() == 0


def test_get_valid_string(redis_queue):
    item = (1, "test_string")
    redis_queue.add(item)
    fetched_item = redis_queue.get()
    assert fetched_item == "test_string"
    assert redis_queue.size() == 0


def test_poll_with_timeout(redis_queue):
    item = (1, {"key": "value"})
    redis_queue.add(item)
    fetched_item = redis_queue.poll(timeout=5)
    assert fetched_item == {"key": "value"}
    assert redis_queue.size() == 0


def test_peek_valid_json(redis_queue):
    item = (1, {"key": "value"})
    redis_queue.add(item)
    peeked_item = redis_queue.peek()
    assert peeked_item == {"key": "value"}
    assert redis_queue.size() == 1


def test_add_invalid_item_raise_exception(redis_queue):
    with pytest.raises(QueueEncodingException):
        redis_queue.add((1, {"invalid"}))  # noqa


def test_get_empty_queue(redis_queue):
    with pytest.raises(QueueEmptyException):
        redis_queue.get()


def test_peek_empty_queue(redis_queue):
    with pytest.raises(QueueEmptyException):
        redis_queue.peek()


def test_large_queue(redis_queue):
    values = []
    # sourcery skip: no-loop-in-tests
    for p in range(1000):
        item = 1000 - p, f"item_{p}"
        redis_queue.add(item)
        values.append(item[1])
    for value in values:
        assert redis_queue.get() == value
    with pytest.raises(QueueEmptyException):
        redis_queue.peek()
    values = []
    for p in reversed(range(1000)):
        item = p, f"item_{p}"
        redis_queue.add(item)
        values.append(item[1])
    for value in values:
        assert redis_queue.get() == value


@pytest.mark.slow
def test_poll_empty_queue(redis_queue):
    with pytest.raises(QueueEmptyException):
        redis_queue.poll(timeout=1, retries=2)
