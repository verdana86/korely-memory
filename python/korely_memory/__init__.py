"""korely-memory — the Python SDK for Korely Agents.

A typed, dependency-free client over the Korely REST API. Every method maps
1:1 onto an endpoint; the moat (typed bi-temporal facts, contradiction
checking) runs server-side.

    from korely_memory import Korely

    korely = Korely(api_key="kor_live_...", region="eu")
    korely.add("User prefers TypeScript", agent_id="coding-assistant")
    ctx = korely.get_context(query="what does the user like?", user_id="dana")
"""
from .aio import AsyncKorely
from .client import Korely, __version__
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
    HistoryEvent,
    Memory,
    MemoryHistory,
    MemoryPage,
    Profile,
    SearchHit,
    UserScope,
    UsersPage,
)

__all__ = [
    "Korely",
    "AsyncKorely",
    "__version__",
    # exceptions
    "KorelyError",
    "AuthenticationError",
    "NamespaceForbiddenError",
    "NotFoundError",
    "StaleWriteError",
    "QuotaExceededError",
    "APIError",
    # models
    "Memory",
    "Fact",
    "SearchHit",
    "MemoryPage",
    "DeleteReceipt",
    "BulkReceipt",
    "Context",
    "BatchJob",
    "Profile",
    "HistoryEvent",
    "MemoryHistory",
    "UserScope",
    "UsersPage",
    "AgentScope",
    "AgentsPage",
    "AgentDeleteReceipt",
]
