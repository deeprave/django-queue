from django.core.exceptions import ImproperlyConfigured


class QueueException(Exception):
    pass


class InvalidQueueBackendError(ImproperlyConfigured, QueueException):
    pass


class QueueFullException(QueueException):
    pass


class QueueEmptyException(QueueException):
    pass


class QueueEncodingException(QueueException):
    pass


class QueueValueError(QueueException, ValueError):
    pass
