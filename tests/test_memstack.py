import pytest
from django_queue.backends import MemoryStack, QueueFullException, QueueEmptyException


@pytest.fixture
def empty_stack():
    return MemoryStack()


@pytest.fixture
def filled_stack():
    stack = MemoryStack()
    for i in range(3):
        stack.add(i)
    return stack


def test_init_default():
    stack = MemoryStack()
    assert stack.size() == 0
    assert stack.stack


def test_capacity():
    maxsize = 10
    stack = MemoryStack(maxsize=maxsize)
    assert stack.capacity == maxsize

    maxsize = 0
    stack = MemoryStack(maxsize=maxsize)
    assert stack.capacity == maxsize

    maxsize = 100
    stack = MemoryStack(maxsize=maxsize)
    assert stack.capacity == maxsize


def test_init_with_maxsize():
    stack = MemoryStack(maxsize=5)
    assert stack.size() == 0
    assert stack.capacity == 5


def test_add_item(empty_stack):
    empty_stack.add(1)
    assert empty_stack.size() == 1


def test_get_item(filled_stack):
    item = filled_stack.get()
    assert item == 2
    assert filled_stack.size() == 2


def test_peek_item(filled_stack):
    item = filled_stack.peek()
    assert item == 0
    assert filled_stack.size() == 3


def test_peek_empty_stack(empty_stack):
    with pytest.raises(QueueEmptyException):
        empty_stack.peek()


def test_size(filled_stack):
    assert filled_stack.size() == 3


def test_add_and_get_multiple_items():
    stack = MemoryStack()
    items = [1, 2, 3, 4, 5]
    stack.add(*items)

    assert stack.size() == 5

    # sourcery skip: no-loop-in-tests
    for expected_item in reversed(items):
        assert stack.get() == expected_item

    assert stack.size() == 0


def test_add_to_full_stack():
    stack = MemoryStack(maxsize=2)
    stack.add(1)
    stack.add(2)
    with pytest.raises(QueueFullException):
        stack.add(3)


def test_get_from_empty_stack(empty_stack):
    with pytest.raises(QueueEmptyException):
        empty_stack.get()


def test_peek_peek_on_empty_stack(empty_stack):
    with pytest.raises(QueueEmptyException):
        assert empty_stack.peek() is None


def test_peek_does_not_remove_item(filled_stack):
    initial_size = filled_stack.size()
    # sourcery skip: no-loop-in-tests
    for _ in range(3):
        filled_stack.peek()
    assert filled_stack.size() == initial_size


def test_lifo_order():
    stack = MemoryStack(stack=True)
    items = ["a", "b", "c"]
    stack.add(*items)

    # sourcery skip: no-loop-in-tests
    for expected_item in reversed(items):
        assert stack.get() == expected_item
