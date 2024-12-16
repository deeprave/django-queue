import pytest
from django_queue.backends import RedisQueueJson, QueueEncodingException


@pytest.fixture
def redis_queue(redis_client):
    queue = RedisQueueJson(redis_client, queue_name="test_queue", maxsize=5)
    queue.clear()
    return queue


def test_add_valid_json(redis_queue):
    item = {"key": "value"}
    redis_queue.add(item)
    assert redis_queue.size() == 1


def test_get_valid_json(redis_queue):
    item = {"key": "value"}
    redis_queue.add(item)
    retrieved_item = redis_queue.get()
    assert retrieved_item == item


def test_poll_valid_json(redis_queue):
    item = {"key": "value"}
    redis_queue.add(item)
    retrieved_item = redis_queue.poll()
    assert retrieved_item == item
    assert redis_queue.size() == 0


def test_peek_valid_json(redis_queue):
    item = {"key": "value"}
    redis_queue.add(item)
    retrieved_item = redis_queue.peek()
    assert retrieved_item == item
    assert redis_queue.size() == 1


def test_add_none_item(redis_queue):
    redis_queue.add(None)  # noqa
    assert redis_queue.size() == 0


def test_add_invalid_json(redis_queue):
    with pytest.raises(QueueEncodingException):
        redis_queue.add({1, 2, 3})  # noqa Sets cannot be JSON encoded


def test_add_str(redis_queue):
    string = "hello world"
    # a string is handled as valid JSON
    redis_queue.add(string)
    assert redis_queue.size() == 1
    assert redis_queue.get() == string
