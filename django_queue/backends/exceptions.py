from django.core.exceptions import ImproperlyConfigured


class QueueException(Exception):
    pass


class InvalidQueueBackendError(ImproperlyConfigured, QueueException):
    pass


class QueueFullException(QueueException):
    pass


class QueueEmptyException(QueueException):
    pass


class QueueClaimConflictError(QueueException):
    """A pending entry already has a claim."""

    def __init__(self, entry_id):
        self.entry_id = entry_id
        super().__init__(f"Queue entry {entry_id} is already claimed")


class QueueReliableDeliveryUnsupportedError(QueueException):
    """The queue cannot provide claim-based reliable delivery."""


class QueueEntryMissingError(QueueException):
    """A claimed entry's record is no longer available."""

    def __init__(self, entry_id):
        self.entry_id = entry_id
        super().__init__(f"Queue entry {entry_id} is missing")


class QueueEncodingException(QueueException):
    pass


class QueueValueError(QueueException, ValueError):
    pass
