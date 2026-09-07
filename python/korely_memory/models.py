"""Response models. The JSON shapes from the REST API reference are the
attribute shapes here — each ``from_dict`` keeps documented fields and ignores
anything new, so a server that returns a superset never breaks an old SDK."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, List, Optional


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
    # write-shape only: ids this fact superseded (from add()/update())
    invalidated: List[str] = field(default_factory=list)
    source_memory_id: Optional[str] = None
    created_at: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Fact":
        return cls(**_take(cls, d))


@dataclass
class Memory:
    id: Optional[str] = None
    content: Optional[str] = None
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
    user_id: Optional[str] = None
    memories_forgotten: Optional[int] = None
    facts_invalidated: Optional[int] = None
    audit_id: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "BulkReceipt":
        return cls(**_take(cls, d))


@dataclass
class Context:
    context: str = ""
    tokens: int = 0
    sources: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Context":
        return cls(**_take(cls, d))


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
    """Iterable page of agent namespaces. ``total`` == distinct namespaces ==
    ``used`` slots; ``cap`` is the tier agent cap (reconciles with the
    ``agent_cap_exceeded`` 403)."""
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
    """The purge counts from hard-deleting an agent namespace (frees its cap slot)."""
    agent_id: Optional[str] = None
    memories_deleted: Optional[int] = None
    facts_deleted: Optional[int] = None
    audit_id: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "AgentDeleteReceipt":
        return cls(**_take(cls, d))
