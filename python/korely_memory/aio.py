"""Async client.

An agent in production does not make one Korely call at a time. It fans out
across users, or reads memory while a model is already streaming. With only a
blocking client, we become the thing your event loop waits on.

``AsyncKorely`` mirrors ``Korely`` method for method, so anything you learned
from the sync client transfers:

    import asyncio
    from korely_memory import AsyncKorely

    async def main():
        korely = AsyncKorely()
        # three users, one round trip's worth of wall time
        ctxs = await asyncio.gather(
            korely.get_context(query="what plan?", user_id="a"),
            korely.get_context(query="what plan?", user_id="b"),
            korely.get_context(query="what plan?", user_id="c"),
        )

    asyncio.run(main())

Why a thread pool rather than an async HTTP library: this package has **zero
runtime dependencies**, which is the reason it is safe to drop into any stack,
and keeping it that way is worth more than the last drop of efficiency. Calls
run on ``asyncio.to_thread``, so the event loop is never blocked and concurrent
requests really do overlap. What you do not get is a socket-level async
implementation, which matters only at thousands of in-flight requests, and at
that point you want a connection pool we would have to configure anyway.

The sync ``Korely`` is unchanged and still the right choice for scripts.
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional

from .client import Korely
from .models import (
    AgentDeleteReceipt,
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
    UsersPage,
)

__all__ = ["AsyncKorely"]


class AsyncKorely:
    """Every method of :class:`Korely`, awaitable.

    Construction takes the same arguments, including the key resolution order:
    explicit argument, then ``KORELY_API_KEY``, then the key ``korely init``
    saved. Building one is cheap and does no I/O, so it is fine to create it at
    startup and share it.
    """

    def __init__(self, api_key: Optional[str] = None, region: str = "eu",
                 base_url: Optional[str] = None, timeout: float = 30.0):
        self._sync = Korely(api_key=api_key, region=region,
                            base_url=base_url, timeout=timeout)

    @property
    def api_key(self) -> str:
        return self._sync.api_key

    @property
    def base_url(self) -> str:
        return self._sync.base_url

    async def _run(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    # ── write ─────────────────────────────────────────────────────────────
    async def add(self, content: "str | list", **kw) -> Memory:
        return await self._run(lambda: self._sync.add(content, **kw))

    async def update(self, memory_id: str, **kw) -> Memory:
        return await self._run(lambda: self._sync.update(memory_id, **kw))

    async def delete(self, memory_id: str) -> DeleteReceipt:
        return await self._run(self._sync.delete, memory_id)

    async def delete_all(self, **kw) -> BulkReceipt:
        return await self._run(lambda: self._sync.delete_all(**kw))

    async def add_fact_triple(self, subject: str, predicate: str, object: str, **kw) -> Fact:
        return await self._run(
            lambda: self._sync.add_fact_triple(subject, predicate, object, **kw))

    async def batch(self, memories: List[dict]) -> BatchJob:
        return await self._run(self._sync.batch, memories)

    # ── read ──────────────────────────────────────────────────────────────
    async def search(self, query: str, **kw) -> List[SearchHit]:
        return await self._run(lambda: self._sync.search(query, **kw))

    async def get(self, memory_id: str) -> Memory:
        return await self._run(self._sync.get, memory_id)

    async def get_all(self, **kw) -> MemoryPage:
        return await self._run(lambda: self._sync.get_all(**kw))

    async def get_context(self, **kw) -> Context:
        return await self._run(lambda: self._sync.get_context(**kw))

    async def get_facts(self, **kw) -> List[Fact]:
        return await self._run(lambda: self._sync.get_facts(**kw))

    async def get_profile(self, **kw) -> Profile:
        return await self._run(lambda: self._sync.get_profile(**kw))

    async def history(self, memory_id: str) -> MemoryHistory:
        return await self._run(self._sync.history, memory_id)

    async def users(self, **kw) -> UsersPage:
        return await self._run(lambda: self._sync.users(**kw))

    async def list_agents(self, **kw) -> AgentsPage:
        return await self._run(lambda: self._sync.list_agents(**kw))

    async def delete_agent(self, agent_id: str) -> AgentDeleteReceipt:
        return await self._run(self._sync.delete_agent, agent_id)

    async def batch_status(self, job_id: str) -> BatchJob:
        return await self._run(self._sync.batch_status, job_id)

    def __repr__(self) -> str:  # pragma: no cover
        return f"AsyncKorely(base_url={self.base_url!r})"
