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
from typing import Any, List, Optional
from urllib import error as _urlerror
from urllib import parse as _urlparse
from urllib import request as _urlrequest

from .exceptions import (
    APIError,
    AuthenticationError,
    KorelyError,
    NamespaceForbiddenError,
    NotFoundError,
    QuotaExceededError,
    StaleWriteError,
)
from .models import (
    AgentDeleteReceipt,
    AgentsPage,
    BatchJob,
    BatchMemory,
    BulkReceipt,
    Context,
    DeleteReceipt,
    Fact,
    FactList,
    Memory,
    MemoryHistory,
    MemoryPage,
    Profile,
    SearchHit,
    UsersPage,
)

__version__ = "0.1.17"

# All keys are the EU region; data is stored and processed in the EU.
_REGIONS = {"eu": "https://api.korely.ai"}


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


def _key_from_config() -> Optional[str]:
    """Fall back to the key `korely init` saved.

    The CLI writes it to ~/.korely/config.json, and the docs tell people to run
    `korely init` first. Without this the documented path (init, then import the
    SDK) fails with "No API key", which is the first thing a new user hits.
    Best effort: any read problem just means we carry on and raise the normal
    missing-key error.
    """
    try:
        home = os.environ.get("KORELY_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".korely")
        with open(os.path.join(home, "config.json"), encoding="utf-8") as fh:
            key = json.load(fh).get("api_key")
        return key if isinstance(key, str) and key else None
    except Exception:
        return None


def _base_from_config() -> Optional[str]:
    """The server `korely init` was pointed at, from the same file.

    `korely init --base-url https://my-server` writes both the key and the
    address, and the CLI reads both back. This class read only the key, so the
    documented path (init, then import the SDK) picked up a self-hosted key and
    sent it to api.korely.ai. Same defect as the environment variable, in the
    one path the docstring above calls the documented one.
    """
    try:
        home = os.environ.get("KORELY_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".korely")
        with open(os.path.join(home, "config.json"), encoding="utf-8") as fh:
            base = json.load(fh).get("base_url")
        return base if isinstance(base, str) and base else None
    except Exception:
        return None


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
    >>> korely.add("User prefers TypeScript", agent_id="coding-assistant")
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        region: str = "eu",
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        ca_file: Optional[str] = None,
        verify: bool = True,
    ):
        """`ca_file`: a PEM file with the certificate authority to trust for
        https (argument, else KORELY_CA_FILE), for a server whose certificate
        a private CA signed: Caddy's local CA on a laptop install, a company
        CA. Needed on the Python 3.9 macOS ships with, whose LibreSSL does not
        read SSL_CERT_FILE. `verify=False` turns certificate checks off: only
        as an argument, never from the environment, because it lets anyone on
        the path read the key and the memories."""
        self.api_key = api_key or os.environ.get("KORELY_API_KEY") or _key_from_config()
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
            or os.environ.get("KORELY_BASE_URL")
            or _base_from_config()
            or _REGIONS.get(region)
            or _REGIONS["eu"]
        ).rstrip("/")
        _refuse_a_mismatched_pair(self.api_key, self.base_url)
        self.timeout = timeout
        self._ssl_context = _ssl_context(ca_file or os.environ.get("KORELY_CA_FILE") or None, verify)

    # ── low-level transport (the one seam tests override) ──────────────────
    def _send(self, method: str, path: str, *, params: Optional[dict] = None,
              json_body: Optional[Any] = None) -> "tuple[int, dict]":
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
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Accept": "application/json",
            "User-Agent": "korely-memory-python/" + __version__,
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = _urlrequest.Request(url, data=data, method=method, headers=headers)
        try:
            # The TLS context only when one was asked for: by default the call
            # is the one it always was, with Python's own trust store.
            extra = {"context": self._ssl_context} if self._ssl_context is not None else {}
            with _urlrequest.urlopen(req, timeout=self.timeout, **extra) as resp:
                raw = resp.read()
                status = getattr(resp, "status", resp.getcode())
        except _urlerror.HTTPError as e:
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
            return e.code, parsed
        except _urlerror.URLError as e:
            raise KorelyError(
                "Connection error: " + str(e.reason) + _sni_hint(self.base_url, e.reason)
            )
        except (OSError, _httpclient.HTTPException) as e:
            raise KorelyError(
                f"Connection error: {type(e).__name__}: {e} "
                f"({method} {path}, timeout {self.timeout}s)"
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

    def _call(self, method: str, path: str, *, params: Optional[dict] = None,
              json_body: Optional[Any] = None) -> dict:
        status, body = self._send(method, path, params=params, json_body=json_body)
        if status >= 400:
            self._raise(status, body if isinstance(body, dict) else {})
        return body if isinstance(body, dict) else {}

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
    def _raise(status: int, body: dict) -> None:
        # `_retry_after` is the transport's note of the Retry-After header, not
        # something the server said. The exception's `body` is documented as
        # the server's response verbatim, so it leaves here.
        server_body = {k: v for k, v in body.items() if k != "_retry_after"}
        code, msg = Korely._error_fields(status, body)
        if status == 401:
            raise AuthenticationError(msg, status=status, code=code, body=server_body)
        if status == 403:
            raise NamespaceForbiddenError(msg, status=status, code=code, body=server_body)
        if status == 404:
            raise NotFoundError(msg, status=status, code=code, body=server_body)
        if status == 409:
            raise StaleWriteError(msg, status=status, code=code, body=server_body)
        if status == 429:
            ra = _retry_after_seconds(body.get("_retry_after") or body.get("retry_after"))
            raise QuotaExceededError(msg, status=status, code=code, retry_after=ra,
                                     body=server_body)
        raise APIError(msg, status=status, code=code, body=server_body)

    # ── memories ───────────────────────────────────────────────────────────
    def add(self, content: "str | list", *, agent_id: Optional[str] = None,
            user_id: Optional[str] = None, run_id: Optional[str] = None,
            metadata: Optional[dict] = None,
            timestamp: Optional[str] = None) -> Memory:
        """POST /v1/memories: store a memory; returns it with extracted facts.

        ``content`` is a string, or a list of chat messages
        ``[{"role": ..., "content": ...}]`` (Mem0/Supermemory shape), which is
        joined into one text block before sending.

        Pass ``timestamp`` (ISO date/datetime) when the events happened in the
        past (backfill / migration): facts extracted inherit it as ``valid_from``,
        so ``as_of`` point-in-time queries reflect when things were true, not when
        they were ingested. Defaults to now."""
        text = _coerce_content(content)
        if not text.strip():
            raise KorelyError("content is empty: pass a non-blank string or messages with content.")
        body = self._call("POST", "/v1/memories", json_body=_clean({
            "content": text, "agent_id": agent_id, "user_id": user_id,
            "run_id": run_id, "metadata": metadata, "timestamp": timestamp,
        }))
        return Memory.from_dict(body)

    def search(self, query: str, *, user_id: Optional[str] = None,
               agent_id: Optional[str] = None, run_id: Optional[str] = None,
               metadata: Optional[dict] = None,
               limit: Optional[int] = None) -> List[SearchHit]:
        """POST /v1/memories/search: semantic search over raw memories, ranked
        by score (vector similarity to the query).

        ``run_id`` scopes to one session and ``metadata`` filters on what you
        stored at write time (keys ANDed, compared as strings). Both mirror the
        arguments ``add()`` accepts, so anything you can write you can query.

        ``limit`` defaults to the server default (15) when not passed, max 50."""
        body = self._call("POST", "/v1/memories/search", json_body=_clean({
            "query": query, "user_id": user_id, "agent_id": agent_id,
            "run_id": run_id, "metadata": metadata, "limit": limit,
        }))
        return [SearchHit.from_dict(h) for h in body.get("results", [])]

    def get_all(self, *, user_id: Optional[str] = None, agent_id: Optional[str] = None,
                run_id: Optional[str] = None,
                limit: int = 50, offset: int = 0) -> MemoryPage:
        """GET /v1/memories: list a scope, newest first.

        ``run_id`` narrows to one session. Metadata filtering lives on
        ``search()``, which carries a body and can take a dict. ``limit`` goes
        up to 200 (the server capped it at 100 before 2026-09-28, below what
        its own contract said); ``.total`` and ``offset`` walk the rest."""
        body = self._call("GET", "/v1/memories", params=_clean({
            "user_id": user_id, "agent_id": agent_id, "run_id": run_id,
            "limit": limit, "offset": offset,
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
        history, audited). For erasure use ``delete_all``."""
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
        does). Returns the purge counts, an audit id and ``slot_freed``.

        The namespace must be one ``list_agents()`` shows for this key:
        anything else raises NotFoundError, including a name that only another
        project of the account uses (the server answered 200 with zero counts
        for it before 2026-09-28, and freed nothing). The cap counts a name
        across the account, so while another project still uses the same
        ``agent_id`` its rows stay and ``slot_freed`` is False."""
        return AgentDeleteReceipt.from_dict(
            self._call("DELETE", "/v1/agents/" + _seg(agent_id, "agent_id"))
        )

    # ── facts ────────────────────────────────────────────────────────────────
    def get_facts(self, *, subject: Optional[str] = None, entity: Optional[str] = None,
                  predicate: Optional[str] = None, predicate_family: Optional[str] = None,
                  include_invalidated: bool = False, as_of: Optional[str] = None,
                  user_id: Optional[str] = None, agent_id: Optional[str] = None,
                  limit: int = 50, offset: int = 0) -> FactList:
        """GET /v1/facts: typed (subject, predicate, object) triples with
        bi-temporal validity. Pass ``as_of`` (ISO date) for a point-in-time
        query: what was true on that date.

        Returns a list of Fact. It also carries ``.total``, how many facts
        match the filters across all pages, so ``offset`` can walk them."""
        params = _clean({
            "subject": subject, "entity": entity, "predicate": predicate,
            "predicate_family": predicate_family, "as_of": as_of,
            "user_id": user_id, "agent_id": agent_id, "limit": limit, "offset": offset,
        })
        if include_invalidated:
            params["include_invalidated"] = "true"
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
        any fact ids it superseded."""
        body = self._call("POST", "/v1/facts", json_body=_clean({
            "subject": subject, "predicate": predicate, "object": object,
            "user_id": user_id, "agent_id": agent_id, "run_id": run_id,
            "subject_type": subject_type, "object_is_literal": object_is_literal,
            "confidence": confidence, "valid_from": valid_from, "tense": tense,
        }))
        return Fact.from_dict(body)

    def forget_fact(self, fact_id: str, *, at: Optional[str] = None) -> dict:
        """POST /v1/facts/{id}/forget: close a fact; it stops being current and
        stays in history.

        ``at`` is the date it STOPPED being true, not the date you noticed.
        Reading ``as_of`` a date before it still returns the fact, which is the
        reason history is kept rather than rows deleted.

        Idempotent: closing an already-closed fact changes nothing and comes
        back with ``status == "already_forgotten"``. Returns a dict with
        ``id``, ``status``, ``invalid_at`` and ``audit_id``.

        This is the half that makes a no-model write path possible. An agent
        that knows a fact is finished says so, and nothing has to infer it from
        a later sentence.
        """
        return self._call("POST", "/v1/facts/" + _seg(fact_id, "fact_id") + "/forget",
                          json_body=_clean({"at": at}))

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
                    as_of: Optional[str] = None) -> Profile:
        """GET /v1/profile: the assembled profile of one end user, the active
        typed facts known about them, the end user's own facts first, grouped by
        predicate family. ``user_id`` is required. Pass ``as_of`` (ISO date) for
        the point-in-time profile ("what we knew on 2026-03-01")."""
        body = self._call("GET", "/v1/profile", params=_clean({
            "user_id": user_id, "agent_id": agent_id, "as_of": as_of,
        }))
        return Profile.from_dict(body)

    # ── context ──────────────────────────────────────────────────────────────
    def get_context(self, query: Optional[str] = None, *, user_id: Optional[str] = None,
                    agent_id: Optional[str] = None, token_budget: int = 800) -> Context:
        """GET /v1/context: one call that assembles a prompt-ready context
        block (profile + relevant facts + memories) within a token budget.

        ``query`` by position or by name: ``get_context("...", user_id=...)``
        is how the docs write it, and it raised TypeError until 2026-09-29."""
        if not query or not str(query).strip():
            raise KorelyError("get_context needs a query.")
        body = self._call("GET", "/v1/context", params=_clean({
            "query": query, "user_id": user_id, "agent_id": agent_id,
            "token_budget": token_budget,
        }))
        return Context.from_dict(body)

    # ── processing state ─────────────────────────────────────────────────────
    def events(self, *, user_id: Optional[str] = None, status: Optional[str] = None,
               limit: int = 50) -> dict:
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
        """
        return self._call("GET", "/v1/events", params=_clean({
            "user_id": user_id, "status": status, "limit": limit,
        }))

    # ── batch ────────────────────────────────────────────────────────────────
    def batch(self, memories: "List[BatchMemory | dict]") -> BatchJob:
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
        same way. Servers older than 2026-09-28 refuse ``timestamp`` itself."""
        body = self._call("POST", "/v1/batch", json_body={"memories": list(memories)})
        return BatchJob.from_dict(body)

    def batch_status(self, job_id: str) -> BatchJob:
        """GET /v1/batch/:id: poll an import job."""
        return BatchJob.from_dict(self._call("GET", "/v1/batch/" + _seg(job_id, "job_id")))
