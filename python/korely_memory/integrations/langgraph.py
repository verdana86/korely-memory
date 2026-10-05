"""LangGraph on Korely: a context helper, two tools and a store.

    pip install 'korely-memory[langgraph]'

Three pieces, each usable on its own:

- ``korely_context(client, user_id, query)``: the text a graph node puts in a
  SystemMessage before calling the model. One ``GET /v1/context``, with the
  current date on top: the reader answers better when its prompt carries it.
- ``create_korely_tools(client, user_id)``: ``search_memory`` and
  ``save_memory`` for a tool-calling model. The app binds the user id here; it
  is not in the tools' schema, so the model can neither see nor choose it.
- ``KorelyStore``: LangGraph's ``BaseStore`` over the Korely API, for code that
  expects a store (``graph.compile(store=...)``, ``runtime.store``, LangMem).

Nothing here is imported by ``import korely_memory``: the core package keeps
zero runtime dependencies, and only this module needs the extra.
"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, Iterator, List, NamedTuple, Optional, Sequence, Tuple, Union

try:
    from langchain_core.tools import BaseTool, StructuredTool, ToolException
    from langgraph.store.base import (
        BaseStore,
        GetOp,
        InvalidNamespaceError,
        Item,
        ListNamespacesOp,
        PutOp,
        SearchItem,
        SearchOp,
        get_text_at_path,
    )
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover - exercised in a subprocess
    raise ImportError(
        "korely_memory.integrations.langgraph needs LangGraph and LangChain: "
        "pip install 'korely-memory[langgraph]' (Python 3.10 or later, which "
        "LangGraph itself requires). The rest of korely_memory works without it."
    ) from exc

from ..aio import AsyncKorely
from ..client import Korely
from ..exceptions import KorelyError, NotFoundError
from ..models import Memory

__all__ = [
    "KorelyStore",
    "KorelyScope",
    "default_namespace_to_scope",
    "create_korely_tools",
    "korely_context",
    "akorely_context",
]

# The metadata key under which the store keeps its own bookkeeping (namespace,
# key, timestamps). Every other metadata key is a field of the stored value.
_RESERVED = "langgraph"
# Each namespace is one Korely run: "langgraph:" + the labels joined by ".".
# LangGraph refuses a period inside a label, so the join cannot be ambiguous.
_RUN_PREFIX = "langgraph:"
_RUN_ID_MAX = 255  # the API's limit on run_id
_LIST_PAGE = 200  # GET /v1/memories: the largest page
_SEARCH_MAX = 50  # POST /v1/memories/search: the largest limit
# Where a value's text usually lives: LangGraph's own examples use "data" and
# "memory", LangMem writes "content".
_TEXT_FIELDS = ("content", "text", "memory", "data")
_OPERATORS = ("$eq", "$ne", "$gt", "$gte", "$lt", "$lte")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

ClientLike = Union[Korely, AsyncKorely]


def _sync_client(client: Optional[ClientLike]) -> Korely:
    """The blocking client behind whatever the caller passed. An AsyncKorely
    wraps one, and the store and the sync tools need exactly that."""
    if client is None:
        return Korely()
    if isinstance(client, AsyncKorely):
        return client._sync
    if isinstance(client, Korely):
        return client
    raise TypeError(f"expected a Korely or AsyncKorely client, got {type(client).__name__}")


def _require_user(user_id: Any, where: str) -> str:
    """A user id is required wherever the model's prompt is built.

    ``GET /v1/context`` without ``user_id`` reads every end user of the key,
    so a node whose ``runtime.context.user_id`` came back empty would put other
    people's memories in this user's prompt. Refusing here turns that into an
    error at the first call instead of a leak nobody sees.
    """
    if user_id is None or not str(user_id).strip():
        raise ValueError(
            f"{where} needs the end user's id: without it Korely reads the "
            "memories of every end user of the key."
        )
    return str(user_id)


# ── context ────────────────────────────────────────────────────────────────


def _day(today: Optional[date]) -> str:
    """The date line's date: the one given, else today in UTC, the calendar
    the server's fact dates use."""
    if today is None:
        return datetime.now(timezone.utc).date().isoformat()
    if isinstance(today, datetime):
        return today.date().isoformat()
    if isinstance(today, date):
        return today.isoformat()
    raise TypeError(f"today must be a date, got {type(today).__name__}")


def _with_date(block: str, include_date: bool, today: Optional[date]) -> str:
    parts = []
    if include_date:
        parts.append("Current date: " + _day(today))
    if block and block.strip():
        parts.append(block.strip())
    return "\n\n".join(parts)


def korely_context(
    client: Optional[ClientLike],
    user_id: str,
    query: Optional[str],
    *,
    token_budget: int = 800,
    include_date: bool = True,
    agent_id: Optional[str] = None,
    today: Optional[date] = None,
) -> str:
    """The text a graph node prepends as a SystemMessage before the model answers.

    One ``GET /v1/context``: the user's current facts and the memories most
    relevant to ``query``, packed under ``token_budget`` tokens (50 to 8000),
    with ``Current date: YYYY-MM-DD`` on top. The date is there by default
    because the reader answers better when its prompt carries it: "since
    2026-05-12" means something only next to today. ``today`` overrides it
    (pass the user's local date if you know it); ``include_date=False`` leaves
    it out.

    An empty ``query`` (a message with no text) makes no call and returns the
    date line alone. Errors are the client's: a ``KorelyError`` subclass.
    """
    uid = _require_user(user_id, "korely_context")
    block = ""
    if query is not None and str(query).strip():
        ctx = _sync_client(client).get_context(
            str(query), user_id=uid, agent_id=agent_id, token_budget=token_budget)
        block = ctx.context or ""
    return _with_date(block, include_date, today)


async def akorely_context(
    client: Optional[ClientLike],
    user_id: str,
    query: Optional[str],
    *,
    token_budget: int = 800,
    include_date: bool = True,
    agent_id: Optional[str] = None,
    today: Optional[date] = None,
) -> str:
    """``korely_context`` for async nodes: same arguments, same text. With a
    ``Korely`` client the call runs on a thread, so the event loop never waits."""
    uid = _require_user(user_id, "akorely_context")
    block = ""
    if query is not None and str(query).strip():
        if isinstance(client, AsyncKorely):
            ctx = await client.get_context(
                str(query), user_id=uid, agent_id=agent_id, token_budget=token_budget)
        else:
            ctx = await asyncio.to_thread(
                _sync_client(client).get_context, str(query),
                user_id=uid, agent_id=agent_id, token_budget=token_budget)
        block = ctx.context or ""
    return _with_date(block, include_date, today)


# ── tools ──────────────────────────────────────────────────────────────────


class _SearchMemoryInput(BaseModel):
    query: str = Field(
        description="What to look up, in plain words: a question or a topic, "
                    "e.g. 'where does the user live'.")


class _SaveMemoryInput(BaseModel):
    content: str = Field(
        description="One thing worth remembering about the user, as a short "
                    "self-contained sentence, e.g. 'The user is vegetarian.'")


_SEARCH_DESCRIPTION = (
    "Look up what long-term memory knows about the user: their current facts, "
    "with the date each became true, and the past messages most relevant to "
    "the query. Use it before answering anything that depends on the user's "
    "history, preferences or situation."
)
_SAVE_DESCRIPTION = (
    "Save one thing worth remembering about the user to long-term memory: a "
    "preference, a fact about their life or work, a decision. Write it as a "
    "short, self-contained sentence. It is available in every later "
    "conversation with this user."
)
_NOTHING_FOUND = "Nothing in long-term memory matches this query."


def create_korely_tools(
    client: Optional[ClientLike],
    user_id: str,
    agent_id: Optional[str] = None,
    *,
    token_budget: int = 800,
    include_date: bool = True,
) -> List[BaseTool]:
    """``[search_memory, save_memory]``, bound to one end user.

    - ``search_memory(query)``: one ``GET /v1/context``, the same block as
      ``korely_context`` (current facts and relevant memories, the date on
      top), or a sentence saying nothing matched.
    - ``save_memory(content)``: one ``POST /v1/memories``; Korely extracts the
      facts in the background.

    ``user_id`` (and ``agent_id``) are fixed here by the app. They are not
    fields of the tools' input schema, so the model never sees them, and an
    argument it invents anyway is dropped by the schema. Create the tools per
    user, for instance in the node that calls the model.

    A Korely error comes back to the model as the tool's answer (the tools set
    ``handle_tool_error``), so a memory outage costs a sentence, not the run.
    """
    uid = _require_user(user_id, "create_korely_tools")
    korely = _sync_client(client)
    aclient = client if isinstance(client, AsyncKorely) else None

    def _answer(block: Optional[str]) -> str:
        if not block or not block.strip():
            return _NOTHING_FOUND
        return _with_date(block, include_date, None)

    def search_memory(query: str) -> str:
        try:
            ctx = korely.get_context(query, user_id=uid, agent_id=agent_id,
                                     token_budget=token_budget)
        except KorelyError as exc:
            raise ToolException(f"Korely memory is unavailable: {exc}") from exc
        return _answer(ctx.context)

    async def asearch_memory(query: str) -> str:
        try:
            if aclient is not None:
                ctx = await aclient.get_context(query, user_id=uid, agent_id=agent_id,
                                                token_budget=token_budget)
            else:
                ctx = await asyncio.to_thread(korely.get_context, query, user_id=uid,
                                              agent_id=agent_id, token_budget=token_budget)
        except KorelyError as exc:
            raise ToolException(f"Korely memory is unavailable: {exc}") from exc
        return _answer(ctx.context)

    def _saved(memory: Memory) -> str:
        return f"Saved to long-term memory ({memory.id})."

    def save_memory(content: str) -> str:
        try:
            memory = korely.add(content, user_id=uid, agent_id=agent_id)
        except KorelyError as exc:
            raise ToolException(f"Could not save to Korely memory: {exc}") from exc
        return _saved(memory)

    async def asave_memory(content: str) -> str:
        try:
            if aclient is not None:
                memory = await aclient.add(content, user_id=uid, agent_id=agent_id)
            else:
                memory = await asyncio.to_thread(korely.add, content, user_id=uid,
                                                 agent_id=agent_id)
        except KorelyError as exc:
            raise ToolException(f"Could not save to Korely memory: {exc}") from exc
        return _saved(memory)

    return [
        StructuredTool.from_function(
            func=search_memory, coroutine=asearch_memory, name="search_memory",
            description=_SEARCH_DESCRIPTION, args_schema=_SearchMemoryInput,
            handle_tool_error=True),
        StructuredTool.from_function(
            func=save_memory, coroutine=asave_memory, name="save_memory",
            description=_SAVE_DESCRIPTION, args_schema=_SaveMemoryInput,
            handle_tool_error=True),
    ]


# ── store ──────────────────────────────────────────────────────────────────


class KorelyScope(NamedTuple):
    """Where a namespace's items live in Korely: the end user and, optionally,
    the agent namespace. ``agent_id=None`` falls back to the store's own."""
    user_id: Optional[str]
    agent_id: Optional[str] = None


def default_namespace_to_scope(namespace: Tuple[str, ...]) -> KorelyScope:
    """``("memories", user_id)``, LangGraph's convention, is that Korely end user.

    Any other shape is refused rather than guessed: ``(user_id, "memories")``
    and ``("memories", user_id)`` look alike, and reading one as the other
    files a user's memories under somebody else. Map other shapes with
    ``KorelyStore(namespace_to_scope=...)``.
    """
    if len(namespace) == 2 and namespace[0] == "memories" and namespace[1]:
        return KorelyScope(user_id=namespace[1])
    raise InvalidNamespaceError(
        f"KorelyStore reads the namespace ('memories', user_id) as that Korely "
        f"end user, and {tuple(namespace)!r} is not that shape. For another "
        "one pass namespace_to_scope=, a function from the namespace to the "
        "user id or to (user_id, agent_id)."
    )


def _as_scope(raw: Any, namespace: Tuple[str, ...]) -> KorelyScope:
    if isinstance(raw, KorelyScope):
        return raw
    if raw is None or isinstance(raw, str):
        return KorelyScope(user_id=raw)
    if isinstance(raw, tuple) and len(raw) in (1, 2):
        return KorelyScope(*raw)
    raise TypeError(
        f"namespace_to_scope({namespace!r}) returned {raw!r}: expected a user id, "
        "a (user_id, agent_id) tuple or a KorelyScope.")


class _Where(NamedTuple):
    """One namespace, resolved: the labels, and the Korely scope and run that
    hold its items."""
    namespace: Tuple[str, ...]
    user_id: Optional[str]
    agent_id: Optional[str]
    run_id: str


def _check_namespace(namespace: Any) -> Tuple[str, ...]:
    """LangGraph's rules for a label, applied to reads too: the run id joins
    the labels with ".", which is only unambiguous if no label holds one."""
    ns = tuple(namespace)
    if not ns:
        raise InvalidNamespaceError("Namespace cannot be empty.")
    for label in ns:
        if not isinstance(label, str) or not label:
            raise InvalidNamespaceError(
                f"Namespace labels must be non-empty strings; got {label!r} in {ns!r}.")
        if "." in label:
            raise InvalidNamespaceError(
                f"Invalid namespace label {label!r} in {ns!r}: labels cannot "
                "contain periods ('.').")
    return ns


def _bookkeeping(metadata: Any, namespace: Tuple[str, ...]) -> Optional[Dict[str, Any]]:
    """The store's own record on a memory, when the memory is an item of this
    namespace; None for anything else.

    The run id already scopes every read to the namespace. This second check is
    for a server that ignores the filter (an older self-hosted install) and for
    a memory somebody else wrote under the same run id: neither becomes an item.
    """
    if not isinstance(metadata, dict):
        return None
    record = metadata.get(_RESERVED)
    if not isinstance(record, dict) or not isinstance(record.get("key"), str):
        return None
    if tuple(record.get("namespace") or ()) != namespace:
        return None
    return record


def _parse_time(value: Any, fallback: Optional[datetime]) -> datetime:
    """An ISO 8601 timestamp as an aware datetime. ``Z`` is accepted, which
    ``fromisoformat`` refuses before Python 3.11."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return fallback if fallback is not None else _EPOCH


def _check_value(value: Any) -> Dict[str, Any]:
    """A value the store can keep: a dict with string keys that survives a JSON
    round trip, without the store's reserved field. Checked before anything is
    sent, so a bad value never leaves a half-done write behind."""
    if not isinstance(value, dict):
        raise TypeError(f"A store value is a dict, got {type(value).__name__}.")
    for name in value:
        if not isinstance(name, str):
            raise TypeError(f"Store value keys must be strings, got {name!r}.")
    if _RESERVED in value:
        raise ValueError(
            f"The field name {_RESERVED!r} is reserved: KorelyStore keeps its "
            "own bookkeeping under it in the memory's metadata.")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "KorelyStore keeps the value as the memory's JSON metadata, and this "
            f"value is not JSON: {exc}") from exc
    return value


def _default_text(value: Dict[str, Any]) -> str:
    """The text Korely stores for a value when no index fields are given: its
    ``content``, ``text``, ``memory`` or ``data`` string (looked for inside a
    nested dict too, which is where LangMem puts it), else one ``key: value``
    line per field."""
    for name in _TEXT_FIELDS:
        field_value = value.get(name)
        if isinstance(field_value, str) and field_value.strip():
            return field_value
        if isinstance(field_value, dict):
            nested = _default_text(field_value)
            if nested.strip():
                return nested
    lines = []
    for name, field_value in value.items():
        if field_value is None or field_value == "" or field_value == [] or field_value == {}:
            continue
        shown = field_value if isinstance(field_value, str) else json.dumps(
            field_value, ensure_ascii=False, sort_keys=True)
        lines.append(f"{name}: {shown}")
    return "\n".join(lines)


def _check_filter(flt: Any) -> Optional[Dict[str, Any]]:
    """A LangGraph filter, with every operator known: an unknown one is refused
    before any request, as the reference store refuses it."""
    if not flt:
        return None
    if not isinstance(flt, dict):
        raise TypeError(f"A search filter is a dict, got {type(flt).__name__}.")

    def walk(cond: Any) -> None:
        if isinstance(cond, dict):
            for name, inner in cond.items():
                if isinstance(name, str) and name.startswith("$") and name not in _OPERATORS:
                    raise ValueError(f"Unsupported filter operator: {name}")
                walk(inner)
        elif isinstance(cond, (list, tuple)):
            for inner in cond:
                walk(inner)

    walk(flt)
    return dict(flt)


def _pushdown(flt: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """The metadata filter Korely applies before ranking, for a semantic search.

    Korely filters metadata by equality on a top-level key, comparing strings,
    so a condition goes to the server as the text its JSON value reads as:
    ``True`` as ``"true"``, ``5`` as ``"5"``. Anything else (an operator other
    than ``$eq``, a nested dict, a list, None) has no server form. Filtering it
    on our side after the ranking would drop matches ranked past the hits we
    fetched, a wrong answer that looks right, so it is refused instead.
    """
    if not flt:
        return None
    out: Dict[str, str] = {}
    for name, cond in flt.items():
        if isinstance(cond, dict) and list(cond) == ["$eq"]:
            cond = cond["$eq"]
        if isinstance(cond, bool):
            out[name] = "true" if cond else "false"
        elif isinstance(cond, str):
            out[name] = cond
        elif isinstance(cond, int):
            out[name] = str(cond)
        elif isinstance(cond, float) and math.isfinite(cond):
            out[name] = json.dumps(cond)
        else:
            raise NotImplementedError(
                f"KorelyStore cannot apply the filter {{{name!r}: {cond!r}}} to a "
                "search with a query: Korely filters metadata by equality on a "
                "top-level field (a string, number or boolean). Search without "
                "a query to use every filter operator.")
    return out


def _compare(item_value: Any, cond: Any) -> bool:
    """LangGraph's filter semantics, as its in-memory store applies them."""
    if isinstance(cond, dict):
        if any(isinstance(k, str) and k.startswith("$") for k in cond):
            return all(_operator(item_value, op, arg) for op, arg in cond.items())
        if not isinstance(item_value, dict):
            return False
        return all(_compare(item_value.get(k), v) for k, v in cond.items())
    if isinstance(cond, (list, tuple)):
        return (isinstance(item_value, (list, tuple)) and len(item_value) == len(cond)
                and all(_compare(a, b) for a, b in zip(item_value, cond)))
    return item_value == cond


def _operator(value: Any, op: str, arg: Any) -> bool:
    if op == "$eq":
        return value == arg
    if op == "$ne":
        return value != arg
    try:
        left, right = float(value), float(arg)
    except (TypeError, ValueError):
        return False  # a missing or non-numeric field is not greater than anything
    if op == "$gt":
        return left > right
    if op == "$gte":
        return left >= right
    if op == "$lt":
        return left < right
    if op == "$lte":
        return left <= right
    raise ValueError(f"Unsupported filter operator: {op}")


def _matches(value: Dict[str, Any], flt: Optional[Dict[str, Any]]) -> bool:
    return not flt or all(_compare(value.get(k), v) for k, v in flt.items())


_NO_LIST = (
    "KorelyStore cannot list namespaces: the Korely API has no way to list the "
    "runs that hold them. korely.users() lists your end users."
)
_NO_TTL = (
    "KorelyStore does not support ttl: a Korely memory stays until it is "
    "deleted. Leave ttl unset."
)
_NO_UNINDEXED = (
    "KorelyStore cannot store an item outside the index: Korely embeds every "
    "memory and extracts facts from it. Keep values that must not be searched "
    "in another store."
)


class KorelyStore(BaseStore):
    """LangGraph's long-term memory store, on the Korely API.

    ``graph.compile(store=KorelyStore(korely))`` and every node's
    ``runtime.store`` reads and writes Korely. An item is one Korely memory of
    the namespace's end user, so what the store writes is embedded, digested
    into facts and found by ``get_context()``, ``korely_context()`` and the
    tools, like anything else the user said.

    How an item maps:

    - the namespace is the end user (and agent) that ``namespace_to_scope``
      returns, by default ``("memories", user_id)`` -> ``user_id``, plus a run,
      ``run_id="langgraph:" + ".".join(namespace)``, so the store reads its own
      items and nothing else of the user's;
    - the value travels whole in the memory's metadata (JSON, within the
      server's metadata cap: 8 KB on the current server) and comes back exactly;
    - the memory's text, which Korely embeds and mines, is the value's
      ``content``, ``text``, ``memory`` or ``data`` string, else one
      ``key: value`` line per field; ``index_fields`` here, or ``index=[...]``
      on a put, choose the fields instead, as LangGraph index paths.

    What the API allows, and so what this does:

    - ``put``, ``get`` and ``delete`` by key work, at a price. The API has no
      lookup by a key of yours, so each one pages through the namespace's items
      (one ``GET /v1/memories`` per 200). A ``put`` on an existing key stores
      the new memory first, then forgets the old one, so a failed write never
      loses the item. ``delete`` forgets the memory as ``korely.delete`` does:
      its facts are invalidated and kept as history (erasure is
      ``delete_all``).
    - ``search`` with a query is one ``POST /v1/memories/search``, ranked by
      Korely, scores included; ``offset + limit`` can be at most 50, and the
      filter takes equality on top-level fields. Without a query it lists the
      namespace newest first, with every filter operator. A search covers
      exactly the namespace it names: under the default mapping there is
      nothing below ``("memories", user_id)``, and with a ``namespace_to_scope``
      that accepts deeper namespaces, their items are not included.
    - Not supported, and refused before any request: ``list_namespaces``,
      searching a prefix that spans namespaces (``("memories",)``), ``ttl``,
      and ``index=False``.

    Two writers putting the same key at the same moment can both create a
    memory. Reads then return the newest, and the next ``put`` or ``delete`` of
    that key removes the other.
    """

    supports_ttl = False

    def __init__(
        self,
        client: Optional[ClientLike] = None,
        *,
        agent_id: Optional[str] = None,
        namespace_to_scope: Optional[Callable[[Tuple[str, ...]], Any]] = None,
        index_fields: Optional[Sequence[str]] = None,
    ) -> None:
        """``client``: a ``Korely`` or ``AsyncKorely`` (None builds a
        ``Korely()`` from the environment). ``agent_id``: the agent namespace
        for every scope that does not name one. ``namespace_to_scope``: a
        function from a namespace to the user id, a ``(user_id, agent_id)``
        tuple or a ``KorelyScope``; raise ``InvalidNamespaceError`` for a
        namespace it does not map. ``index_fields``: the value fields whose
        text becomes the memory's text, as LangGraph index paths."""
        self._client = _sync_client(client)
        self._agent_id = agent_id
        self._to_scope = namespace_to_scope or default_namespace_to_scope
        if isinstance(index_fields, str):
            index_fields = [index_fields]
        self._index_fields = list(index_fields) if index_fields else None

    # ── the two methods BaseStore requires ────────────────────────────────

    def batch(self, ops) -> list:
        """Run the operations in order, each one seeing the ones before it.

        Every operation is checked before the first request, so a batch with
        one operation Korely cannot do sends nothing at all.
        """
        plans = [self._plan(op) for op in ops]
        return [plan() for plan in plans]

    async def abatch(self, ops) -> list:
        """``batch`` on a thread: the client is blocking and keeps zero
        dependencies, and the event loop never waits for it."""
        return await asyncio.to_thread(self.batch, list(ops))

    # ── planning: every check before any request ─────────────────────────

    def _where(self, namespace: Any) -> _Where:
        ns = _check_namespace(namespace)
        scope = _as_scope(self._to_scope(ns), ns)
        run_id = _RUN_PREFIX + ".".join(ns)
        if len(run_id) > _RUN_ID_MAX:
            raise InvalidNamespaceError(
                f"Namespace {ns!r} is too long for Korely: its run id would be "
                f"{len(run_id)} characters, and the API takes {_RUN_ID_MAX}.")
        agent_id = scope.agent_id if scope.agent_id is not None else self._agent_id
        return _Where(ns, scope.user_id, agent_id, run_id)

    def _plan(self, op: Any) -> Callable[[], Any]:
        if isinstance(op, GetOp):
            where, key = self._where(op.namespace), str(op.key)
            return lambda: self._get(where, key)
        if isinstance(op, SearchOp):
            return self._plan_search(op)
        if isinstance(op, PutOp):
            return self._plan_put(op)
        if isinstance(op, ListNamespacesOp):
            raise NotImplementedError(_NO_LIST)
        raise ValueError(f"Unknown operation type: {type(op).__name__}")

    def _plan_put(self, op: PutOp) -> Callable[[], Any]:
        where, key = self._where(op.namespace), str(op.key)
        if op.value is None:
            return lambda: self._delete(where, key)
        if getattr(op, "ttl", None) is not None:
            raise NotImplementedError(_NO_TTL)
        value = _check_value(op.value)
        text = self._text(value, op.index)
        return lambda: self._put(where, key, value, text)

    def _plan_search(self, op: SearchOp) -> Callable[[], Any]:
        try:
            where = self._where(op.namespace_prefix)
        except InvalidNamespaceError as exc:
            raise NotImplementedError(
                "KorelyStore searches one namespace at a time, and "
                f"{tuple(op.namespace_prefix)!r} is not one it maps: {exc}") from exc
        limit, offset = int(op.limit), int(op.offset)
        if limit < 0 or offset < 0:
            raise ValueError("limit and offset cannot be negative.")
        flt = _check_filter(op.filter)
        if op.query:
            if offset + limit > _SEARCH_MAX:
                raise NotImplementedError(
                    f"Korely returns at most {_SEARCH_MAX} hits per search, so "
                    f"offset + limit must be {_SEARCH_MAX} or less (got "
                    f"offset={offset}, limit={limit}).")
            pushed = _pushdown(flt)
            query = str(op.query)
            return lambda: self._semantic(where, query, flt, pushed, limit, offset)
        return lambda: self._listing(where, flt, limit, offset)

    def _text(self, value: Dict[str, Any], index: Any) -> str:
        """The memory's text: what Korely embeds and extracts facts from."""
        if index is False or (index is not None and len(index) == 0):
            raise NotImplementedError(_NO_UNINDEXED)
        paths = list(index) if index is not None else self._index_fields
        if paths:
            texts: List[str] = []
            for path in paths:
                texts.extend(t for t in get_text_at_path(value, path) if t and t.strip())
            text = "\n".join(texts)
            if not text.strip():
                raise ValueError(
                    f"None of the index fields {paths!r} hold any text in this "
                    "value, and Korely stores a memory as text.")
            return text
        text = _default_text(value)
        if not text.strip():
            raise ValueError(
                "KorelyStore stores a value as a memory, which needs text, and "
                "this value has none.")
        return text

    # ── reads ─────────────────────────────────────────────────────────────

    def _items(self, where: _Where) -> Iterator[Tuple[Memory, Dict[str, Any]]]:
        """The namespace's items, newest first: its run, page by page.

        The offset advances by what each page held, not by the page size: a
        server from before 2026-09-28 caps a page at 100.
        """
        offset, seen = 0, set()
        while True:
            page = self._client.get_all(
                user_id=where.user_id, agent_id=where.agent_id, run_id=where.run_id,
                limit=_LIST_PAGE, offset=offset)
            for memory in page.memories:
                if memory.id in seen:
                    continue
                seen.add(memory.id)
                record = _bookkeeping(memory.metadata, where.namespace)
                if record is not None:
                    yield memory, record
            offset += len(page.memories)
            if not page.memories or offset >= page.total:
                return

    @staticmethod
    def _item(where: _Where, metadata: Dict[str, Any], record: Dict[str, Any],
              fallback: Optional[datetime], score: Optional[float] = None,
              search: bool = False) -> Item:
        value = {k: v for k, v in metadata.items() if k != _RESERVED}
        created = _parse_time(record.get("created_at"), fallback)
        updated = _parse_time(record.get("updated_at"), created)
        if search:
            return SearchItem(namespace=where.namespace, key=record["key"], value=value,
                              created_at=created, updated_at=updated, score=score)
        return Item(value=value, key=record["key"], namespace=where.namespace,
                    created_at=created, updated_at=updated)

    def _get(self, where: _Where, key: str) -> Optional[Item]:
        for memory, record in self._items(where):
            if record["key"] == key:  # newest first: the current value
                return self._item(where, memory.metadata, record,
                                  _parse_time(memory.created_at, None))
        return None

    def _semantic(self, where: _Where, query: str, flt: Optional[Dict[str, Any]],
                  pushed: Optional[Dict[str, str]], limit: int, offset: int) -> list:
        if limit == 0:
            return []
        # With a filter, ask for the most hits there are: the server compares
        # strings, so a stored "5" passes a filter on 5 and is dropped below.
        want = _SEARCH_MAX if flt else offset + limit
        hits = self._client.search(query, user_id=where.user_id, agent_id=where.agent_id,
                                   run_id=where.run_id, metadata=pushed, limit=want)
        found: List[SearchItem] = []
        position: Dict[str, int] = {}
        for hit in hits:
            record = _bookkeeping(hit.metadata, where.namespace)
            if record is None:
                continue
            item = self._item(where, hit.metadata, record, None, score=hit.score, search=True)
            if not _matches(item.value, flt):
                continue
            seen_at = position.get(item.key)
            if seen_at is None:
                position[item.key] = len(found)
                found.append(item)
            elif item.updated_at > found[seen_at].updated_at:
                # Two memories for one key (concurrent writers): the newer
                # value, at the better rank.
                found[seen_at] = item
        return found[offset:offset + limit]

    def _listing(self, where: _Where, flt: Optional[Dict[str, Any]],
                 limit: int, offset: int) -> list:
        if limit == 0:
            return []
        out: List[SearchItem] = []
        keys, skipped = set(), 0
        for memory, record in self._items(where):
            if record["key"] in keys:
                continue  # an older memory of a key already listed
            keys.add(record["key"])
            item = self._item(where, memory.metadata, record,
                              _parse_time(memory.created_at, None), search=True)
            if not _matches(item.value, flt):
                continue
            if skipped < offset:
                skipped += 1
                continue
            out.append(item)
            if len(out) >= limit:
                break
        return out

    # ── writes ────────────────────────────────────────────────────────────

    def _put(self, where: _Where, key: str, value: Dict[str, Any], text: str) -> None:
        # The whole namespace is read before anything is written: deleting
        # while paging would shift the offsets and skip a memory.
        old = [(memory, record) for memory, record in self._items(where)
               if record["key"] == key]
        now = datetime.now(timezone.utc).isoformat()
        created = old[0][1].get("created_at") if old else None
        metadata = dict(value)
        metadata[_RESERVED] = {
            "namespace": list(where.namespace),
            "key": key,
            "created_at": created or now,
            "updated_at": now,
        }
        self._client.add(text, user_id=where.user_id, agent_id=where.agent_id,
                         run_id=where.run_id, metadata=metadata)
        for memory, _record in old:
            self._forget(memory.id)

    def _delete(self, where: _Where, key: str) -> None:
        doomed = [memory.id for memory, record in self._items(where) if record["key"] == key]
        for memory_id in doomed:
            self._forget(memory_id)

    def _forget(self, memory_id: Optional[str]) -> None:
        if not memory_id:
            return
        try:
            self._client.delete(memory_id)
        except NotFoundError:
            pass  # already forgotten, by a concurrent writer: the outcome wanted
