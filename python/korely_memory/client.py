"""The Korely client. A thin, dependency-free HTTP wrapper: every method maps
1:1 onto a REST endpoint (see /agents/docs/surfaces/sdk). All the intelligence
— embeddings, entity + typed-fact extraction, contradiction checking — runs
server-side, so this stays a small client over stdlib urllib."""
from __future__ import annotations

import json
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
    AgentScope,
    AgentsPage,
    BatchJob,
    BulkReceipt,
    Context,
    DeleteReceipt,
    Fact,
    Memory,
    MemoryHistory,
    MemoryPage,
    Profile,
    SearchHit,
    UserScope,
    UsersPage,
)

__version__ = "0.1.8"

# All keys are the EU region; data is stored and processed in the EU.
_REGIONS = {"eu": "https://api.korely.ai"}


def _clean(d: dict) -> dict:
    """Drop None values so we never send null params/body fields."""
    return {k: v for k, v in d.items() if v is not None}


def _coerce_content(content: Any) -> str:
    """add() accepts a string OR a list of chat messages
    [{"role": ..., "content": ...}] (Mem0/Supermemory shape). A message list is
    joined into one text block (``role: content`` per line) before sending —
    the server stores and mines the resulting text. Empty or role-only messages
    are dropped (no dangling ``role:`` lines)."""
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
                # role-only / empty message → dropped
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




def _sni_hint(base_url: str, reason: object) -> str:
    """Explain the one TLS failure whose message explains nothing.

    A server named after its own IP address, `2.29.27.64.nip.io`, is what the
    install instructions suggest when a machine has no DNS name yet. It works in
    every browser. From the Python that ships with macOS it fails with
    `TLSV1_ALERT_INTERNAL_ERROR` and nothing else, on both ends: the server sees
    a handshake with no server name and hangs up, the client reports an internal
    error it did not have.

    The cause is that that Python is built against LibreSSL 2.8.3, which reads a
    name beginning with four numbers as an IP address. RFC 6066 forbids sending
    an IP address as the server name, so it sends no name at all, and a server
    holding one certificate cannot tell which one was wanted. Measured against a
    server that logs what it receives: `2.29.27.64.nip.io` arrives empty,
    `2-29-27-64.nip.io` arrives intact, and a current Python sends both.

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
    ):
        self.api_key = api_key or os.environ.get("KORELY_API_KEY") or _key_from_config()
        if not self.api_key:
            raise KorelyError(
                "No API key. Pass api_key='kor_live_...', set KORELY_API_KEY, "
                "or run `korely init --agent` to get a free one."
            )
        self.base_url = (base_url or _REGIONS.get(region) or _REGIONS["eu"]).rstrip("/")
        self.timeout = timeout

    # ── low-level transport (the one seam tests override) ──────────────────
    def _send(self, method: str, path: str, *, params: Optional[dict] = None,
              json_body: Optional[Any] = None) -> "tuple[int, dict]":
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
            with _urlrequest.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                status = getattr(resp, "status", resp.getcode())
                return status, (json.loads(raw) if raw else {})
        except _urlerror.HTTPError as e:
            raw = e.read()
            try:
                parsed = json.loads(raw) if raw else {}
            except ValueError:
                parsed = {"message": raw.decode("utf-8", "replace")}
            if not isinstance(parsed, dict):
                parsed = {"message": str(parsed)}
            retry_after = e.headers.get("Retry-After") if e.headers else None
            parsed.setdefault("_retry_after", retry_after)
            return e.code, parsed
        except _urlerror.URLError as e:
            raise KorelyError(
                "Connection error: " + str(e.reason) + _sni_hint(self.base_url, e.reason)
            )

    def _call(self, method: str, path: str, *, params: Optional[dict] = None,
              json_body: Optional[Any] = None) -> dict:
        status, body = self._send(method, path, params=params, json_body=json_body)
        if status >= 400:
            self._raise(status, body if isinstance(body, dict) else {})
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _raise(status: int, body: dict) -> None:
        code = body.get("code")
        msg = body.get("message") or code or ("HTTP " + str(status))
        if status == 401:
            raise AuthenticationError(msg, status=status, code=code)
        if status == 403:
            raise NamespaceForbiddenError(msg, status=status, code=code)
        if status == 404:
            raise NotFoundError(msg, status=status, code=code)
        if status == 409:
            raise StaleWriteError(msg, status=status, code=code)
        if status == 429:
            ra = body.get("_retry_after") or body.get("retry_after")
            try:
                ra = int(ra) if ra is not None else None
            except (ValueError, TypeError):
                ra = None
            raise QuotaExceededError(msg, status=status, code=code, retry_after=ra)
        raise APIError(msg, status=status, code=code)

    # ── memories ───────────────────────────────────────────────────────────
    def add(self, content: "str | list", *, agent_id: Optional[str] = None,
            user_id: Optional[str] = None, run_id: Optional[str] = None,
            metadata: Optional[dict] = None,
            timestamp: Optional[str] = None) -> Memory:
        """POST /v1/memories — store a memory; returns it with extracted facts.

        ``content`` is a string, or a list of chat messages
        ``[{"role": ..., "content": ...}]`` (Mem0/Supermemory shape), which is
        joined into one text block before sending.

        Pass ``timestamp`` (ISO date/datetime) when the events happened in the
        past (backfill / migration): facts extracted inherit it as ``valid_from``,
        so ``as_of`` point-in-time queries reflect when things were true, not when
        they were ingested. Defaults to now."""
        text = _coerce_content(content)
        if not text.strip():
            raise KorelyError("content is empty — pass a non-blank string or messages with content.")
        body = self._call("POST", "/v1/memories", json_body=_clean({
            "content": text, "agent_id": agent_id, "user_id": user_id,
            "run_id": run_id, "metadata": metadata, "timestamp": timestamp,
        }))
        return Memory.from_dict(body)

    def search(self, query: str, *, user_id: Optional[str] = None,
               agent_id: Optional[str] = None, run_id: Optional[str] = None,
               metadata: Optional[dict] = None,
               limit: Optional[int] = None) -> List[SearchHit]:
        """POST /v1/memories/search — hybrid retrieval, ranked by score.

        ``run_id`` scopes to one session and ``metadata`` filters on what you
        stored at write time (keys ANDed, compared as strings). Both mirror the
        arguments ``add()`` accepts, so anything you can write you can query.

        ``limit`` defaults to the server default (15) when not passed."""
        body = self._call("POST", "/v1/memories/search", json_body=_clean({
            "query": query, "user_id": user_id, "agent_id": agent_id,
            "run_id": run_id, "metadata": metadata, "limit": limit,
        }))
        return [SearchHit.from_dict(h) for h in body.get("results", [])]

    def get_all(self, *, user_id: Optional[str] = None, agent_id: Optional[str] = None,
                run_id: Optional[str] = None,
                limit: int = 50, offset: int = 0) -> MemoryPage:
        """GET /v1/memories — list a scope, newest first.

        ``run_id`` narrows to one session. Metadata filtering lives on
        ``search()``, which carries a body and can take a dict."""
        body = self._call("GET", "/v1/memories", params=_clean({
            "user_id": user_id, "agent_id": agent_id, "run_id": run_id,
            "limit": limit, "offset": offset,
        }))
        return MemoryPage.from_dict(body)

    def get(self, memory_id: str) -> Memory:
        """GET /v1/memories/:id — full content, metadata, extracted facts."""
        return Memory.from_dict(self._call("GET", "/v1/memories/" + memory_id))

    def update(self, memory_id: str, *, content: str,
               expected_updated_at: Optional[str] = None) -> Memory:
        """PATCH /v1/memories/:id — re-runs extraction. Pass
        ``expected_updated_at`` for optimistic concurrency (raises
        StaleWriteError instead of clobbering)."""
        body = self._call("PATCH", "/v1/memories/" + memory_id, json_body=_clean({
            "content": content, "expected_updated_at": expected_updated_at,
        }))
        return Memory.from_dict(body)

    def delete(self, memory_id: str) -> DeleteReceipt:
        """DELETE /v1/memories/:id — forget one memory (audited invalidation)."""
        return DeleteReceipt.from_dict(self._call("DELETE", "/v1/memories/" + memory_id))

    def delete_all(self, *, user_id: str) -> BulkReceipt:
        """DELETE /v1/users/:user_id/memories — forget every memory + fact for
        one end user in a single call."""
        return BulkReceipt.from_dict(
            self._call("DELETE", "/v1/users/" + user_id + "/memories")
        )

    def history(self, memory_id: str) -> MemoryHistory:
        """GET /v1/memories/:id/history — the lifecycle timeline of a memory:
        created / updated / deleted, plus every typed fact it produced (and the
        moment each was learned or superseded)."""
        return MemoryHistory.from_dict(
            self._call("GET", "/v1/memories/" + memory_id + "/history")
        )

    def users(self, *, agent_id: Optional[str] = None, limit: int = 50,
              offset: int = 0) -> UsersPage:
        """GET /v1/users — the end users you've stored data for (the distinct
        ``user_id`` namespaces), each with active memory + fact counts and
        last-active time. The default (null) namespace is omitted. Returns a
        UsersPage: iterable like a list, with ``.total`` for pagination."""
        body = self._call("GET", "/v1/users", params=_clean({
            "agent_id": agent_id, "limit": limit, "offset": offset,
        }))
        return UsersPage.from_dict(body)

    # ── agents ───────────────────────────────────────────────────────────────
    def list_agents(self, *, limit: int = 50, offset: int = 0) -> AgentsPage:
        """GET /v1/agents — the agent namespaces you've written under (the
        distinct non-null ``agent_id`` values), each with active memory + fact
        counts and last-active time, plus the tier agent ``cap`` and how many
        slots are ``used``. The antidote to the agent-cap trap: when a write is
        rejected with ``agent_cap_exceeded``, call this to see which namespaces
        already exist and reuse one instead of minting a new id. Returns an
        AgentsPage: iterable like a list, with ``.total`` / ``.cap`` / ``.used``."""
        body = self._call("GET", "/v1/agents", params=_clean({
            "limit": limit, "offset": offset,
        }))
        return AgentsPage.from_dict(body)

    def delete_agent(self, agent_id: str) -> AgentDeleteReceipt:
        """DELETE /v1/agents/:agent_id — hard-delete an agent namespace: purge
        every memory + fact written under this ``agent_id`` and FREE its cap slot
        (soft-forgetting its data does NOT free the slot). Returns the purge
        counts + an audit id."""
        return AgentDeleteReceipt.from_dict(
            self._call("DELETE", "/v1/agents/" + agent_id)
        )

    # ── facts ────────────────────────────────────────────────────────────────
    def get_facts(self, *, subject: Optional[str] = None, entity: Optional[str] = None,
                  predicate: Optional[str] = None, predicate_family: Optional[str] = None,
                  include_invalidated: bool = False, as_of: Optional[str] = None,
                  user_id: Optional[str] = None, agent_id: Optional[str] = None,
                  limit: int = 50, offset: int = 0) -> List[Fact]:
        """GET /v1/facts — typed (subject, predicate, object) triples with
        bi-temporal validity. Pass ``as_of`` (ISO date) for a point-in-time
        query: what was true on that date."""
        params = _clean({
            "subject": subject, "entity": entity, "predicate": predicate,
            "predicate_family": predicate_family, "as_of": as_of,
            "user_id": user_id, "agent_id": agent_id, "limit": limit, "offset": offset,
        })
        if include_invalidated:
            params["include_invalidated"] = "true"
        body = self._call("GET", "/v1/facts", params=params)
        return [Fact.from_dict(f) for f in body.get("facts", [])]

    def add_fact_triple(self, subject: str, predicate: str, object: str, *,
                        user_id: Optional[str] = None, agent_id: Optional[str] = None,
                        run_id: Optional[str] = None, subject_type: str = "unknown",
                        object_is_literal: bool = False, confidence: float = 0.9,
                        valid_from: Optional[str] = None) -> Fact:
        """POST /v1/facts — write a typed (subject, predicate, object) triple
        directly, skipping extraction. The server runs the contradiction check
        and the fact is bi-temporal: pass ``valid_from`` (ISO date) for a
        historical fact. Returns the written Fact, with ``invalidated`` listing
        any fact ids it superseded."""
        body = self._call("POST", "/v1/facts", json_body=_clean({
            "subject": subject, "predicate": predicate, "object": object,
            "user_id": user_id, "agent_id": agent_id, "run_id": run_id,
            "subject_type": subject_type, "object_is_literal": object_is_literal,
            "confidence": confidence, "valid_from": valid_from,
        }))
        return Fact.from_dict(body)

    def get_profile(self, *, user_id: str, agent_id: Optional[str] = None,
                    as_of: Optional[str] = None) -> Profile:
        """GET /v1/profile — the assembled profile of one end user: the active
        typed facts known about them, the end user's own facts first, grouped by
        predicate family. ``user_id`` is required. Pass ``as_of`` (ISO date) for
        the point-in-time profile ("what we knew on 2026-03-01")."""
        body = self._call("GET", "/v1/profile", params=_clean({
            "user_id": user_id, "agent_id": agent_id, "as_of": as_of,
        }))
        return Profile.from_dict(body)

    # ── context ──────────────────────────────────────────────────────────────
    def get_context(self, *, query: str, user_id: Optional[str] = None,
                    agent_id: Optional[str] = None, token_budget: int = 800) -> Context:
        """GET /v1/context — one call that assembles a prompt-ready context
        block (profile + relevant facts + memories) within a token budget."""
        body = self._call("GET", "/v1/context", params=_clean({
            "query": query, "user_id": user_id, "agent_id": agent_id,
            "token_budget": token_budget,
        }))
        return Context.from_dict(body)

    # ── batch ────────────────────────────────────────────────────────────────
    def events(self, *, user_id: Optional[str] = None, status: Optional[str] = None,
               limit: int = 50) -> dict:
        """GET /v1/events — which writes have finished being processed.

        ``add()`` returns as soon as the memory is stored, then fact extraction
        runs behind it, so a read taken immediately can legitimately find no
        facts. This tells you which is which. The ``processing`` count is how
        many are still in flight for your account, so a batch import can wait on
        one number instead of walking every id.

        Prefer the ``fact_extracted`` webhook when you can receive one. This is
        the pull equivalent for local development, serverless, and scripts.
        """
        return self._call("GET", "/v1/events", params=_clean({
            "user_id": user_id, "status": status, "limit": limit,
        }))

    def batch(self, memories: List[dict]) -> BatchJob:
        """POST /v1/batch — bulk import (up to 500 memory objects), async."""
        body = self._call("POST", "/v1/batch", json_body={"memories": list(memories)})
        return BatchJob.from_dict(body)

    def batch_status(self, job_id: str) -> BatchJob:
        """GET /v1/batch/:id — poll an import job."""
        return BatchJob.from_dict(self._call("GET", "/v1/batch/" + job_id))
