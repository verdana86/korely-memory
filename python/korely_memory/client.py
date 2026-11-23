"""The Korely client. A thin, dependency-free HTTP wrapper: every method maps
1:1 onto a REST endpoint (see /agents/docs/surfaces/sdk). All the intelligence
(embeddings, entity and typed-fact extraction, contradiction checking) runs
server-side, so this stays a small client over stdlib urllib."""
from __future__ import annotations

import http.client as _httpclient
import json
import math
import os
import re
import time
import uuid
from datetime import date, datetime, timezone
from typing import Any, Iterator, List, Optional
from urllib import error as _urlerror
from urllib import parse as _urlparse
from urllib import request as _urlrequest

from .exceptions import (
    APIError,
    AuthenticationError,
    ConflictError,
    KorelyError,
    NamespaceForbiddenError,
    NotFoundError,
    QuotaExceededError,
    StaleWriteError,
    TooManyBatchesError,
)
from .models import (
    AccountDeleteReceipt,
    AgentDeleteReceipt,
    AgentInitResult,
    AgentsPage,
    AuditEvent,
    AuditPage,
    BatchJob,
    BatchMemory,
    BulkReceipt,
    DeclaredName,
    Context,
    DeleteReceipt,
    EventsResponse,
    Fact,
    FactList,
    ForgetReceipt,
    Memory,
    MemoryHistory,
    MemoryPage,
    PingResponse,
    Profile,
    SearchHit,
    UsersPage,
)

__version__ = "0.2.2"

# One API today, in the EU, where the data is stored (Helsinki). The model
# that reads a memory is the project's region, set per project in the
# dashboard: Europe (gpt-oss-120b on Scaleway, Paris) or Global (Gemini, by
# Google), the default.
_REGIONS = {"eu": "https://api.korely.ai"}
# The header with which every answer to a key that expires says when
# (Korely 1.0.23 and later).
KEY_EXPIRES_HEADER = "Korely-Key-Expires-At"
# The token budget of the context block when the caller names none: the API's
# own default (DEFAULT_TOKEN_BUDGET in the server's services/context.py, 1,500
# since Korely 1.0.35). Every read of the block in this package names a budget,
# so each takes it from here: the client, the CLI, the local MCP server and the
# LangGraph helpers. Until 0.2.1 each had its own 800 and kept it after the
# server moved.
DEFAULT_TOKEN_BUDGET = 1500
# How long a write that got no answer waits before its one retry, in seconds:
# the first retry delay of the Stainless-generated SDKs (openai-python 3.58,
# _constants.py, INITIAL_RETRY_DELAY = 0.5), which also retry with the same
# Idempotency-Key. One retry only, so no backoff to grow.
WRITE_RETRY_DELAY = 0.5


def _clean(d: dict) -> dict:
    """Drop None values so we never send null params/body fields."""
    return {k: v for k, v in d.items() if v is not None}


def _seg(value: Any, what: str) -> str:
    """One id as a URL path segment, percent-encoded.

    Ids went into the path as they came. An end user id is usually somebody
    else's string (an email, a handle, a customer number), and one carrying
    `/`, `?` or `#` changed which endpoint was called: `delete_agent("bot#1")`
    sent `DELETE /v1/agents/bot`, because urllib drops everything after `#`,
    and purged the namespace `bot` instead of refusing. A space made http.client
    raise before sending, outside every error this client documents.

    An empty id is refused here: `get("")` asked for `/v1/memories/`, which is
    the list endpoint behind a redirect, and came back as a Memory with nothing
    in it.
    """
    if value is not None and (isinstance(value, bool) or not isinstance(value, (str, int))):
        # Only a string, or a whole number, is an id: str() turned a dict or a
        # function into text that went out as the path (the JS client's
        # "/v1/facts/[object Object]" in the hosted product's logs, 2026-10-05).
        raise KorelyError(f"{what} must be a string id, not {type(value).__name__}.")
    s = "" if value is None else str(value)
    if not s:
        raise KorelyError(f"{what} is empty.")
    return _urlparse.quote(s, safe="")


def _moment(value: Any, what: str) -> Optional[str]:
    """``since`` / ``until`` as the ISO 8601 text the API reads.

    A string goes as written. A ``datetime`` without a time zone is sent as
    UTC, the rule the servers and this package's own date printing follow for
    a value with no offset: sent bare, the server would compare it in its
    database session's zone, whatever that is. A ``date`` is its midnight, UTC,
    so ``until=date(2026, 10, 1)`` stops where 1 October begins.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).isoformat()
    if isinstance(value, str):
        return value
    raise KorelyError(f"{what} must be an ISO 8601 string, a datetime or a date, "
                      f"not {type(value).__name__}.")


def _audit_filters(user_id: Any, action: Any, since: Any, until: Any) -> dict:
    """The filters of GET /v1/audit, checked before anything is sent.

    An empty ``user_id`` or ``action`` is refused: both servers read an empty
    filter as no filter, so ``audit(user_id=uid)`` with a ``uid`` that happened
    to be empty answered with the events of every end user. The trail is what
    an access request for ONE person is answered from.
    """
    for name, value in (("user_id", user_id), ("action", action)):
        if isinstance(value, str) and value == "":
            raise KorelyError(f"{name} is empty: the server reads an empty filter as no "
                              f"filter. Pass None to mean every {name.split('_')[0]}.")
    return {"user_id": user_id, "action": action,
            "since": _moment(since, "since"), "until": _moment(until, "until")}


def _coerce_content(content: Any) -> str:
    """add() accepts a string OR a list of chat messages
    [{"role": ..., "content": ...}] (Mem0/Supermemory shape). A message list is
    joined into one text block (``role: content`` per line) before sending,
    and the server stores and mines the resulting text. Empty or role-only
    messages are dropped (no dangling ``role:`` lines)."""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts: List[str] = []
        for m in content:
            if isinstance(m, dict):
                role = (m.get("role") or "").strip()
                body = m.get("content")
                body = "" if body is None else str(body).strip()
                if role and body:
                    parts.append(f"{role}: {body}")
                elif body:
                    parts.append(body)
                # role-only / empty message: dropped
            else:
                s = str(m).strip()
                if s:
                    parts.append(s)
        return "\n".join(parts)
    return str(content)


def config_path() -> str:
    """~/.korely/config.json, or $KORELY_CONFIG_HOME/config.json: where
    `korely init` saves a key, and where every client reads it back."""
    home = os.environ.get("KORELY_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".korely")
    return os.path.join(home, "config.json")


def _profile_name(profile: Optional[str] = None) -> str:
    """The profile a read of the config file goes to: the one named, else
    KORELY_PROFILE, else "default" (the file's top level)."""
    return profile or os.environ.get("KORELY_PROFILE") or "default"


def profile_config(profile: Optional[str] = None) -> dict:
    """The saved settings of one profile: the file's top level for the default
    profile, its `profiles[<name>]` for a named one (`korely init --profile`),
    the way the AWS CLI keeps several accounts in one file. `profile` None
    reads KORELY_PROFILE, else the default. Best effort: an unreadable file is
    an empty profile, and the caller raises its usual missing-key error."""
    try:
        with open(config_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    name = _profile_name(profile)
    if name == "default":
        return data
    named = (data.get("profiles") or {}).get(name)
    return named if isinstance(named, dict) else {}


def _from_config(field: str, profile: Optional[str] = None) -> Optional[str]:
    value = profile_config(profile).get(field)
    return value if isinstance(value, str) and value else None


def saved_identity(profile: Optional[str], api_key: Optional[str]) -> dict:
    """The ids `korely init --user-id` / `--agent-id` saved for a profile:
    whose memory a write of the `korely` command line or of `korely-mcp` is
    when the write names neither. ``{}`` when none was saved, and when
    ``api_key``, the key the write goes with, is not the key saved in that
    same profile: a key from KORELY_API_KEY, ``--api-key`` or another
    profile may be another project's or another account's, where the id
    saved for one's own use would name one of its end users.

    This class does not read it: a program says whose memory each of its
    writes is, so a file on one machine never changes where a program's
    memories go."""
    saved = profile_config(profile)
    if not api_key or saved.get("api_key") != api_key:
        return {}
    return {f: v for f, v in ((f, saved.get(f)) for f in ("user_id", "agent_id"))
            if isinstance(v, str) and v}


def _key_from_config(profile: Optional[str] = None) -> Optional[str]:
    """Fall back to the key `korely init` saved.

    The CLI writes it to ~/.korely/config.json, and the docs tell people to run
    `korely init` first. Without this the documented path (init, then import the
    SDK) fails with "No API key", which is the first thing a new user hits.
    Best effort: any read problem just means we carry on and raise the normal
    missing-key error.
    """
    return _from_config("api_key", profile)


def _base_from_config(profile: Optional[str] = None) -> Optional[str]:
    """The server `korely init` was pointed at, from the same file.

    `korely init --base-url https://my-server` writes both the key and the
    address, and the CLI reads both back. This class read only the key, so the
    documented path (init, then import the SDK) picked up a self-hosted key and
    sent it to api.korely.ai. Same defect as the environment variable, in the
    one path the docstring above calls the documented one.
    """
    return _from_config("base_url", profile)


def _sni_hint(base_url: str, reason: object) -> str:
    """Explain the one TLS failure whose message explains nothing.

    A server named after its own IP address, `203.0.113.64.nip.io`, is what the
    install instructions suggest when a machine has no DNS name yet. It works in
    every browser. From the Python that ships with macOS it fails with
    `TLSV1_ALERT_INTERNAL_ERROR` and nothing else, on both ends: the server sees
    a handshake with no server name and hangs up, the client reports an internal
    error it did not have.

    The cause is that that Python is built against LibreSSL 2.8.3, which reads a
    name beginning with four numbers as an IP address. RFC 6066 forbids sending
    an IP address as the server name, so it sends no name at all, and a server
    holding one certificate cannot tell which one was wanted. Measured against a
    server that logs what it receives: `203.0.113.64.nip.io` arrives empty,
    `203-0-113-64.nip.io` arrives intact, and a current Python sends both.

    Nothing here can fix it. What it can do is stop the next person spending an
    afternoon on it, which is what happened to the person this was written for.
    """
    try:
        import ssl
        if not isinstance(reason, ssl.SSLError):
            return ""
        host = _urlparse.urlsplit(base_url).hostname or ""
        m = re.match(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.", host)
        if not m or any(int(g) > 255 for g in m.groups()):
            return ""
        if not ssl.OPENSSL_VERSION.startswith("LibreSSL"):
            return ""
        dashed = host.replace(".".join(m.groups()), "-".join(m.groups()), 1)
        return (
            "\n\nThis is not your server. This Python is built against "
            + ssl.OPENSSL_VERSION + ", which reads a host name starting with "
            "four numbers as an IP address and then sends no server name at all, "
            "so the server cannot tell which certificate you wanted.\n"
            "Two ways out, either is enough:\n"
            "  use the dashed name, which resolves to the same address:\n"
            "    base_url=\"https://" + dashed + "\"\n"
            "  or run this on a Python built against OpenSSL 3, which sends it "
            "correctly."
        )
    except Exception:
        return ""


# Hosts that are ours. A key we issued must not be handed to anybody else's
# machine, and a key issued by somebody else's install must not be sent to us.
_OUR_HOSTS = ("korely.ai",)


def _ssl_context(ca_file: Optional[str], verify: bool):
    """The TLS context of the requests: None (Python's default) unless a CA
    file is given or verification is turned off."""
    import ssl

    if not verify:
        import warnings

        warnings.warn(
            "Korely(verify=False): TLS certificates are not checked; anyone on the "
            "network path can read the API key and the memories.",
            stacklevel=3,
        )
        return ssl._create_unverified_context()
    if ca_file:
        if not os.path.isfile(ca_file):
            raise KorelyError(f"ca_file {ca_file!r} does not exist")
        return ssl.create_default_context(cafile=ca_file)
    return None


def _is_ours(base_url: str) -> bool:
    host = (_urlparse.urlsplit(base_url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _OUR_HOSTS)


def _refuse_a_mismatched_pair(api_key: str, base_url: str) -> None:
    """Stop before the request, not after the 401.

    A key carries where it came from in its first nine characters: `kor_live_`
    from the hosted service, `kor_self_` from an install somebody runs
    themselves. When the key says one thing and the address says another, the
    only outcomes are a refusal and a memory that travelled to reach it.

    That is the failure 0.1.11 half-solved. It made the environment variable win
    over the region default, so somebody who had set both stopped talking to us
    by accident. It did nothing for the case where only one of the two is set,
    which is the same mistake with one hand tied: a self-hosted key, no address,
    and the default takes it to api.korely.ai.

    Checked here, in the constructor, because the point is to fail before
    anything is sent. A 401 is an answer that arrives after the body.

    No override on purpose. If somebody is genuinely fronting the hosted service
    with their own domain, this refuses them and we will hear about it, which is
    a better way to learn that the case is real than shipping a switch for a
    user who may not exist.
    """
    ours = _is_ours(base_url)
    if api_key.startswith("kor_self_") and ours:
        raise KorelyError(
            "This key was issued by an install you run yourself (kor_self_), "
            f"and {base_url} is the hosted Korely service. It would be refused "
            "there, but the memory would arrive first. Point base_url at your "
            "own server, or set KORELY_BASE_URL."
        )
    if api_key.startswith("kor_live_") and not ours:
        raise KorelyError(
            "This key was issued by the hosted Korely service (kor_live_), and "
            f"{base_url} is not it. Sending it there hands a credential we "
            "issued to a machine that is not ours. Use a key minted by that "
            "install (kor_self_), or drop base_url to reach the hosted service."
        )


class _RedirectsOnlyAGet(_urlrequest.HTTPRedirectHandler):
    """urllib's redirects, for a GET or a HEAD only. urllib follows a POST
    answered 301, 302 or 303 as a GET with no body, and the answer of that
    GET came back as the POST's: ``add()`` reported a memory stored from
    another address's reply. A call that is not a GET or a HEAD is never
    redirected: the 3xx is its answer (an APIError with its status)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.get_method() not in ("GET", "HEAD"):
            raise _urlerror.HTTPError(req.full_url, code, msg, headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(req, timeout, context=None):
    """``urlopen`` with the client's redirect rule (`_RedirectsOnlyAGet`):
    the one call to the network, and the seam the tests replace. The TLS
    context only when one was asked for, as ``urlopen(context=...)``."""
    handlers = [_RedirectsOnlyAGet()]
    if context is not None:
        handlers.append(_urlrequest.HTTPSHandler(context=context))
    return _urlrequest.build_opener(*handlers).open(req, timeout=timeout)


def _origin(url: str) -> str:
    """``scheme://host[:port]`` of an address: where a redirect points, its
    path and query left out."""
    parts = _urlparse.urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


def _account_id(value: Any) -> Optional[str]:
    """``value`` as the id of an account, spaces stripped, or None: a UUID in
    its usual text form (8-4-4-4-12 hex digits), the form GET /admin/whoami
    gives ``tenant_id`` in. The server reads ``?confirm=`` with
    ``uuid.UUID``, so nothing that passes here is a boolean to any server:
    a ``tenant_id`` of ``"true"`` must never become ``?confirm=true``, which
    deletes the account on a server older than Korely 1.0.23."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        return text if str(uuid.UUID(text)) == text.lower() else None
    except ValueError:
        return None


def _idempotency_header(key: Optional[str]) -> Optional[dict]:
    """The Idempotency-Key header for a write, or none. Its value is a String
    of RFC 9651 (section 3.3.3), quoted, as draft-ietf-httpapi-idempotency-
    key-header-08 section 2.1 has it: a backslash and a quote are escaped
    (section 4.1.6). The key is what the server takes in a quoted String
    (its ``idempotency_key_invalid``): 1 to 255 characters, each printable
    ASCII, space to ``~``. Anything else is refused here, before a request
    is sent: a line break reached http.client, which raised a bare
    ValueError, not the KorelyError every other failure of a call is."""
    if key is None:
        return None
    if not (isinstance(key, str) and 1 <= len(key) <= 255
            and all(" " <= c <= "~" for c in key)):
        raise KorelyError("idempotency_key must be 1 to 255 printable ASCII characters "
                          "(space to ~), one per write: a UUID will do.")
    return {"Idempotency-Key": '"' + key.replace("\\", "\\\\").replace('"', '\\"') + '"'}


def _new_idempotency_key() -> str:
    """The Idempotency-Key of a write the caller gave none: random, made once
    before the first try and sent again on the retry (`Korely._write`)."""
    return uuid.uuid4().hex


def _retry_after_seconds(raw: Any) -> Optional[int]:
    """Retry-After in whole seconds, rounded up, or None if absent/unreadable."""
    if raw is None:
        return None
    try:
        return max(0, math.ceil(float(raw)))
    except (TypeError, ValueError):
        return None


class Korely:
    """Typed client over the Korely REST API.

    >>> korely = Korely(api_key="kor_live_...", region="eu")
    >>> korely.add("User prefers TypeScript", user_id="dana", agent_id="coding-assistant")
    """

    #: When the key this client sends ends (ISO 8601, UTC, to the second), as
    #: the last answer that said it: Korely 1.0.23 and later put
    #: ``Korely-Key-Expires-At`` on every answer to a key that expires, so a
    #: program can warn before its calls start being refused with 401. None
    #: before the first answer, for a key that never ends (the header is not
    #: sent) and from an older server. An answer without the header (a 401, a
    #: 500) leaves the value as it was: one client sends one key.
    key_expires_at: Optional[str] = None

    #: The profile of the config file whose saved key this client sends
    #: ("default" for the file's top level); None for a key given as
    #: ``api_key`` or by KORELY_API_KEY, which ``korely init`` does not write.
    #: Set by the constructor, in the order it reads the key.
    _key_profile: Optional[str] = None

    #: Whether a write that got no answer is sent once more with its key
    #: (`_write`). Set by the constructor; True on a client built without it.
    retry_writes: bool = True

    def __init__(
        self,
        api_key: Optional[str] = None,
        region: str = "eu",
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        ca_file: Optional[str] = None,
        verify: bool = True,
        client: Optional[str] = None,
        profile: Optional[str] = None,
        retry_writes: bool = True,
    ):
        """`client`: the tool writing, e.g. "claude-code" (argument, else
        KORELY_CLIENT), sent as `X-Korely-Client`: Korely Agent keeps it on
        each memory as `client`, a label that no agent cap counts. Left out,
        the header names this library, `korely-python/<version>`.

        `profile`: a key saved with `korely init --profile <name>`, read from
        the config file instead of the default one; a profile named here is a
        decision, so KORELY_API_KEY and KORELY_BASE_URL do not override it.

        `ca_file`: a PEM file with the certificate authority to trust for
        https (argument, else KORELY_CA_FILE), for a server whose certificate
        a private CA signed: Caddy's local CA on a laptop install, a company
        CA. Needed on the Python 3.9 macOS ships with, whose LibreSSL does not
        read SSL_CERT_FILE. `verify=False` turns certificate checks off: only
        as an argument, never from the environment, because it lets anyone on
        the path read the key and the memories.

        `retry_writes`: a write (`add`, `batch`) whose answer never came, a
        timeout or a connection that failed, is sent once more, after half a
        second, with the same Idempotency-Key, which Korely 1.0.27 and later
        make once. False sends every write once. Either way a write with no
        answer raises a KorelyError with code ``write_not_confirmed`` that
        names the key to send it again with."""
        if profile and not api_key and not _key_from_config(profile):
            raise KorelyError(f"No key saved for the profile {profile!r}: run "
                              f"`korely init --api-key <key> --profile {profile}`.")
        env = {} if profile else os.environ
        self.api_key = api_key or env.get("KORELY_API_KEY") or _key_from_config(profile)
        self._key_profile = (None if api_key or env.get("KORELY_API_KEY")
                             else _profile_name(profile))
        if not self.api_key:
            raise KorelyError(
                "No API key. Pass api_key='kor_live_...', set KORELY_API_KEY, "
                "or run `korely init --agent` to get a free one."
            )
        # KORELY_BASE_URL comes before the region default, and the reason is not
        # convenience. Somebody who installed Korely on their own machine sets
        # KORELY_API_KEY and KORELY_BASE_URL, writes `Korely()`, and expects to
        # be talking to their own server. Without this line they are talking to
        # ours: their key is rejected with a 401, so nothing is stored, but the
        # memory travelled in the body of the request before being refused.
        #
        # For a product sold on "your data stays on your machine", quietly
        # sending it somewhere else is the one failure that cannot be waved
        # through as a nuisance. An explicit `base_url=` still wins over the
        # environment, because an argument is a decision and a variable is a
        # setting.
        # Same order as the key above, and for the same reason: an argument is a
        # decision, a variable is a setting, a config file is what `korely init`
        # was told once. The hosted region is the last resort, not the default
        # that quietly wins.
        self.base_url = (
            base_url
            or env.get("KORELY_BASE_URL")
            or _base_from_config(profile)
            or _REGIONS.get(region)
            or _REGIONS["eu"]
        ).rstrip("/")
        _refuse_a_mismatched_pair(self.api_key, self.base_url)
        self.timeout = timeout
        self.client = (client or os.environ.get("KORELY_CLIENT") or "").strip() or None
        self._ssl_context = _ssl_context(ca_file or os.environ.get("KORELY_CA_FILE") or None, verify)
        self.retry_writes = bool(retry_writes)

    @classmethod
    def init_agent(cls, agent_caller: Optional[str] = None, *, region: str = "eu",
                   processing_region: Optional[str] = None,
                   base_url: Optional[str] = None, timeout: float = 30.0,
                   ca_file: Optional[str] = None, verify: bool = True) -> AgentInitResult:
        """POST /v1/agents/init, with no key: sign up for a key that costs nothing.

        The one call that runs without a key, because it is how one is
        obtained, so it is a class method: ``Korely.init_agent("my-app")``,
        then ``Korely(api_key=result.api_key)``. ``agent_caller`` is a
        free-form label of who signed up, kept for your reference. The answer
        carries the key, shown once (save it), when it stops working
        (``expires_at``), the trial in words (``note``: its limits until the
        account is adopted, and how to adopt it), its ``tier``, ``region``,
        ``scopes`` and ``quotas`` (the monthly numbers once adopted). The
        account it creates has no login: the key is the account, and
        ``delete_account(confirm="<the account's id>")`` closes it.

        ``processing_region`` chooses where the new project's memories are
        read by a model: ``"eu"`` (gpt-oss-120b on Scaleway, Paris) or
        ``"global"`` (Gemini, by Google), the server's default when it is left
        out; it is sent only when given (servers since 2026-10-07).

        Cloud only. The server is ``base_url``, else ``KORELY_BASE_URL``, else
        the region's (the config file is not read: it holds what an earlier
        signup saved). Refusals: 403 ``signup_disabled`` (NamespaceForbiddenError)
        when self-signup is closed; 429 ``signup_rate_limited``
        (QuotaExceededError, with ``retry_after``) past the new accounts a
        network may open in a day. The Self-hosted has no such route and
        answers 404, or 405 where it serves its dashboard: its keys come from
        its own dashboard.
        """
        if agent_caller is not None and not isinstance(agent_caller, str):
            raise KorelyError("agent_caller is a label, a string, "
                              f"not {type(agent_caller).__name__}.")
        if processing_region is not None and processing_region not in ("eu", "global"):
            raise KorelyError(f"processing_region is \"eu\" or \"global\", not {processing_region!r}.")
        k = cls._without_key(
            base_url or os.environ.get("KORELY_BASE_URL") or _REGIONS.get(region)
            or _REGIONS["eu"], timeout=timeout, ca_file=ca_file, verify=verify)
        body = k._call("POST", "/v1/agents/init",
                       json_body=_clean({"agent_caller": agent_caller,
                                         "processing_region": processing_region}))
        return AgentInitResult.from_dict(body)

    @classmethod
    def _without_key(cls, base_url: str, *, timeout: float, ca_file: Optional[str],
                     verify: bool) -> "Korely":
        """A client with no key, for the one call that runs without one. The
        constructor refuses to build it, rightly, for every other call: the
        same transport and the same reading of errors, minus the header."""
        k = cls.__new__(cls)
        k.api_key = None
        k.base_url = base_url.rstrip("/")
        k.timeout = timeout
        k._ssl_context = _ssl_context(ca_file or os.environ.get("KORELY_CA_FILE") or None, verify)
        return k

    # ── low-level transport (the one seam tests override) ──────────────────
    def _send(self, method: str, path: str, *, params: Optional[dict] = None,
              json_body: Optional[Any] = None,
              headers: Optional[dict] = None) -> "tuple[int, dict]":
        """One HTTP exchange. Returns (status, parsed body); a Retry-After
        header travels in the body under ``_retry_after``, which ``_raise``
        takes out again before the body reaches an exception.

        Every way the exchange can fail without an HTTP status becomes a
        KorelyError. Only URLError used to be caught, and that covers failures
        while connecting: a timeout while READING the answer, a connection
        reset halfway through the body, or a 200 whose body is not JSON (a
        proxy's HTML page) arrived as a bare TimeoutError, ConnectionResetError
        or JSONDecodeError. The CLI and the MCP tools catch KorelyError, so
        those printed a traceback instead of an error.
        """
        url = self.base_url + path
        if params:
            qs = _urlparse.urlencode(_clean(params), doseq=True)
            if qs:
                url += "?" + qs
        data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
        extra_headers = headers
        headers = {
            "Accept": "application/json",
            "User-Agent": "korely-memory-python/" + __version__,
        }
        # Who is writing: the tool the developer named, else this library, as
        # the JavaScript client has always said (`korely-js/<version>`).
        headers["X-Korely-Client"] = (getattr(self, "client", None)
                                      or "korely-python/" + __version__)
        if data is not None:
            headers["Content-Type"] = "application/json"
        headers.update(extra_headers or {})
        req = _urlrequest.Request(url, data=data, method=method, headers=headers)
        # No key only on the client init_agent() builds: that call is how a
        # key is obtained, and an empty `Bearer ` is not "no key" to a server.
        # The key goes to `base_url` and nowhere else: urllib copies a
        # request's headers onto the request a redirect makes, to whatever
        # host, scheme or port it names (a GET answered 302 by another host
        # sent the key there), but never its unredirected headers, which is
        # how urllib's own HTTPBasicAuthHandler sends Authorization. A GET's
        # redirect is still followed, without the key, so a server that needs
        # it answers 401; any other call is never redirected (`_open`).
        if self.api_key:
            req.add_unredirected_header("Authorization", "Bearer " + self.api_key)
        try:
            # The TLS context only when one was asked for: by default the call
            # is the one it always was, with Python's own trust store.
            extra = {"context": self._ssl_context} if self._ssl_context is not None else {}
            with _open(req, timeout=self.timeout, **extra) as resp:
                raw = resp.read()
                status = getattr(resp, "status", resp.getcode())
                self._note_the_key_end(getattr(resp, "headers", None))
        except _urlerror.HTTPError as e:
            self._note_the_key_end(e.headers)
            try:
                raw = e.read()
            except (OSError, _httpclient.HTTPException):
                raw = b""
            finally:
                e.close()
            try:
                parsed = json.loads(raw) if raw else {}
            except ValueError:
                parsed = {"message": raw.decode("utf-8", "replace")}
            if not isinstance(parsed, dict):
                parsed = {"message": str(parsed)}
            retry_after = e.headers.get("Retry-After") if e.headers else None
            if retry_after is not None:
                parsed["_retry_after"] = retry_after
            # Where a redirect pointed: the Location of a 3xx not followed,
            # or the other origin a followed GET ended at (its 401 is the
            # key left behind, and that address is the server to name). Only
            # the origin: a path or a query may hold what is not ours to
            # repeat.
            location = e.headers.get("Location") if e.headers else None
            if 300 <= e.code < 400 and location:
                parsed["_redirect"] = _origin(_urlparse.urljoin(e.filename or url, location))
            elif e.filename and _origin(e.filename) != _origin(req.full_url):
                parsed["_redirect"] = _origin(e.filename)
            return e.code, parsed
        # No answer, with the code that says so (`connection_error`): a write
        # that ends here may have been made (`_write`). A timeout is one of
        # them, as openai-python's APITimeoutError is an APIConnectionError.
        except _urlerror.URLError as e:
            raise KorelyError(
                "Connection error: " + str(e.reason) + _sni_hint(self.base_url, e.reason),
                code="connection_error",
            )
        except (OSError, _httpclient.HTTPException) as e:
            raise KorelyError(
                f"Connection error: {type(e).__name__}: {e} "
                f"({method} {path}, timeout {self.timeout}s)",
                code="connection_error",
            )
        if not raw:
            return status, {}
        try:
            return status, json.loads(raw)
        except ValueError:
            snippet = raw[:200].decode("utf-8", "replace")
            raise KorelyError(
                f"The server answered {status} to {method} {path} with a body "
                f"that is not JSON: {snippet!r}",
                status=status,
            )

    def _note_the_key_end(self, headers: Any) -> None:
        """``key_expires_at`` from an answer's headers, when they say it."""
        ends = headers.get(KEY_EXPIRES_HEADER) if headers is not None else None
        if ends:
            self.key_expires_at = ends

    def _call(self, method: str, path: str, *, params: Optional[dict] = None,
              json_body: Optional[Any] = None, headers: Optional[dict] = None) -> dict:
        body = self._call_any(method, path, params=params, json_body=json_body, headers=headers)
        return body if isinstance(body, dict) else {}

    def _call_any(self, method: str, path: str, *, params: Optional[dict] = None,
                  json_body: Optional[Any] = None, headers: Optional[dict] = None) -> Any:
        """`_call` for an answer that may be a list (GET /admin/keys).

        Only a 2xx is an answer. A 3xx reaches here when urllib does not
        follow it (any redirect of a DELETE, PATCH or PUT, a 307 or 308 of a
        POST, a 300 or 304): it was read as a success, so a DELETE /v1/account
        answered 307 by a proxy counted as a deleted account, and `korely
        delete-account` threw away the key of an account that still
        existed."""
        # A header of the call's own (the Idempotency-Key of a write) only when
        # there is one: every other exchange is the one it always was.
        extra = {"headers": headers} if headers else {}
        status, body = self._send(method, path, params=params, json_body=json_body, **extra)
        if not 200 <= status < 300:
            self._raise(status, body if isinstance(body, dict) else {}, method=method)
        return body

    def _write(self, path: str, json_body: Any, idempotency_key: Optional[str],
               how: str) -> dict:
        """POST a write that creates (`add`, `batch`) with an Idempotency-Key:
        the caller's, else one made here, the same on both tries. A try with
        no answer (``connection_error``) may have been made, so it is sent
        once more after WRITE_RETRY_DELAY, unless ``retry_writes`` is False;
        a retry answered 409 ``idempotency_key_in_use`` found the first still
        being made. Either outcome left unconfirmed raises KorelyError
        ``write_not_confirmed``, naming the key. Never a third try, never a
        call the server answered otherwise, never a read. ``how`` is how a
        caller reads to look for it, for the message."""
        key = idempotency_key if idempotency_key is not None else _new_idempotency_key()
        headers = _idempotency_header(key)
        tries = 2 if self.retry_writes else 1
        failed: List[KorelyError] = []
        for attempt in range(tries):
            if attempt:
                time.sleep(WRITE_RETRY_DELAY)
            try:
                return self._call("POST", path, json_body=json_body, headers=headers)
            except KorelyError as e:
                if not (e.code == "connection_error"
                        or (attempt and e.code == "idempotency_key_in_use")):
                    raise
                failed.append(e)
        what = ("; the retry with the same key: ".join(str(e) for e in failed) if tries > 1
                else f"{failed[0]} (sent once: retry_writes is off)")
        raise KorelyError(
            f"POST {path} was not confirmed: {what}. The write may have been made. Send it "
            f"again with the same Idempotency-Key, {key}: Korely 1.0.27 and later make it "
            f"at most once within 24 hours. Or look for it first ({how}).",
            code="write_not_confirmed",
        ) from failed[-1]

    @staticmethod
    def _detail_line(body: dict) -> Optional[str]:
        """Render FastAPI's `detail` into one line.

        An older self-hosted install answers FastAPI's own `{detail: [...]}`,
        where each entry names the exact field: on a batch load that is
        `memories.1.content` and the reason. Reading only `message` meant
        every such validation error arrived as the string "HTTP 422".
        """
        d = body.get("detail")
        if isinstance(d, str):
            return d
        if isinstance(d, list) and d:
            parts = []
            for e in d[:3]:
                if not isinstance(e, dict):
                    parts.append(str(e)); continue
                where = ".".join(str(x) for x in (e.get("loc") or []) if x != "body")
                parts.append(f"{where}: {e.get('msg', 'invalid')}" if where
                             else str(e.get("msg", "invalid")))
            more = len(d) - 3
            return "; ".join(parts) + (f" (and {more} more)" if more > 0 else "")
        return None

    @staticmethod
    def _error_fields(status: int, body: dict) -> "tuple[Optional[str], str]":
        """The ``(code, message)`` of an error answer, whichever server sent it.

        Three shapes reach this client:

        - the hosted service: ``{"code", "message"}`` (the ApiError of the
          published contract), on every 4xx and 5xx;
        - a self-hosted install from 2026-09-28 on: the same two keys next to
          FastAPI's ``detail``, which it keeps as it always sent it;
        - an older self-hosted install: ``detail`` alone, as a sentence, as a
          ``{"code", "message"}`` pair, or as the list of fields that failed
          validation.

        The top-level keys win and ``detail`` is the fallback for each of them.
        Without the fallback an older install's
        ``{"detail": {"code": "stale_write", "message": "..."}}`` arrived with
        ``code`` None and the message "HTTP 409".
        """
        def text(v: Any) -> Optional[str]:
            return v if isinstance(v, str) and v.strip() else None

        detail = body.get("detail")
        pair = detail if isinstance(detail, dict) else {}
        code = text(body.get("code")) or text(pair.get("code"))
        message = (text(body.get("message")) or text(pair.get("message"))
                   or Korely._detail_line(body) or code or ("HTTP " + str(status)))
        return code, message

    @staticmethod
    def _raise(status: int, body: dict, *, method: Optional[str] = None) -> None:
        # `_retry_after` is the transport's note of the Retry-After header, not
        # something the server said. The exception's `body` is documented as
        # the server's response verbatim, so it leaves here.
        server_body = {k: v for k, v in body.items() if k not in ("_retry_after", "_redirect")}
        code, msg = Korely._error_fields(status, server_body)
        redirect = body.get("_redirect")
        if status == 401 and redirect:
            # Only a GET is redirected, never with the key (`_send`), and
            # `_redirect` names another origin only: on the same one, the
            # address would be base_url itself.
            msg += (f" The call was redirected to {redirect}, and the key is never sent "
                    "after a redirect: if that is the server, use that address as base_url.")
        # Retry-After on every status (2026-10-06), not only on a 429: the
        # Cloud's 503 `writes_paused` sends it too, and it is the one number
        # that says when writes resume.
        ra = _retry_after_seconds(body.get("_retry_after") or body.get("retry_after"))
        if status == 401:
            raise AuthenticationError(msg, status=status, code=code, body=server_body,
                                      retry_after=ra)
        if status == 403:
            raise NamespaceForbiddenError(msg, status=status, code=code, body=server_body,
                                          retry_after=ra)
        if status == 404:
            raise NotFoundError(msg, status=status, code=code, body=server_body, retry_after=ra)
        if status == 409:
            # Only `stale_write` is a stale write (2026-10-06). Every 409 was
            # raised as StaleWriteError, so `delete_account()`'s
            # `account_has_login` read as a lost update. Both servers name
            # `stale_write` on every stale update, the self-hosted one since
            # its first release (inside `detail` on an older install, which
            # _error_fields reads), so nothing a stale write sends lands in
            # the generic branch.
            cls = StaleWriteError if code == "stale_write" else ConflictError
            raise cls(msg, status=status, code=code, body=server_body, retry_after=ra)
        if status == 429:
            # `too_many_batches` has its own class (2026-10-06): with no
            # Retry-After it was indistinguishable by class from a monthly
            # `quota_exceeded`, and it clears as soon as a batch finishes.
            cls = TooManyBatchesError if code == "too_many_batches" else QuotaExceededError
            raise cls(msg, status=status, code=code, retry_after=ra, body=server_body)
        if 300 <= status < 400:
            to = f" to {redirect}" if redirect else ""
            if method in ("GET", "HEAD"):
                # urllib follows 301, 302, 303 and 307, and 308 from Python
                # 3.11: a 308 before it, a 300 or a 304 reach here.
                msg = (f"The server answered {status} to a {method}, a redirect{to} that "
                       "urllib does not follow (it follows 301, 302, 303 and 307, and 308 "
                       "from Python 3.11): that is not the answer to the call.")
            else:
                msg = (f"The server answered {status} to a {method or 'call'}, a redirect{to} "
                       "this client does not follow: a call that is not a GET is never "
                       "redirected, so that is not its answer, and nothing says it was done.")
        raise APIError(msg, status=status, code=code, body=server_body, retry_after=ra)

    # ── the key and its account ────────────────────────────────────────────
    def ping(self) -> PingResponse:
        """GET /v1/ping: does this key work, and what may it do? Both
        products answer it, with the same shape.

        The cheapest authenticated call there is: no scope, no rate limit, no
        quota, so it is how to check a key that was just minted or rotated
        without spending anything. ``users()`` was the usual stand-in, and it
        needs ``memories:read`` and counts as a query."""
        return PingResponse.from_dict(self._call("GET", "/v1/ping"))

    def delete_account(self, *, confirm: Optional[str] = None) -> AccountDeleteReceipt:
        """DELETE /v1/account?confirm=<the account's id>: delete the account
        of this key for good, with every key, project, memory, fact and
        webhook of it; the key stops working. Cloud only, for an account made
        by ``init_agent()`` or ``korely init --agent``, which nobody signs in
        to: the key is the account, and this is how it is closed (GDPR Art.
        17).

        ``confirm`` is the account's id, typed by whoever means it, as the
        dashboard has the email typed: ``tenant_id`` of GET /admin/whoami,
        which the error below gives. So a key that leaked, or a script
        pointed at the wrong key, is not enough with a flag (Korely 1.0.23
        asks the same of the server's route). Nothing is deleted, and the
        DELETE is not sent, unless ``confirm`` is a UUID equal, spaces
        stripped, to the ``tenant_id`` that GET /admin/whoami gives this key:
        a missing, ``True``, ``"true"`` or wrong ``confirm`` is a KorelyError
        whose ``code`` is ``confirmation_required`` (the server's code for the
        same refusal) and whose message names the id to type when the key
        may read it. A key that may not read GET /admin/whoami (403, a key
        without the ``admin`` scope) deletes nothing: with no id typed, that
        same ``confirmation_required``, whose message says whoami could not
        be read; with the id typed, the server's refusal of the whoami,
        NamespaceForbiddenError (an APIError, ``status`` 403, ``code``
        ``forbidden``).

        The account is deleted only on the server's answer that says so
        (``deleted: true``); any other answer, a redirect or a 2xx without
        it, is an error, and the account may still be there. A server older
        than 1.0.23 takes only ``?confirm=true`` and refuses the id (422):
        that is an APIError whose ``code`` is ``server_too_old``, nothing is
        deleted, and the client does not fall back to the flag. Upgrade the
        server, or use korely-memory 0.1.29.

        An account with a Korely login answers 409 ``account_has_login``
        (ConflictError): a key that ended up in a log must not be able to
        delete it, so it is closed from the app (Settings, Account). The
        Self-hosted has no such route and answers 404 (NotFoundError), or 405
        where it serves its dashboard. No audit event survives: the trail is
        part of what goes."""
        return self._delete_account(confirm, typed_as='pass confirm="{}"')

    def _delete_account(self, confirm: Any, *, typed_as: str) -> AccountDeleteReceipt:
        """`delete_account()`, with the way the caller types the id
        (``typed_as``, with ``{}`` for the id): ``korely delete-account``
        says ``--confirm <id>``."""
        typed = _account_id(confirm)
        if typed is None:
            raise self._not_confirmed(
                "This deletes the account of this key for good, every key, project, memory "
                "and fact of it, and its id, typed, is the confirmation: a flag is not enough.",
                typed_as)
        account = self._account_of_this_key()
        if typed != account:
            raise KorelyError(
                f"The account of this key is {account}, and the confirmation names {typed}: "
                f"nothing was deleted. To delete account {account}, "
                + typed_as.format(account) + ".", code="confirmation_required")
        try:
            body = self._call("DELETE", "/v1/account", params={"confirm": account})
        except APIError as e:
            # Only a server before 1.0.23 refuses the id itself: it reads
            # `confirm` as a boolean (422 of validation). Sending the flag
            # instead would undo the check, so the error says what to do.
            if e.status == 422:
                raise APIError(
                    "This server is older than Korely 1.0.23: it deletes an account with "
                    "?confirm=true, and this client confirms with the account's id and does "
                    "not fall back to the flag. Nothing was deleted. Upgrade the server to "
                    "1.0.23 or later, or delete the account with korely-memory 0.1.29 "
                    "(pip install 'korely-memory==0.1.29').",
                    status=422, code="server_too_old", body=e.body) from e
            raise
        # The server's answer to a deletion is the API's AccountDeleted answer,
        # `deleted: true` and the rows removed, as Supermemory's reset answers
        # `success: true`. A 2xx without it (an
        # empty 202 of a proxy, a page) was read as a deletion.
        if body.get("deleted") is not True:
            raise KorelyError(
                "DELETE /v1/account answered without `deleted: true`, the answer that says "
                "the account is gone: it is not counted as deleted, and it may still be "
                "there (GET /admin/whoami answers with this key while it is).")
        return AccountDeleteReceipt.from_dict(body)

    def _account_of_this_key(self) -> str:
        """The id of this key's account: ``tenant_id`` of GET /admin/whoami,
        checked as an account id (`_account_id`). The errors of the call are
        raised as they come."""
        account = _account_id(self._call("GET", "/admin/whoami").get("tenant_id"))
        if account is None:
            raise KorelyError("GET /admin/whoami gave no account id (a UUID) for this key: "
                              "nothing was deleted.", code="confirmation_required")
        return account

    def _not_confirmed(self, why: str, typed_as: str) -> KorelyError:
        """The refusal of a deletion not confirmed, naming the id to type
        when this key may read it."""
        try:
            account = self._account_of_this_key()
        except KorelyError as e:
            return KorelyError(
                f"{why} Nothing was deleted. The id is tenant_id in GET /admin/whoami, which "
                f"this key could not read: {e}", code="confirmation_required")
        return KorelyError(f"{why} Nothing was deleted. To delete account {account}, "
                           + typed_as.format(account) + ".", code="confirmation_required")

    # ── audit ──────────────────────────────────────────────────────────────
    def audit(self, *, user_id: Optional[str] = None, action: Optional[str] = None,
              since: "str | datetime | date | None" = None,
              until: "str | datetime | date | None" = None,
              limit: int = 100, offset: int = 0) -> AuditPage:
        """GET /v1/audit: what this key's project did, and what its agents
        read, newest first (``ts`` descending, then the event's id, so paging
        with ``offset`` is stable). Both products; the key needs
        ``memories:read``.

        ``user_id`` keeps the events that touched one end user, the shape of
        an access or erasure request; ``action`` one kind (``read``,
        ``write``, ``erase``...; an unknown one answers an empty page);
        ``since`` and ``until`` bound ``ts``, both inclusive, as ISO 8601
        text, a ``datetime`` (UTC when it has no zone) or a ``date`` (its
        midnight, UTC). ``limit`` is 1 to 1000. To read everything, use
        ``iter_audit()``.

        Reading the trail counts against no quota and is not itself written to
        it; it does count against the rate limit. Scoped to the key's account
        and project, like every read.
        """
        body = self._call("GET", "/v1/audit", params=_clean(dict(
            _audit_filters(user_id, action, since, until), limit=limit, offset=offset)))
        return AuditPage.from_dict(body)

    def iter_audit(self, *, user_id: Optional[str] = None, action: Optional[str] = None,
                   since: "str | datetime | date | None" = None,
                   until: "str | datetime | date | None" = None,
                   page_size: int = 1000, offset: int = 0) -> Iterator[AuditEvent]:
        """Every event ``audit()`` would page through, one at a time, for an
        export: the same filters, ``page_size`` events per request (1000, the
        most the API gives), ``offset += len(page)`` until ``total``.

        Without ``until`` the walk pins it to the newest event of its first
        page. The trail grows while it is read, newest first, and every new
        event pushed the rest one place down: the next page began with events
        already returned. The pin makes the export the trail as it stood when
        it started.

        A rate limit raises QuotaExceededError mid-way, as any call does, and
        the SDK does not retry. To resume, call again with the same ``until``
        (pin it yourself: ``until=datetime.now(timezone.utc)``) and ``offset``
        the number of events already read.
        """
        for page in self._audit_pages(user_id=user_id, action=action, since=since,
                                      until=until, page_size=page_size, offset=offset):
            for event in page.events:
                yield event

    def _audit_pages(self, *, user_id, action, since, until, page_size: int,
                     offset: int) -> Iterator[AuditPage]:
        """The pages of ``iter_audit()``, one request each. AsyncKorely walks
        the same generator a page at a time, off the event loop."""
        filters = _audit_filters(user_id, action, since, until)
        pin = filters["until"] is None and offset == 0
        while True:
            page = self.audit(user_id=filters["user_id"], action=filters["action"],
                              since=filters["since"], until=filters["until"],
                              limit=page_size, offset=offset)
            if not page.events:
                return
            yield page
            if pin and page.events[0].ts:
                filters["until"], pin = page.events[0].ts, False
            offset += len(page.events)
            if offset >= page.total:
                return

    # ── memories ───────────────────────────────────────────────────────────
    def add(self, content: "str | list", *, agent_id: Optional[str] = None,
            user_id: Optional[str] = None, run_id: Optional[str] = None,
            metadata: Optional[dict] = None,
            timestamp: Optional[str] = None, kind: Optional[str] = None,
            idempotency_key: Optional[str] = None) -> Memory:
        """POST /v1/memories: store a memory; returns it with extracted facts.

        ``content`` is a string, or a list of chat messages
        ``[{"role": ..., "content": ...}]`` (Mem0/Supermemory shape), which is
        joined into one text block before sending.

        Say whose memory it is: ``user_id``, the end user it belongs to, or
        ``agent_id``, the agent writing it (or both: that agent's memory of
        that user). Korely 1.0.19 and later refuse a write with neither, and
        store nothing: a 422 (APIError, ``code == "identity_required"``) with
        the server's message. An older server accepts it. ``run_id`` alone is
        not an identity. For a program that writes for one person only, any
        stable id will do, its own name for instance.

        Pass ``timestamp`` (ISO date/datetime) when the events happened in the
        past (backfill / migration): facts extracted inherit it as ``valid_from``,
        so ``as_of`` point-in-time queries reflect when things were true, not when
        they were ingested. Defaults to now.

        ``kind``: ``"note"`` (the server's default) or ``"session"``, the log
        of a work session, which a read can leave out (``exclude_sessions``).
        Korely Agent after 0.1.41; an older server refuses it (422).

        ``idempotency_key``: the same write sent again with the same key is
        made once (Korely Agent 1.0.27 and later; an older server ignores
        it). Without one the client makes one for this write (0.2.2). A write
        whose answer never came (a timeout, a connection that failed) may
        have been made: the client sends it once more with the same key
        (``retry_writes``), and if that gets no answer either it raises
        KorelyError ``write_not_confirmed``, naming the key. Pass your own key
        to send the write again later yourself: one new key per write, the
        same one only to send that write again."""
        text = _coerce_content(content)
        if not text.strip():
            raise KorelyError("content is empty: pass a non-blank string or messages with content.")
        body = self._write("/v1/memories", _clean({
            "content": text, "agent_id": agent_id, "user_id": user_id,
            "run_id": run_id, "metadata": metadata, "timestamp": timestamp,
            "kind": kind,
        }), idempotency_key, "get_all() or search()")
        return Memory.from_dict(body)

    def search(self, query: str, *, user_id: Optional[str] = None,
               agent_id: Optional[str] = None, run_id: Optional[str] = None,
               metadata: Optional[dict] = None,
               limit: Optional[int] = None,
               min_score: Optional[float] = None,
               exclude_sessions: bool = False,
               types: Optional[List[str]] = None,
               full_text: bool = False) -> List[SearchHit]:
        """POST /v1/memories/search: semantic search over raw memories, ranked
        by score (vector similarity to the query); with ``types``, over the
        facts too, or the facts only.

        ``run_id`` scopes to one session and ``metadata`` filters on what you
        stored at write time (keys ANDed, compared as strings). Both mirror the
        arguments ``add()`` accepts, so anything you can write you can query.

        ``limit`` defaults to the server default (15) when not passed, max 50.

        ``min_score`` (0 to 1) leaves out the memories whose score is below
        it, compared as the response shows the score; None is no floor, and
        fewer than ``limit`` results can come back. 0.20 is the value measured
        on LongMemEval with EmbeddingGemma; the right one depends on your
        texts. A server older than the parameter answers 422.

        ``exclude_sessions=True`` leaves out the work sessions (memories
        written with ``kind="session"``, what a coding assistant's hooks save)
        and the facts only they state. Korely Agent after 0.1.41.

        ``types`` names what to search, each kind once: ``["memories"]`` (what
        the server searches when it is not sent), ``["facts"]``, the facts
        true now, or both, each kind ranked by its own score and the two
        rankings merged by rank (a fact's score and a memory's are not to be
        compared). With it every result says its ``type``, and a fact's
        carries ``fact``. ``run_id``, ``metadata`` and ``min_score`` narrow
        the memories only: with ``"facts"`` in ``types`` the server answers
        422.

        ``full_text=True`` also returns each result's whole text as
        ``content``; ``snippet`` stays its first 280 characters. A memory can
        hold 16,000 characters, so a page of whole memories can be large.

        ``types`` and ``full_text`` are sent only when given: Korely 1.0.13
        and later take them, an older server answers 422."""
        body = self._call("POST", "/v1/memories/search", json_body=_clean({
            "query": query, "user_id": user_id, "agent_id": agent_id,
            "run_id": run_id, "metadata": metadata, "limit": limit,
            "min_score": min_score, "exclude_sessions": exclude_sessions or None,
            "types": types,
            "full_text": full_text or None,
        }))
        return [SearchHit.from_dict(h) for h in body.get("results", [])]

    def get_all(self, *, user_id: Optional[str] = None, agent_id: Optional[str] = None,
                run_id: Optional[str] = None,
                limit: int = 50, offset: int = 0,
                exclude_sessions: bool = False) -> MemoryPage:
        """GET /v1/memories: list a scope, newest first.

        ``run_id`` narrows to one session. Metadata filtering lives on
        ``search()``, which carries a body and can take a dict. ``limit`` goes
        up to 200 (the server capped it at 100 before 2026-09-28, below what
        its own contract said); ``.total`` and ``offset`` walk the rest."""
        body = self._call("GET", "/v1/memories", params=_clean({
            "user_id": user_id, "agent_id": agent_id, "run_id": run_id,
            "limit": limit, "offset": offset,
            "exclude_sessions": "true" if exclude_sessions else None,
        }))
        return MemoryPage.from_dict(body)

    def get(self, memory_id: str) -> Memory:
        """GET /v1/memories/:id: full content, metadata, extracted facts."""
        return Memory.from_dict(
            self._call("GET", "/v1/memories/" + _seg(memory_id, "memory_id")))

    def update(self, memory_id: str, *, content: str,
               expected_updated_at: Optional[str] = None) -> Memory:
        """PATCH /v1/memories/:id: re-runs extraction. Pass
        ``expected_updated_at`` for optimistic concurrency (raises
        StaleWriteError instead of clobbering)."""
        body = self._call("PATCH", "/v1/memories/" + _seg(memory_id, "memory_id"),
                          json_body=_clean({
                              "content": content, "expected_updated_at": expected_updated_at,
                          }))
        return Memory.from_dict(body)

    def delete(self, memory_id: str) -> DeleteReceipt:
        """DELETE /v1/memories/:id: forget one memory. It drops out of every
        default read, and the facts only it asserted are invalidated (kept as
        history, audited). The facts its facts had closed are true again:
        the receipt counts them in ``facts_reopened``. For erasure use
        ``delete_all``."""
        return DeleteReceipt.from_dict(
            self._call("DELETE", "/v1/memories/" + _seg(memory_id, "memory_id")))

    def delete_all(self, *, user_id: str) -> BulkReceipt:
        """DELETE /v1/users/:user_id/memories: ERASE every memory and fact of
        one end user (GDPR Art. 17). Physical deletion, not a flag: nothing is
        readable afterwards, ``include_invalidated`` included. The audit row
        (counts, never content) survives; ``erasure`` reads ``"permanent"``.

        The receipt counts the rows in ``memories_deleted`` and
        ``facts_deleted``. ``memories_forgotten`` and ``facts_invalidated``
        carry the same numbers under their old names and are deprecated (see
        ``BulkReceipt``)."""
        return BulkReceipt.from_dict(
            self._call("DELETE", "/v1/users/" + _seg(user_id, "user_id") + "/memories")
        )

    def declare_name(self, name: str, *, user_id: str,
                     agent_id: Optional[str] = None) -> DeclaredName:
        """POST /v1/users/:user_id/names: declare that this end user is also
        called ``name`` (a full name for the id: ``declare_name("Maria Rossi",
        user_id="maria")``). The store never merges two names of a person by
        itself, since two people can share one; after this the facts about
        ``name`` in the end user's memory read as the end user's, those
        already written included. In the key's project. Declaring the same
        name again answers the same; a name already declared for somebody
        else in that memory raises ``ConflictError``."""
        return DeclaredName.from_dict(self._call(
            "POST", "/v1/users/" + _seg(user_id, "user_id") + "/names",
            json_body=_clean({"name": name, "agent_id": agent_id})))

    def history(self, memory_id: str) -> MemoryHistory:
        """GET /v1/memories/:id/history: the lifecycle timeline of a memory:
        created / updated / deleted, plus every typed fact it produced (and the
        moment each was learned or superseded)."""
        return MemoryHistory.from_dict(
            self._call("GET", "/v1/memories/" + _seg(memory_id, "memory_id") + "/history")
        )

    def users(self, *, agent_id: Optional[str] = None, limit: int = 50,
              offset: int = 0) -> UsersPage:
        """GET /v1/users: the end users you've stored data for (the distinct
        ``user_id`` namespaces), each with active memory + fact counts and
        last-active time. The default (null) namespace is omitted. Returns a
        UsersPage: iterable like a list, with ``.total`` for pagination."""
        body = self._call("GET", "/v1/users", params=_clean({
            "agent_id": agent_id, "limit": limit, "offset": offset,
        }))
        return UsersPage.from_dict(body)

    # ── agents ───────────────────────────────────────────────────────────────
    def list_agents(self, *, limit: int = 50, offset: int = 0) -> AgentsPage:
        """GET /v1/agents: the agent namespaces written under in this key's
        project (the distinct non-null ``agent_id`` values), each with active
        memory + fact counts and last-active time. The antidote to the
        agent-cap trap: when a write is rejected with ``agent_cap_exceeded``,
        call this to see which namespaces already exist and reuse one instead
        of minting a new id.

        Returns an AgentsPage: iterable like a list, with ``.total`` (this
        project's namespaces, exactly the ones this key can delete), ``.cap``
        (the plan's agent cap) and ``.used`` (the slots taken across the whole
        account, by name, which is what the 403 counts). ``used`` is above
        ``total`` when other projects of the account use names this one does
        not. A self-hosted install sets no cap and answers ``cap == 0``."""
        body = self._call("GET", "/v1/agents", params=_clean({
            "limit": limit, "offset": offset,
        }))
        return AgentsPage.from_dict(body)

    def delete_agent(self, agent_id: str) -> AgentDeleteReceipt:
        """DELETE /v1/agents/:agent_id: hard-delete an agent namespace, purging
        every memory + fact written under this ``agent_id`` in this key's
        project (soft-forgetting its data does NOT free its cap slot; this
        does). Returns the purge counts and an audit id; ``slot_freed`` is always
        None, because no Korely server sends it, and none ever has. To see
        whether a cap slot is free, read ``cap`` and ``used`` from
        ``list_agents()``.

        The namespace must be one ``list_agents()`` shows for this key:
        anything else raises NotFoundError, including a name that only another
        project of the account uses (the server answered 200 with zero counts
        for it before 2026-09-28, and freed nothing). The cap counts a name
        across the account, so while another project still uses the same
        ``agent_id`` its rows stay and the slot stays taken."""
        return AgentDeleteReceipt.from_dict(
            self._call("DELETE", "/v1/agents/" + _seg(agent_id, "agent_id"))
        )

    # ── administration (an admin key) ────────────────────────────────────────
    # Projects and keys, as the dashboard manages them (the founder, 17 Oct
    # 2026: what the dashboard does, the API and the terminal do too). The key
    # needs the `admin` scope; on the Cloud /admin answers on api.korely.ai.
    # The answers are the server's own dicts.
    def projects(self, *, include_archived: bool = False) -> list:
        """GET /admin/projects: the account's projects."""
        body = self._call("GET", "/admin/projects",
                          params=_clean({"include_archived": True if include_archived else None}))
        return list(body.get("projects") or [])

    def create_project(self, name: str, *, region: Optional[str] = None,
                       description: Optional[str] = None) -> dict:
        """POST /admin/projects. ``region``: ``eu`` or ``global`` (None: the
        install's default writer)."""
        return self._call("POST", "/admin/projects", json_body=_clean({
            "name": name, "region": region, "description": description}))

    def update_project(self, project_id: str, *, name: Optional[str] = None,
                       description: Optional[str] = None) -> dict:
        """PATCH /admin/projects/:id: a new name or description."""
        return self._call("PATCH", "/admin/projects/" + _seg(project_id, "project_id"),
                          json_body=_clean({"name": name, "description": description}))

    def archive_project(self, project_id: str) -> dict:
        """POST /admin/projects/:id/archive (refused while live keys are in it)."""
        return self._call("POST", f"/admin/projects/{_seg(project_id, 'project_id')}/archive")

    def unarchive_project(self, project_id: str) -> dict:
        return self._call("POST", f"/admin/projects/{_seg(project_id, 'project_id')}/unarchive")

    def delete_project(self, project_id: str, *, erase: bool = False) -> dict:
        """DELETE /admin/projects/:id. What it held moves to the default
        project, or with ``erase`` is physically deleted (keys still move)."""
        return self._call("DELETE", "/admin/projects/" + _seg(project_id, "project_id"),
                          params={"erase": "true"} if erase else None)

    def keys(self) -> list:
        """GET /admin/keys: the account's keys, masked (never a secret)."""
        body = self._call_any("GET", "/admin/keys")
        return list(body if isinstance(body, list) else body.get("keys") or [])

    def create_key(self, name: str, *, project_id: Optional[str] = None,
                   scopes: Optional[List[str]] = None, users: Optional[List[str]] = None,
                   expires_in_days: Any = ...) -> dict:
        """POST /admin/keys. The answer's ``key`` is the secret, shown once.
        ``users``: confine it to these end users of its project (exact ids and
        prefixes ending in ``*``); ``expires_in_days``: left out the server's
        default, None never, 0 expired now."""
        body = _clean({"name": name, "project_id": project_id, "scopes": scopes, "users": users})
        if expires_in_days is not ...:
            body["expires_in_days"] = expires_in_days
        return self._call("POST", "/admin/keys", json_body=body)

    def revoke_key(self, key_id: str) -> dict:
        return self._call("POST", f"/admin/keys/{_seg(key_id, 'key_id')}/revoke")

    def move_key(self, key_id: str, project_id: str) -> dict:
        return self._call("POST", f"/admin/keys/{_seg(key_id, 'key_id')}/move",
                          json_body={"project_id": project_id})

    # ── facts ────────────────────────────────────────────────────────────────
    def get_facts(self, *, subject: Optional[str] = None, entity: Optional[str] = None,
                  predicate: Optional[str] = None, predicate_family: Optional[str] = None,
                  include_invalidated: bool = False, as_of: Optional[str] = None,
                  user_id: Optional[str] = None, agent_id: Optional[str] = None,
                  limit: int = 50, offset: int = 0,
                  known_as_of: Optional[str] = None,
                  exclude_sessions: bool = False) -> FactList:
        """GET /v1/facts: typed (subject, predicate, object) triples with
        bi-temporal validity. Pass ``as_of`` (ISO date) for a point-in-time
        query: what was true on that date.

        ``known_as_of`` (ISO date) asks the other clock: what the store knew
        on that date. A move made in September and told in October:
        ``as_of="2026-09-20"`` returns the new address, ``known_as_of=
        "2026-09-20"`` the old one. Both together: what the store knew on
        ``known_as_of`` about ``as_of``. Each Fact then shows the end the
        store knew of on that date (``closed_known_at`` says when it learned
        it). Servers before Korely Agent 0.1.26 ignore the parameter.

        Returns a list of Fact. It also carries ``.total``, how many facts
        match the filters across all pages, so ``offset`` can walk them."""
        params = _clean({
            "subject": subject, "entity": entity, "predicate": predicate,
            "predicate_family": predicate_family, "as_of": as_of,
            "known_as_of": known_as_of,
            "user_id": user_id, "agent_id": agent_id, "limit": limit, "offset": offset,
        })
        if include_invalidated:
            params["include_invalidated"] = "true"
        if exclude_sessions:
            params["exclude_sessions"] = "true"
        body = self._call("GET", "/v1/facts", params=params)
        return FactList([Fact.from_dict(f) for f in body.get("facts", [])],
                        total=body.get("total"))

    def add_fact_triple(self, subject: str, predicate: str, object: str, *,
                        user_id: Optional[str] = None, agent_id: Optional[str] = None,
                        run_id: Optional[str] = None, subject_type: str = "unknown",
                        object_is_literal: bool = False, confidence: float = 0.9,
                        valid_from: Optional[str] = None,
                        tense: Optional[str] = None) -> Fact:
        """POST /v1/facts: write a typed (subject, predicate, object) triple
        directly, skipping extraction. The server runs the contradiction check
        and the fact is bi-temporal: pass ``valid_from`` (ISO date) for a
        historical fact. ``tense`` is ``"current"`` (the server default),
        ``"past"`` (the fact is over, and closes the open fact it restates) or
        ``"planned"``. Returns the written Fact, with ``invalidated`` listing
        any fact ids it superseded.

        ``user_id`` or ``agent_id`` says whose fact it is, as on ``add()``:
        Korely 1.0.19 and later refuse a fact with neither (422,
        ``code == "identity_required"``)."""
        body = self._call("POST", "/v1/facts", json_body=_clean({
            "subject": subject, "predicate": predicate, "object": object,
            "user_id": user_id, "agent_id": agent_id, "run_id": run_id,
            "subject_type": subject_type, "object_is_literal": object_is_literal,
            "confidence": confidence, "valid_from": valid_from, "tense": tense,
        }))
        # Whose fact it is: servers since 2026-10-07 answer it; an older one
        # left it out, and the Fact said user_id None for a fact written for an
        # end user. What was sent is what it was stored under.
        if isinstance(body, dict):
            body.setdefault("user_id", user_id)
            body.setdefault("agent_id", agent_id)
        return Fact.from_dict(body)

    def forget_fact(self, fact_id: str, *, at: Optional[str] = None) -> ForgetReceipt:
        """POST /v1/facts/{id}/forget: close a fact; it stops being current and
        stays in history.

        ``at`` is the date it STOPPED being true, not the date you noticed.
        Reading ``as_of`` a date before it still returns the fact, which is the
        reason history is kept rather than rows deleted.

        Idempotent: closing an already-closed fact changes nothing and comes
        back with ``status == "already_forgotten"``. Returns a ForgetReceipt
        (``.id``, ``.status``, ``.invalid_at``, ``.audit_id``), which is still
        the dict it was until 2026-10-06.

        This is the half that makes a no-model write path possible. An agent
        that knows a fact is finished says so, and nothing has to infer it from
        a later sentence.
        """
        return ForgetReceipt.from_dict(
            self._call("POST", "/v1/facts/" + _seg(fact_id, "fact_id") + "/forget",
                       json_body=_clean({"at": at})))

    def correct_fact(self, fact_id: str, *, subject: Optional[str] = None,
                     predicate: Optional[str] = None,
                     object: Optional[str] = None) -> Fact:
        """PATCH /v1/facts/{id}: supersede a fact with a corrected one.

        Not an edit: the old row keeps its dates and gains a pointer to the new
        one, so ``as_of`` before the correction still returns what you believed
        then. At least one of the three fields is required.

        Returns the new Fact in the write shape. Its ``invalidated`` lists
        every fact the correction superseded: the corrected one first, then
        any other that the contradiction check on the new fact closed (each
        gets its ``fact.invalidated`` webhook). It is not always one id.

        A correction that names the fact as it already stands (the same
        subject, predicate and object, still open) supersedes nothing: the
        server reconfirms the fact and returns it, same ``id``, with
        ``invalidated == []``.
        """
        body = self._call("PATCH", "/v1/facts/" + _seg(fact_id, "fact_id"),
                          json_body=_clean({
                              "subject": subject, "predicate": predicate, "object": object,
                          }))
        return Fact.from_dict(body)

    def get_profile(self, *, user_id: str, agent_id: Optional[str] = None,
                    as_of: Optional[str] = None,
                    known_as_of: Optional[str] = None,
                    exclude_sessions: bool = False) -> Profile:
        """GET /v1/profile: the assembled profile of one end user, the active
        typed facts known about them, the end user's own facts first, grouped by
        predicate family. ``user_id`` is required. Pass ``as_of`` (ISO date) for
        the point-in-time profile ("what was true on 2026-03-01"), and
        ``known_as_of`` for the profile as the store knew it then ("what we
        knew on 2026-03-01"); see ``get_facts``."""
        body = self._call("GET", "/v1/profile", params=_clean({
            "user_id": user_id, "agent_id": agent_id, "as_of": as_of,
            "known_as_of": known_as_of,
            "exclude_sessions": "true" if exclude_sessions else None,
        }))
        return Profile.from_dict(body)

    # ── context ──────────────────────────────────────────────────────────────
    def get_context(self, query: Optional[str] = None, *, user_id: Optional[str] = None,
                    agent_id: Optional[str] = None,
                    token_budget: int = DEFAULT_TOKEN_BUDGET,
                    known_as_of: Optional[str] = None,
                    exclude_sessions: bool = False) -> Context:
        """GET /v1/context: one call that assembles a prompt-ready context
        block (profile + relevant facts + memories) within a token budget:
        ``token_budget``, 50 to 8000, 1,500 by default (``DEFAULT_TOKEN_BUDGET``,
        the API's own default since Korely 1.0.35).

        ``query`` by position or by name: ``get_context("...", user_id=...)``
        is how the docs write it, and it raised TypeError until 2026-09-29.

        ``known_as_of`` (ISO date): the block as the store could have built
        it then, the facts it knew and the memories it had received, to see
        what an agent could have known when it decided. Rebuilt with today's
        ranking. Servers before Korely Agent 0.1.26 ignore it."""
        if not query or not str(query).strip():
            raise KorelyError("get_context needs a query.")
        body = self._call("GET", "/v1/context", params=_clean({
            "query": query, "user_id": user_id, "agent_id": agent_id,
            "token_budget": token_budget, "known_as_of": known_as_of,
            "exclude_sessions": "true" if exclude_sessions else None,
        }))
        return Context.from_dict(body)

    # ── processing state ─────────────────────────────────────────────────────
    def events(self, *, user_id: Optional[str] = None, status: Optional[str] = None,
               limit: int = 50) -> EventsResponse:
        """GET /v1/events: which writes have finished being processed.

        ``add()`` returns as soon as the memory is stored, then fact extraction
        runs behind it, so a read taken immediately can legitimately find no
        facts. This tells you which is which: each event carries a memory's
        ``status`` (``processing``, ``ready`` or ``error``), newest first.
        ``status=`` filters before ``limit`` is applied, so ``status="error"``
        returns the latest errors however many ready writes came after them.
        ``limit`` goes up to 200.

        ``processing`` in the answer counts every write of this key's project
        still being extracted (of ``user_id``, when given), whatever ``status``
        and ``limit`` say, so a script can wait on that one number. It does not
        see a ``batch()`` job that has not stored its memories yet: wait for
        ``batch_status()`` to finish first.

        This is the only way to learn that extraction finished. No webhook
        fires for it: the webhook events are ``memory.created``,
        ``fact.invalidated`` and ``quota.warning``. The name
        ``fact_extracted`` exists only as an event type inside ``history()``.

        Returns an EventsResponse: ``.events`` (MemoryEvent) and
        ``.processing``, and still the dict it was until 2026-10-06.
        """
        return EventsResponse.from_dict(self._call("GET", "/v1/events", params=_clean({
            "user_id": user_id, "status": status, "limit": limit,
        })))

    # ── batch ────────────────────────────────────────────────────────────────
    def batch(self, memories: "List[BatchMemory | dict]", *,
              idempotency_key: Optional[str] = None) -> BatchJob:
        """POST /v1/batch: bulk import, up to 500 memory objects, processed
        asynchronously. Each object is the body of one ``add()`` (see
        ``BatchMemory``): ``content`` and optionally ``user_id``,
        ``agent_id``, ``run_id``, ``metadata`` and ``timestamp``.

        ``timestamp`` means what it means on ``add()``: when the events
        happened. The facts extracted from the item inherit it as
        ``valid_from``, so a migration keeps its real dates; without it an item
        is dated when it is imported. A value that is not an ISO 8601 date or
        datetime refuses the whole batch before anything is queued: a 422
        (APIError, ``code == "invalid_request"``) whose message names the item,
        e.g. ``memories[3].timestamp``. Any key not listed above is refused the
        same way. Servers older than 2026-09-28 refuse ``timestamp`` itself.

        Each object says whose memory it is with ``user_id`` or ``agent_id``,
        as on ``add()``. On Korely 1.0.19 and later one object with neither
        refuses the whole batch before anything is queued: a 422 (APIError,
        ``code == "identity_required"``) whose message names it,
        ``memories[3].user_id or memories[3].agent_id is required``.

        On the Cloud a batch is also refused, before anything is stored, with
        a 429: ``quota_exceeded`` (QuotaExceededError) when its memories do not
        fit in what is left of the month, batches already queued included, and
        ``too_many_batches`` (TooManyBatchesError) while three batches are
        still being imported. The Self-hosted meters nothing and refuses
        neither.

        ``idempotency_key``, and the one retry of a batch with no answer, as
        on ``add()``."""
        body = self._write("/v1/batch", {"memories": list(memories)}, idempotency_key,
                           "get_all() once the import has run")
        return BatchJob.from_dict(body)

    def batch_status(self, job_id: str) -> BatchJob:
        """GET /v1/batch/:id: poll an import job."""
        return BatchJob.from_dict(self._call("GET", "/v1/batch/" + _seg(job_id, "job_id")))
