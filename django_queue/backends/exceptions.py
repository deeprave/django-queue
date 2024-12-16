class QueueException(BaseException):
    pass


class InvalidQueueBackendError(QueueException):
    pass


class QueueFullException(QueueException):
    pass


class QueueEmptyException(QueueException):
    pass


class QueueEncodingException(QueueException):
    pass


class QueueValueError(QueueException, ValueError):
    pass
