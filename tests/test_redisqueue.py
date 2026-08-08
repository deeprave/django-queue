import pytest

from django_queue.backends import QueueEmptyException, QueueFullException, RedisQueue
from django_queue.backends.exceptions import QueueEncodingException
from django_queue.backends.redis.redisqueue import _decode


@pytest.fixture
def redis_queue(redis_client):
    queue = RedisQueue(redis_client, queue_name="test_queue", maxsize=5)
    queue.clear()
    return queue


def test_init(redis_client):
    queue = RedisQueue(redis_client, queue_name="test_queue")
    assert queue._redis is not None
    assert queue._redis.ping() is True
    assert queue.queue_name == "test_queue"
    assert queue._maxsize == 0


def test_capacity(redis_queue):
    assert redis_queue.capacity == 5


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


def test_fifo_order(redis_queue):
    redis_queue.add("item1", "item2", "item3")
    assert redis_queue.get() == "item1"
    assert redis_queue.get() == "item2"
    assert redis_queue.get() == "item3"


def test_fifo_edge_case_empty_queue(redis_queue):
    with pytest.raises(QueueEmptyException):
        redis_queue.get()


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


def test_decode_returns_text_from_a_decoding_client(redis_container):
    """A client built with decode_responses=True yields str, not bytes."""
    import redis

    client = redis.Redis(
        host=redis_container.get_container_host_ip(),
        port=redis_container.get_exposed_port(6379),
        decode_responses=True,
    )
    queue = RedisQueue(client, queue_name="test_decoding_client")
    queue.clear()

    queue.add("item1")

    assert queue.get() == "item1"


def test_decode_rejects_a_value_that_is_neither_text_nor_bytes():
    with pytest.raises(QueueEncodingException, match="not int"):
        _decode(12345, "utf-8")


def test_get_reports_empty_when_another_consumer_wins_the_race(redis_queue, mocker):
    """size() and pop() are not atomic; a None pop means the queue drained."""
    redis_queue.add("item1")
    mocker.patch.object(redis_queue, "pop", return_value=None)

    with pytest.raises(QueueEmptyException):
        redis_queue.get()


def test_poll_reports_empty_when_the_blocking_pop_returns_nothing(redis_queue, mocker):
    mocker.patch.object(redis_queue, "bpop", return_value=None)

    with pytest.raises(QueueEmptyException):
        redis_queue.poll()
