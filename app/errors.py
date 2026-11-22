"""Markers that let domain errors carry an HTTP meaning without importing FastAPI."""


class NotFound(Exception):
    """The resource does not exist for the caller's owner."""


class Forbidden(Exception):
    """The caller is authenticated but its role does not allow the operation."""


class InvalidInput(Exception):
    """The request parsed but is semantically invalid."""


def http_status(exc: Exception, default: int = 409) -> int:
    for marker, status in ((NotFound, 404), (Forbidden, 403), (InvalidInput, 422)):
        if isinstance(exc, marker):
            return status
    return default
