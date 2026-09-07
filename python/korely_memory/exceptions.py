"""Typed exceptions mapping 1:1 onto the REST error codes. All subclass
``KorelyError`` so a caller can catch everything with one except."""
from __future__ import annotations

from typing import Optional


class KorelyError(Exception):
    """Base for every error the SDK raises."""

    def __init__(self, message: str = "", *, status: Optional[int] = None, code: Optional[str] = None):
        super().__init__(message or code or "Korely error")
        self.message = message
        self.status = status
        self.code = code


class AuthenticationError(KorelyError):
    """401 — missing, malformed, or revoked API key."""


class NamespaceForbiddenError(KorelyError):
    """403 — the key lacks the scope required for this call."""


class NotFoundError(KorelyError):
    """404 — memory or fact id does not exist, or was forgotten."""


class StaleWriteError(KorelyError):
    """409 — update() with an expected_updated_at older than the record."""


class QuotaExceededError(KorelyError):
    """429 — past the soft cap. Carries ``retry_after`` in seconds when the
    server sent a Retry-After header."""

    def __init__(self, message: str = "", *, status: Optional[int] = None,
                 code: Optional[str] = None, retry_after: Optional[int] = None):
        super().__init__(message, status=status, code=code)
        self.retry_after = retry_after


class APIError(KorelyError):
    """Any other non-2xx response (validation 422, server 5xx, …)."""
