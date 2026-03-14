from __future__ import annotations


class CommandError(RuntimeError):
    """Raised when the CLI cannot satisfy a request."""


class HTTPCommandError(CommandError):
    """Raised when an HTTP request completes but returns an error."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class TransportRetryUnsafeError(CommandError):
    """Raised when retrying execution could duplicate side effects."""

    def __init__(self, message: str, *, request_sent: bool) -> None:
        super().__init__(message)
        self.request_sent = request_sent
