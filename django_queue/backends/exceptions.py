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


class QueueEntryExpiredError(QueueException):
    """A transient entry expired before it could be claimed."""

    def __init__(self, entry_id):
        self.entry_id = entry_id
        super().__init__(f"Queue entry {entry_id} expired before it could be claimed")


class QueueEntryMissingError(QueueException):
    """A claimed entry's record is no longer available."""

    def __init__(self, entry_id):
        self.entry_id = entry_id
        super().__init__(f"Queue entry {entry_id} is missing")


class QueueEntryNotFoundError(QueueException):
    """A requested retained entry does not exist."""

    def __init__(self, entry_id):
        self.entry_id = entry_id
        super().__init__(f"Queue entry {entry_id} was not found")


class QueueEncodingException(QueueException):
    pass


class QueueValueError(QueueException, ValueError):
    pass
