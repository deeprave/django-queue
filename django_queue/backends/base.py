from abc import ABC, abstractmethod


class BaseQueue(ABC):
    @property
    def stack(self):
        return False

    @abstractmethod
    def capacity(self):
        raise NotImplementedError("capacity")

    @abstractmethod
    def add(self, *items):
        raise NotImplementedError("add")

    @abstractmethod
    def get(self):
        raise NotImplementedError("get")

    @abstractmethod
    def poll(self):
        raise NotImplementedError("poll")

    @abstractmethod
    def peek(self):
        raise NotImplementedError("peek")

    @abstractmethod
    def size(self):
        raise NotImplementedError("size")

    def is_empty(self):
        return self.size() == 0

    def clear(self):
        pass

    def close(self):
        pass

    def __len__(self):
        return self.size()

    def __bool__(self):
        return not self.is_empty()
