"""Typed exceptions. Every one subclasses ``KorelyError``, so a caller can catch
everything the SDK raises with one except.

Every error the server answers with is an ``APIError`` carrying the stable
``code`` and the human ``message`` of the REST error envelope. The common
statuses also have their own subclass, so both styles work:

    except NotFoundError:                # by class
        ...
    except APIError as err:              # by the stable error code
        if err.code == "not_found":
            ...

A ``KorelyError`` that is not an ``APIError`` never reached the server, or got
no usable answer from it: no API key, a key and address that do not belong
together, a connection or timeout error.
"""
from __future__ import annotations

from typing import Optional


class KorelyError(Exception):
    """Base for every error the SDK raises."""

    def __init__(self, message: str = "", *, status: Optional[int] = None,
                 code: Optional[str] = None, body: Optional[dict] = None):
        super().__init__(message or code or "Korely error")
        self.message = message
        self.status = status
        self.code = code
        #: The server's response body, verbatim. On a 422 from a batch load it
        #: names the row and the field; the SDK used to drop it and raise
        #: `HTTP 422` with nothing else, so on a five-hundred-row load there
        #: was no way to learn which row was bad without repeating the call
        #: with curl.
        self.body = body or {}


class APIError(KorelyError):
    """The server answered with a non-2xx status.

    ``status`` is the HTTP status, ``code`` the stable error code
    (``invalid_key``, ``not_found``, ``quota_exceeded``, ``search_unavailable``
    and so on), ``body`` the response as the server sent it. The subclasses
    below cover the statuses a caller usually handles on their own; anything
    else (422 validation, 503 unavailable, other 5xx) is a plain ``APIError``.

    The subclasses were siblings of this class in 0.1.14 and earlier, so the pattern the
    public docs teach, ``except APIError`` and branch on ``err.code``, let a
    401, a 404 or a 429 straight through.

    ``retry_after`` is the server's Retry-After header in whole seconds,
    rounded up, or None when it sent none. Two answers carry it: a rate limit
    (429 ``rate_limit_exceeded``) and the pause of the writes that need a
    model (503 ``writes_paused``, Cloud only), which lasts until 00:00 UTC.
    """

    def __init__(self, message: str = "", *, status: Optional[int] = None,
                 code: Optional[str] = None, body: Optional[dict] = None,
                 retry_after: Optional[int] = None):
        super().__init__(message, status=status, code=code, body=body)
        # On every status since 2026-10-06. Only QuotaExceededError kept it,
        # so the 503 `writes_paused` the Cloud answers when its daily model
        # budget is spent lost the one number that says when to come back,
        # although the transport had read the header.
        self.retry_after = retry_after


class AuthenticationError(APIError):
    """401: missing, malformed, or revoked API key."""


class NamespaceForbiddenError(APIError):
    """403: the key lacks a required scope, or the write would open an agent
    namespace past the plan's cap (``code == "agent_cap_exceeded"``)."""


class NotFoundError(APIError):
    """404: memory or fact id does not exist, or was forgotten."""


class ConflictError(APIError):
    """409: the request conflicts with what the server holds. ``code`` names
    the conflict:

    - ``stale_write``: ``update()`` with an ``expected_updated_at`` older than
      the record, raised as the subclass :class:`StaleWriteError`;
    - ``account_has_login``: ``delete_account()`` with the key of an account
      somebody signs in to (Cloud only);
    - ``conflict``: the Self-hosted's code for a 409 that names no other.

    Until 2026-10-06 every 409 was a ``StaleWriteError``, so a refusal that has
    nothing to do with an update (``account_has_login``) read as a lost write,
    and code written to re-read the record and retry a stale write retried a
    refusal that never changes.
    """


class StaleWriteError(ConflictError):
    """409 ``stale_write``: update() with an expected_updated_at older than the
    record. A :class:`ConflictError`, so ``except ConflictError`` catches it
    too."""


class QuotaExceededError(APIError):
    """429. Three causes, told apart by ``code``:

    - ``rate_limit_exceeded``: too many requests in a minute, hour or day. The
      server sends Retry-After, so ``retry_after`` holds the seconds to wait.
    - ``quota_exceeded``: the monthly write or query quota is used up. There is
      nothing to wait for short of the next month, so ``retry_after`` is None.
    - ``too_many_batches``: raised as the subclass :class:`TooManyBatchesError`.

    ``retry_after`` comes from :class:`APIError`, which every error the server
    answers with now carries.
    """


class TooManyBatchesError(QuotaExceededError):
    """429 ``too_many_batches`` (Cloud only): three ``batch()`` imports of the
    account are still running, and the Cloud takes no fourth until one of them
    finishes. Nothing was stored. Wait for a job to finish (``batch_status()``)
    and send the batch again. There is no Retry-After, because the wait is a
    job and not a clock, so ``retry_after`` is None.

    Its own class since 2026-10-06. It was a plain QuotaExceededError with
    ``retry_after`` None, exactly what a monthly ``quota_exceeded`` looks like,
    and the docs tell you to stop on that one, while this one clears in
    minutes.
    """
