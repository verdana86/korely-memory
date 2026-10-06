"""Response models. The JSON shapes from the REST API reference are the
attribute shapes here. Each ``from_dict`` keeps documented fields and ignores
anything new, so a server that returns a superset never breaks an old SDK."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Dict, List, Optional, TypedDict


def _take(cls, d: Optional[dict]) -> dict:
    """Keep only the keys that are declared fields of ``cls``."""
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in (d or {}).items() if k in names}


@dataclass
class Fact:
    id: Optional[str] = None
    subject: Optional[str] = None
    predicate: Optional[str] = None
    object: Optional[str] = None
    predicate_family: Optional[str] = None
    subject_type: Optional[str] = None
    predicate_raw: Optional[str] = None
    object_is_literal: Optional[bool] = None
    confidence: Optional[float] = None
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    valid_from: Optional[str] = None
    invalid_at: Optional[str] = None
    invalidated_by: Optional[str] = None
    # Write shape only (add(), update(), add_fact_triple(), correct_fact()):
    # the ids this write superseded. On correct_fact() that is the corrected
    # fact plus any other the contradiction check closed; empty when the
    # correction restated the fact as it stands, which reconfirms it.
    invalidated: List[str] = field(default_factory=list)
    source_memory_id: Optional[str] = None
    created_at: Optional[str] = None
    # Sent by the server since 2026-09-27 and dropped here in 0.1.14 and earlier, because
    # `_take` keeps only declared fields. `tense` is what the text said
    # (current | past | planned); `observation_count` > 1 and
    # `last_confirmed_at` mean other memories restated the fact;
    # `subject_canonical` / `object_canonical` are the entities' current names
    # after aliases, while `subject` / `object` keep the words as written.
    tense: Optional[str] = None
    observation_count: Optional[int] = None
    last_confirmed_at: Optional[str] = None
    subject_canonical: Optional[str] = None
    object_canonical: Optional[str] = None
    source_memory_ids: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Fact":
        return cls(**_take(cls, d))


class FactList(list):
    """The facts of one ``get_facts()`` page: a plain list of Fact that also
    carries ``total``, how many facts match the filters across every page.

    ``GET /v1/facts`` has always answered with ``total``; ``get_facts()``
    returned a bare list and dropped it, so ``limit``/``offset`` could page but
    nothing said when to stop. A list subclass keeps every existing caller
    working."""

    def __init__(self, facts=(), total: Optional[int] = None):
        super().__init__(facts)
        self.total = int(total) if isinstance(total, (int, float)) else len(self)


@dataclass
class Memory:
    # `status` is on the wire and was not here, so the field the documentation
    # tells you to look at came back and was dropped on the floor. Facts are
    # mined by a worker a few seconds after the write, and "processing" is how
    # a write says the facts are not there yet: without it the only way to tell
    # an empty list from a not-yet list is to guess or to poll blindly.
    #
    # Found by a tester following the README, which says: what the
    # `"status": "processing"` in the reply means.
    id: Optional[str] = None
    content: Optional[str] = None
    status: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    facts: List[Fact] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Memory":
        d = dict(d or {})
        facts = [Fact.from_dict(f) for f in (d.get("facts") or [])]
        obj = cls(**_take(cls, d))
        obj.facts = facts
        return obj


@dataclass
class SearchHit:
    id: Optional[str] = None
    score: Optional[float] = None
    snippet: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "SearchHit":
        return cls(**_take(cls, d))


@dataclass
class MemoryPage:
    """Iterable page of memories with a ``total`` count."""
    memories: List[Memory] = field(default_factory=list)
    total: int = 0

    def __iter__(self):
        return iter(self.memories)

    def __len__(self) -> int:
        return len(self.memories)

    def __getitem__(self, i):
        return self.memories[i]

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryPage":
        d = d or {}
        return cls(
            memories=[Memory.from_dict(m) for m in (d.get("memories") or [])],
            total=int(d.get("total", 0)),
        )


@dataclass
class DeleteReceipt:
    id: Optional[str] = None
    status: Optional[str] = None
    facts_invalidated: Optional[int] = None
    audit_id: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "DeleteReceipt":
        return cls(**_take(cls, d))


@dataclass
class BulkReceipt:
    """What ``delete_all`` erased. Every row is physically deleted, and
    ``erasure`` is ``"permanent"``.

    ``memories_deleted`` and ``facts_deleted`` count the rows. Read these.

    ``memories_forgotten`` and ``facts_invalidated`` are **deprecated
    aliases** carrying the same two numbers. They were the only names the
    server sent until 2026-09-28, and they describe something that did not
    happen: nothing was forgotten or invalidated, it was deleted. They stay so
    that code written against 0.1.14 and earlier keeps working.

    Whichever pair the server sends fills both, so the new names work against
    an install that predates them, and the old ones keep working against a
    server that stops sending them. The new fields come last so a positional
    ``BulkReceipt(...)`` built by older code means what it meant.
    """
    user_id: Optional[str] = None
    #: Deprecated alias of ``memories_deleted``.
    memories_forgotten: Optional[int] = None
    #: Deprecated alias of ``facts_deleted``.
    facts_invalidated: Optional[int] = None
    erasure: Optional[str] = None
    audit_id: Optional[str] = None
    memories_deleted: Optional[int] = None
    facts_deleted: Optional[int] = None

    @classmethod
    def from_dict(cls, d: dict) -> "BulkReceipt":
        obj = cls(**_take(cls, d))
        if obj.memories_deleted is None:
            obj.memories_deleted = obj.memories_forgotten
        if obj.memories_forgotten is None:
            obj.memories_forgotten = obj.memories_deleted
        if obj.facts_deleted is None:
            obj.facts_deleted = obj.facts_invalidated
        if obj.facts_invalidated is None:
            obj.facts_invalidated = obj.facts_deleted
        return obj


@dataclass
class Context:
    """The block ``get_context()`` assembles.

    ``context`` is the whole block. Both servers also send it in two parts,
    and say when part of it is missing; the JS client typed these in 0.1.8,
    and this one dropped them until 2026-10-06, because ``_take`` keeps only
    declared fields. Each is None when the server does not send it (before
    2026-09-28 for ``degraded``, before 2026-10-01 for the parts).

    - ``stable``: the head of ``context`` that does not depend on the question
      (the reader note; with the profile on, the profile too), the same text
      from one call to the next. Put it in the system prompt, where the model
      provider's prompt cache can reuse it.
    - ``volatile``: the rest, the facts and memories for this question, and
      the closing NOTE when ``degraded``. ``context`` is ``stable`` and
      ``volatile`` joined by a blank line, either side possibly empty.
    - ``stable_hash``: SHA-256 (hex) of ``stable``; the same value means the
      same prefix, so a cached system prompt is still valid.
    - ``degraded``: True when part of the block could not be retrieved the
      way a healthy request retrieves it; the block then ends with a NOTE
      saying so to the model. ``degraded_parts`` says which: ``facts``
      and/or ``memories``.
    """
    context: str = ""
    tokens: int = 0
    sources: List[str] = field(default_factory=list)
    degraded: Optional[bool] = None
    degraded_parts: List[str] = field(default_factory=list)
    stable: Optional[str] = None
    volatile: Optional[str] = None
    stable_hash: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Context":
        return cls(**_take(cls, d))


class _BatchMemoryRequired(TypedDict):
    content: str


class BatchMemory(_BatchMemoryRequired, total=False):
    """One item of ``batch()``: the body of a single ``add()``, as a plain
    dict. ``content`` is required, every other key optional, and a key not
    listed here refuses the whole batch with a 422.

    ``timestamp`` (ISO 8601 date or datetime) is when the item's events
    happened; its facts inherit it as ``valid_from``, exactly as on ``add()``.
    An unreadable value refuses the whole batch with a 422 naming
    ``memories[i].timestamp``, before anything is queued."""
    user_id: str
    agent_id: str
    run_id: str
    metadata: dict
    timestamp: str


@dataclass
class BatchJob:
    id: Optional[str] = None
    status: Optional[str] = None
    received: Optional[int] = None
    imported: Optional[int] = None
    failed: Optional[int] = None
    errors: List[Any] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "BatchJob":
        return cls(**_take(cls, d))


@dataclass
class Profile:
    """The assembled profile of one end user: active typed facts known about
    them, the end user's own facts first, plus a ``by_family`` grouping.
    ``as_of`` echoes a point-in-time request. ``truncated`` is True when
    ``total`` exceeds the number of facts returned (the profile is capped at 200)."""
    user_id: Optional[str] = None
    as_of: Optional[str] = None
    facts: List[Fact] = field(default_factory=list)
    by_family: dict = field(default_factory=dict)
    total: int = 0
    truncated: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        d = dict(d or {})
        facts = [Fact.from_dict(f) for f in (d.get("facts") or [])]
        by_family = {
            k: [Fact.from_dict(f) for f in (v or [])]
            for k, v in (d.get("by_family") or {}).items()
        }
        obj = cls(**_take(cls, d))
        obj.facts = facts
        obj.by_family = by_family
        return obj


@dataclass
class HistoryEvent:
    """One point on a memory's timeline: created | updated | fact_extracted |
    fact_invalidated | deleted. ``fact`` / ``fact_id`` are set on fact events."""
    event: Optional[str] = None
    at: Optional[str] = None
    fact: Optional[str] = None
    fact_id: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "HistoryEvent":
        return cls(**_take(cls, d))


@dataclass
class MemoryHistory:
    id: Optional[str] = None
    events: List[HistoryEvent] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryHistory":
        d = dict(d or {})
        events = [HistoryEvent.from_dict(e) for e in (d.get("events") or [])]
        obj = cls(**_take(cls, d))
        obj.events = events
        return obj


@dataclass
class UserScope:
    """One end user the developer has stored data for, with counts."""
    user_id: Optional[str] = None
    memories: int = 0
    facts: int = 0
    last_active: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "UserScope":
        return cls(**_take(cls, d))


@dataclass
class UsersPage:
    """Iterable page of end users with a ``total`` count (for pagination)."""
    users: List[UserScope] = field(default_factory=list)
    total: int = 0

    def __iter__(self):
        return iter(self.users)

    def __len__(self) -> int:
        return len(self.users)

    def __getitem__(self, i):
        return self.users[i]

    @classmethod
    def from_dict(cls, d: dict) -> "UsersPage":
        d = d or {}
        return cls(
            users=[UserScope.from_dict(u) for u in (d.get("users") or [])],
            total=int(d.get("total", 0)),
        )


@dataclass
class AgentScope:
    """One agent namespace the developer has written under, with active counts."""
    agent_id: Optional[str] = None
    memories: int = 0
    facts: int = 0
    last_active: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "AgentScope":
        return cls(**_take(cls, d))


@dataclass
class AgentsPage:
    """Iterable page of agent namespaces.

    - ``total``: the namespaces in this key's project, before pagination,
      which are exactly the ones this key can delete.
    - ``used``: the agent slots taken across the whole account, counted by
      name. It reconciles with the ``agent_cap_exceeded`` 403, and is above
      ``total`` when other projects use names this project does not.
    - ``cap``: the plan's agent cap; 0 on a self-hosted install, which sets
      no ceiling.

    Until 2026-09-28 the server counted both over the account, so ``total``
    and ``used`` were the same number and this docstring said so."""
    agents: List[AgentScope] = field(default_factory=list)
    total: int = 0
    cap: int = 0
    used: int = 0

    def __iter__(self):
        return iter(self.agents)

    def __len__(self) -> int:
        return len(self.agents)

    def __getitem__(self, i):
        return self.agents[i]

    @classmethod
    def from_dict(cls, d: dict) -> "AgentsPage":
        d = d or {}
        return cls(
            agents=[AgentScope.from_dict(a) for a in (d.get("agents") or [])],
            total=int(d.get("total", 0)),
            cap=int(d.get("cap", 0)),
            used=int(d.get("used", 0)),
        )


@dataclass
class AgentDeleteReceipt:
    """The purge counts from hard-deleting an agent namespace.

    ``slot_freed`` says whether the agent cap slot is free now. The cap counts
    a name across the account, so it stays taken (False) while another
    project of the account still uses the same ``agent_id``; this key can
    neither see nor delete that project's rows. None when the server does not
    say: a self-hosted install has no cap, and servers before 2026-09-28 did
    not send it."""
    agent_id: Optional[str] = None
    memories_deleted: Optional[int] = None
    facts_deleted: Optional[int] = None
    audit_id: Optional[str] = None
    slot_freed: Optional[bool] = None

    @classmethod
    def from_dict(cls, d: dict) -> "AgentDeleteReceipt":
        return cls(**_take(cls, d))


@dataclass
class PingResponse:
    """What ``ping()`` answers: the key works.

    - ``ok``: always True; a key that does not authenticate is an
      AuthenticationError instead.
    - ``tier``: the plan of the key (hobby, developer, team, scale). On the
      Self-hosted every key says ``hobby``, and it limits nothing there.
    - ``region``: where the key's data is stored and processed, as the server
      declares it (``eu-hel1`` on the Cloud; on the Self-hosted, what its
      operator set).
    - ``scopes``: what the key may do, e.g. ``memories:read``."""
    ok: bool = False
    tier: Optional[str] = None
    region: Optional[str] = None
    scopes: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "PingResponse":
        return cls(**_take(cls, d))


@dataclass
class AuditRead:
    """For a read: what the call returned, by public id, at most 100 of each
    kind. With ``as_of`` on ``get_facts()`` they rebuild what an agent knew
    when it decided. Ids only: the trail never holds content."""
    memories: List[str] = field(default_factory=list)
    facts: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "AuditRead":
        return cls(**_take(cls, d))


@dataclass
class AuditEvent:
    """One event of the trail ``audit()`` reads (the API's ``AuditRow``).

    ``actor`` and ``action`` are open strings, not enums: one client reads two
    products whose lists differ, and a server may add a value.

    - ``ts``: when it happened, ISO 8601.
    - ``actor``: ``rest``, ``mcp``, ``worker``, ``dashboard`` (Cloud only) or
      ``manage`` (Self-hosted only).
    - ``action``: ``read``, ``write``, ``fact_write``, ``fact_invalidate``,
      ``erase``, ``key_create``, ``key_revoke``, and ``tenant_create`` on the
      Self-hosted only.
    - ``result``: ``ok``, ``denied`` or ``error``.
    - ``user_id``: the end user the event touched; ``target_id``: the memory or
      fact acted on, by public id.
    - ``meta``: counts and labels, never content; ``ip``: the caller's address.
    - ``read``: on a read, what it returned (:class:`AuditRead`); else None."""
    ts: Optional[str] = None
    actor: Optional[str] = None
    action: Optional[str] = None
    result: Optional[str] = None
    user_id: Optional[str] = None
    target_id: Optional[str] = None
    meta: Optional[dict] = None
    ip: Optional[str] = None
    read: Optional[AuditRead] = None

    @classmethod
    def from_dict(cls, d: dict) -> "AuditEvent":
        d = dict(d or {})
        read = d.get("read")
        obj = cls(**_take(cls, d))
        obj.read = AuditRead.from_dict(read) if isinstance(read, dict) else None
        return obj


@dataclass
class AuditPage:
    """Iterable page of the audit trail, newest first, with ``total``: the
    events that match the filters across every page."""
    events: List[AuditEvent] = field(default_factory=list)
    total: int = 0

    def __iter__(self):
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)

    def __getitem__(self, i):
        return self.events[i]

    @classmethod
    def from_dict(cls, d: dict) -> "AuditPage":
        d = d or {}
        return cls(
            events=[AuditEvent.from_dict(e) for e in (d.get("events") or [])],
            total=int(d.get("total", 0)),
        )


@dataclass
class AccountDeleteReceipt:
    """What ``delete_account()`` removed. ``deleted`` is True: the account and
    every key of it are gone, the one this client holds included. ``removed``
    counts the rows per kind (``memories``, ``facts``, ``keys``...), only the
    kinds that had any."""
    deleted: Optional[bool] = None
    removed: Dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "AccountDeleteReceipt":
        return cls(**_take(cls, d))


@dataclass
class AgentInitResult:
    """What ``Korely.init_agent()`` answers: a new hobby key and what it buys.

    ``api_key`` is shown this once and never again, so save it (``korely
    init`` writes it to ~/.korely/config.json). It is left out of ``repr()``:
    printing the result, or a traceback that shows it, does not put the key in
    a log. ``quotas`` is the hobby allowance (``writes_per_month``,
    ``queries_per_month``, ``agents``, the monthly AI budget)."""
    api_key: Optional[str] = field(default=None, repr=False)
    tier: Optional[str] = None
    region: Optional[str] = None
    scopes: List[str] = field(default_factory=list)
    quotas: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "AgentInitResult":
        return cls(**_take(cls, d))
