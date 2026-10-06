# korely-memory

The Python SDK for [Korely Agents](https://korely.ai/agents): memory for AI
agents, with bi-temporal typed facts and contradiction checking built in.

A typed, **zero-dependency** client over the Korely REST API. Every method maps
1:1 onto an endpoint, so anything you can do with curl you can do here, and the
JSON shapes in the [API reference](https://korely.ai/agents/docs/api-reference)
are the attribute shapes you get back. All the intelligence (embeddings, entity
and typed-fact extraction, contradiction checking, bi-temporal validity) runs
server-side, so your install stays small and your process stays light.

## Install

```bash
pip install korely-memory
```

Python 3.9 or later, for the SDK, the `korely` CLI and the `korely-mcp` stdio
server alike: the MCP server needs no extra package since 0.1.16
(`pip install 'korely-memory[mcp]'` still works, the extra is empty).

## Quickstart

```python
from korely_memory import Korely

korely = Korely(api_key="kor_live_...", region="eu")
# or read the key from the environment (KORELY_API_KEY)
korely = Korely(region="eu")

# Remember: the write path extracts facts and resolves contradictions
korely.add("Maria lives in Rome", user_id="maria")
korely.add("Maria moved to Milan", user_id="maria")

# Recall the raw memories, ranked by meaning. Both come back: memories are
# kept as written, it is the facts extracted from them that get superseded.
for hit in korely.search("where does Maria live", user_id="maria", limit=5):
    print(hit.id, hit.score, hit.snippet)

# One-call, prompt-ready context for your LLM. Its "Known facts" are the
# current ones, so the Rome fact, once superseded, is not among them.
# (Facts are extracted a few seconds after each write on the hosted service.)
ctx = korely.get_context("where should I send the package?", user_id="maria",
                         token_budget=800)
messages = [{"role": "system", "content": f"You are helpful.\n\n{ctx.context}"}]
```

## Methods

Every method wraps exactly one REST endpoint.

| Method | Endpoint |
|---|---|
| `add(content, *, agent_id=, user_id=, run_id=, metadata=, timestamp=)` | `POST /v1/memories` |
| `search(query, *, user_id=, agent_id=, run_id=, metadata=, limit=)` | `POST /v1/memories/search` |
| `get_all(*, user_id=, agent_id=, run_id=, limit=, offset=)` | `GET /v1/memories` |
| `get(memory_id)` | `GET /v1/memories/:id` |
| `update(memory_id, *, content, expected_updated_at=)` | `PATCH /v1/memories/:id` |
| `delete(memory_id)` | `DELETE /v1/memories/:id` |
| `delete_all(*, user_id)` | `DELETE /v1/users/:user_id/memories` |
| `history(memory_id)` | `GET /v1/memories/:id/history` |
| `users(*, agent_id=, limit=, offset=)` | `GET /v1/users` |
| `list_agents(*, limit=, offset=)` | `GET /v1/agents` |
| `delete_agent(agent_id)` | `DELETE /v1/agents/:agent_id` |
| `get_facts(*, subject=, entity=, predicate=, predicate_family=, include_invalidated=, as_of=, …)` | `GET /v1/facts` |
| `add_fact_triple(subject, predicate, object, *, user_id=, valid_from=, tense=, …)` | `POST /v1/facts` |
| `correct_fact(fact_id, *, subject=, predicate=, object=)` | `PATCH /v1/facts/:id` |
| `forget_fact(fact_id, *, at=)` | `POST /v1/facts/:id/forget` |
| `get_profile(*, user_id, agent_id=, as_of=)` | `GET /v1/profile` |
| `get_context(*, query, user_id=, agent_id=, token_budget=)` | `GET /v1/context` |
| `events(*, user_id=, status=, limit=)` | `GET /v1/events` |
| `batch(memories)` | `POST /v1/batch` |
| `batch_status(job_id)` | `GET /v1/batch/:id` |
| `ping()` | `GET /v1/ping` |
| `delete_account(*, confirm=True)` | `DELETE /v1/account` (Cloud only) |
| `Korely.init_agent(agent_caller=None, *, base_url=, …)` | `POST /v1/agents/init`, no key (Cloud only) |
| `audit(*, user_id=, action=, since=, until=, limit=, offset=)` | `GET /v1/audit` |
| `iter_audit(*, user_id=, action=, since=, until=, page_size=, offset=)` | `GET /v1/audit`, every page |

`AsyncKorely` has the same methods, awaitable (`iter_audit` is an
`async for`).

`ping()` checks a key without spending anything (no scope, no rate limit, no
quota) and answers its `tier`, `region` and `scopes`, on the Cloud and on the
Self-hosted alike.

`Korely.init_agent("my-app")` signs up for a free hobby key with no key, so
it is a class method: the answer carries the key once (`repr()` leaves it
out), then `Korely(api_key=result.api_key)`. It is what `korely init --agent`
calls. Cloud only: a Self-hosted install mints its keys in its own dashboard.

`delete_account(confirm=True)` deletes the account of the key, for good, with
every key, memory and fact of it. It is for an account `korely init --agent`
made, which nobody signs in to; one with a Korely login answers
`ConflictError` (`account_has_login`) and is closed from the app. Without
`confirm=True` nothing is sent. Cloud only: the Self-hosted answers 404 (405
where it serves its dashboard).

`audit()` reads the trail of the key's project, newest first: who acted
(`actor`), what (`action`: `read`, `write`, `fact_write`, `fact_invalidate`,
`erase`, `key_create`, `key_revoke`, and `tenant_create` on the Self-hosted;
open strings, not an enum), the `result`, the end user and the
memory or fact touched, and for a read the ids it returned, never the content.
Both products have it, and the key needs `memories:read`; it costs no quota.
`user_id=` answers an access or erasure request for one person; `since` and
`until` take ISO 8601 text, a `datetime` (UTC when it has no zone) or a
`date`. `iter_audit()` walks every page for an export:

```python
import csv, sys

out = csv.writer(sys.stdout)
for e in korely.iter_audit(user_id="maria"):
    out.writerow([e.ts, e.actor, e.action, e.result, e.target_id])
```

It pins `until` to the newest event when it starts, so events written during
the export do not shift its pages.

`add(..., timestamp="2026-01-15")` backfills the past: facts extracted inherit
the timestamp as their `valid_from`, so `as_of` point-in-time queries reflect
when things were true, not when they were ingested. Each item of `batch()` takes
the same `timestamp` key, so a migration keeps its real dates:

```python
korely.batch([
    {"content": "Franco signed up on the Pro plan.", "user_id": "franco", "timestamp": "2026-01-15"},
    {"content": "Franco downgraded to Free.", "user_id": "franco", "timestamp": "2026-06-20"},
])
```

A timestamp that is not an ISO 8601 date or datetime refuses the whole batch
with a 422 naming the item (`memories[1].timestamp`), before anything is queued.

`list_agents()` / `delete_agent(agent_id)` manage your agent namespaces: call
`list_agents()` after an `agent_cap_exceeded` error to reuse an existing
`agent_id`, or `delete_agent()` to purge a throwaway one. The page's `total`
counts the namespaces of this key's project; `used` counts the cap slots taken
across the account, which is what the 403 compares with `cap`. `delete_agent()`
raises `NotFoundError` for a name this project does not use, and its receipt's
`slot_freed` says whether the slot is free now (it is not while another project
of the account still uses the name).

`delete_all(user_id=)` answers with `memories_deleted` and `facts_deleted`, the
rows physically erased. `memories_forgotten` and `facts_invalidated` carry the
same numbers under their old names and are deprecated.

`correct_fact()` returns the new fact, whose `invalidated` lists every fact the
correction superseded (the corrected one, plus any the contradiction check
closed). A correction that restates the fact as it already stands supersedes
nothing: the same fact comes back, reconfirmed, with `invalidated == []`.

`get_context()` returns the block in `context`, and in two parts: `stable`,
the head that is the same from one call to the next (put it in the system
prompt, where the model provider's prompt cache reuses it; `stable_hash` says
when it changed), and `volatile`, the facts and memories for this question.
`degraded` is True when part of the block could not be retrieved, and
`degraded_parts` says which.

`get_facts()` returns a list of `Fact` that also carries `.total`, the number of
facts matching the filters across all pages, so `offset` knows when to stop.
`get_all()`, `get_facts()`, `users()`, `list_agents()` and `events()` take a
`limit` up to 200.

## Bi-temporal facts

The differentiator: typed `(subject, predicate, object)` facts with validity over
time. Ask what was true on any date.

```python
# Current state
facts = korely.get_facts(entity="Northwind Hosting")
print(facts[0].object)      # 50 euro per month
print(facts[0].invalid_at)  # None: active

# Point-in-time: what did we believe on June 1?
facts = korely.get_facts(entity="Northwind Hosting", as_of="2026-06-01")
print(facts[0].object)      # 40 euro per month
```

## Scoping

Three identifiers, three levels of scope, the same everywhere (SDK, REST, MCP):

- `agent_id`: your application or agent (one namespace per product surface)
- `user_id`: your end user (free-form string; **unlimited on every tier**)
- `run_id`: one session or run (sub-scope inside a user)

```python
korely.add("Asked to be contacted on Slack", agent_id="support-bot", user_id="customer-4812")
results = korely.search("contact preference", user_id="customer-4812")
```

> Always pass `user_id` on reads in multi-tenant products. Filters are additive
> (AND); a search without `user_id` spans every end user in the namespace.

## Error handling

Every error the server answers with is an `APIError` carrying the stable `code`
and the `message` of the REST error envelope (`{"code", "message"}`), so you can
branch on `err.code`; a self-hosted install that answers FastAPI's `detail` is
read the same way, and `err.body` keeps the response as it came. The common
statuses also have their own subclass. Everything subclasses `KorelyError`,
which is also what a client-side problem raises (no key, a connection error, a
timeout).

```python
import time
from korely_memory import Korely, AuthenticationError, NotFoundError, QuotaExceededError

korely = Korely(api_key="kor_live_...")
try:
    memory = korely.get("mem_8f2c1a")
except AuthenticationError:
    raise                           # 401: check or rotate the key
except NotFoundError:
    memory = None                   # 404: forgotten or never existed
except QuotaExceededError as err:   # 429
    if err.retry_after is None:
        raise                       # monthly quota used up: nothing to wait for
    time.sleep(err.retry_after)     # rate limit: wait as long as the server said
    memory = korely.get("mem_8f2c1a")
```

| Exception | Status | Typical `code` |
|---|---|---|
| `AuthenticationError` | 401 | `invalid_key` |
| `NamespaceForbiddenError` | 403 | `agent_cap_exceeded`, missing scope |
| `NotFoundError` | 404 | `not_found` |
| `ConflictError` | 409 | `account_has_login` (Cloud), `conflict` (Self-hosted) |
| `StaleWriteError` (a `ConflictError`) | 409 | `stale_write` |
| `QuotaExceededError` | 429 | `rate_limit_exceeded` (has `retry_after`), `quota_exceeded` (monthly, `retry_after` is None) |
| `TooManyBatchesError` (a `QuotaExceededError`) | 429 | `too_many_batches` (`batch()`, Cloud only: three imports still running; send again when one finishes) |
| `APIError` | any other, and the base of all of the above | `invalid_request` (422), `search_unavailable` / `model_unavailable` (503, safe to retry), `writes_paused` (503, Cloud only, has `retry_after`) |

Every `APIError` has `retry_after`: the seconds of the server's `Retry-After`
header, or None when it sent none. A rate limit sends it, and so does
`writes_paused`, the Cloud's pause of the writes that need a model once its
daily model budget is spent (until 00:00 UTC). The SDK does not retry on its
own.

## CLI

`pip install korely-memory` also installs `korely`, one command per API call,
reading the key from `KORELY_API_KEY` or from the file `korely init` saved.
Every command takes `--json` (the API's own shape; an error is a JSON object on
stderr), `--api-key` and `--base-url`; `--user-id` and `--agent-id` scope the
ones that read or write memories.

| Command | Call |
|---|---|
| `korely init [--agent] [--api-key KEY] [--force]` | `POST /v1/agents/init`, or save a key you have; `--force` to replace a saved one |
| `korely auth` / `korely ping` | `GET /v1/ping` |
| `korely add TEXT` / `korely update ID TEXT` | `POST /v1/memories` / `PATCH /v1/memories/:id` (`-` or a pipe reads stdin) |
| `korely search QUERY` | `POST /v1/memories/search` |
| `korely context QUERY` | `GET /v1/context` |
| `korely list [--limit] [--offset]` | `GET /v1/memories` |
| `korely get ID` / `korely history ID` | `GET /v1/memories/:id` / `.../history` |
| `korely events [--status error]` | `GET /v1/events` |
| `korely facts [--as-of DATE]` | `GET /v1/facts` |
| `korely add-fact SUBJECT PREDICATE OBJECT` | `POST /v1/facts` (`--valid-from`, `--tense`) |
| `korely correct-fact ID --object O` | `PATCH /v1/facts/:id` |
| `korely forget-fact ID [--at DATE]` | `POST /v1/facts/:id/forget` |
| `korely profile --user-id U` | `GET /v1/profile` |
| `korely users` | `GET /v1/users` |
| `korely agents` | `GET /v1/agents` |
| `korely delete ID` | `DELETE /v1/memories/:id` |
| `korely delete-all --user-id U --yes` | `DELETE /v1/users/:user_id/memories` |
| `korely delete-agent --agent-id A --yes` | `DELETE /v1/agents/:agent_id` |
| `korely batch FILE` / `korely batch-status JOB` | `POST /v1/batch` / `GET /v1/batch/:id` |
| `korely audit [--all]` | `GET /v1/audit` (`--action`, `--since`, `--until`) |
| `korely delete-account --yes` | `DELETE /v1/account` (Cloud only) |

`korely batch` reads a JSON array, an object with `memories`, or JSON Lines
(`-` for stdin); an item is the body of one `add`, or a string taken as its
content. `--user-id` and `--agent-id` scope the items that name none.

`korely audit --all` walks every page, for an export; with `--json` it is one
JSON document in the API's shape, streamed, so a long trail never sits in
memory. `--offset` resumes an export that stopped (pass the same `--until`).

`korely delete-account --yes` closes the account of an `init --agent` key and
removes that key from `~/.korely/config.json`, so the next `korely init` can
save a new one.

## LangGraph

```bash
pip install 'korely-memory[langgraph]'   # Python 3.10+, as LangGraph itself
```

`korely_memory.integrations.langgraph` gives a graph three ways to use Korely.
Take the ones you need: `import korely_memory` loads none of them, so the core
package keeps zero dependencies.

### Context before the model answers

`korely_context(client, user_id, query)` makes one `GET /v1/context` call and
returns the text for a `SystemMessage`: the user's current facts and the
memories relevant to the question, within `token_budget`, under a
`Current date: YYYY-MM-DD` line. In our measurements the model answers better
when its prompt carries the date; `include_date=False` leaves it out and
`today=` sets it. Write each turn back with `add()`, and the next turn finds
it, on any thread.

```python
from dataclasses import dataclass

from langchain.chat_models import init_chat_model
from langchain_core.messages import SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.runtime import Runtime

from korely_memory import Korely
from korely_memory.integrations.langgraph import korely_context

korely = Korely()                          # reads KORELY_API_KEY
model = init_chat_model("provider:model")  # any chat model LangChain supports


@dataclass
class Context:
    user_id: str


def call_model(state: MessagesState, runtime: Runtime[Context]):
    user_id = runtime.context.user_id
    question = state["messages"][-1].text
    memory = korely_context(korely, user_id, question, token_budget=800)
    reply = model.invoke([SystemMessage(memory), *state["messages"]])
    korely.add([{"role": "user", "content": question},
                {"role": "assistant", "content": reply.text}], user_id=user_id)
    return {"messages": [reply]}


builder = StateGraph(MessagesState, context_schema=Context)
builder.add_node(call_model)
builder.add_edge(START, "call_model")
graph = builder.compile(checkpointer=InMemorySaver())

graph.invoke(
    {"messages": [{"role": "user", "content": "Where should I send the package?"}]},
    {"configurable": {"thread_id": "1"}},
    context=Context(user_id="maria"),
)
```

The checkpointer keeps one thread's messages; Korely keeps what the user said
across all of them. `akorely_context()` is the same call for an `async` node.

### Tools

```python
from korely_memory.integrations.langgraph import create_korely_tools

tools = create_korely_tools(korely, user_id="maria")   # [search_memory, save_memory]
model_with_tools = model.bind_tools(tools)
```

`search_memory(query)` answers with the same block as `korely_context()`;
`save_memory(content)` stores one memory. The app binds the user (and
`agent_id=`) when it creates the tools: neither is in the tools' schema, so
the model can neither see nor change them. Create the tools per user, for
instance in the node that calls the model. A Korely error reaches the model as
the tool's answer instead of ending the run.

### Store

```python
from korely_memory.integrations.langgraph import KorelyStore

graph = builder.compile(checkpointer=InMemorySaver(), store=KorelyStore(korely))
# in a node: runtime.store.put(("memories", user_id), key, {"content": "..."})
```

`KorelyStore` is a LangGraph `BaseStore`, for code that expects one:
`runtime.store`, LangMem's memory tools. Each item is a Korely memory of the
user, so Korely extracts facts from it and `korely_context()` and the tools
find it. The memory's text is the value's `content`, `text`, `memory` or
`data` string, else one `key: value` line per field (`index=[...]` on a put,
or `index_fields=`, picks other fields). The value itself travels in the
memory's metadata and comes back exactly. Each `put` is a write like any
other: it is digested into facts and counts in your plan's writes, so the
store suits what a user said or decided, not caches or scratch state.

| Operation | On Korely |
|---|---|
| `put`, `get`, `delete` | Yes. The API has no lookup by your key, so each pages through the namespace's items, one request per 200. A `put` on an existing key stores the new memory, then forgets the old one. |
| `search(ns, query=...)` | Yes, one `POST /v1/memories/search`, with scores. `offset + limit` at most 50; `filter` takes equality on top-level fields. |
| `search(ns)` without a query | Yes, newest first, with every `filter` operator. |
| `list_namespaces`, a prefix such as `("memories",)`, `ttl`, `index=False` | No: `NotImplementedError`, before any request. |

The namespace `("memories", user_id)` is that Korely end user;
`KorelyStore(korely, agent_id="support-bot")` adds the agent, and
`namespace_to_scope=` maps other shapes (a function returning the user id or
`(user_id, agent_id)`). A search covers exactly the namespace it names, never
the ones below it. Each namespace is also a Korely run,
`run_id="langgraph:memories.maria"`: that is how the store reads its own items
and nothing else of the user's, while the rest of Korely reads them as the
user's memories. A value must fit in a memory's metadata, 8 KB of JSON on the
current server. `delete` forgets as `delete()` does; erasure is
`delete_all(user_id=)`. Two writers on one key at the same instant can leave
two memories: reads take the newest, and the next `put` or `delete` removes
the other.

## MCP server

```bash
pip install 'korely-memory[mcp]'   # Python 3.9+ since 0.1.16
```

`korely-mcp` is a stdio MCP server with four tools (`korely_get_context`,
`korely_add`, `korely_search`, `korely_get_facts`), the same four the hosted
server at `https://api.korely.ai/agent/mcp` offers, with the same arguments
(`korely_add` takes `timestamp`) and the same fact lines: a fact whose end date
is still to come reads `[until 2027-01-01]`, not `[superseded ...]`, and dates
are UTC days. It reads the key from `KORELY_API_KEY` or from the file
`korely init` saved.

## Links

- [SDK docs](https://korely.ai/agents/docs/surfaces/sdk)
- [API reference](https://korely.ai/agents/docs/api-reference)
- [Cookbook: a chatbot that remembers](https://korely.ai/agents/docs/cookbooks/chatbot-that-remembers)

MIT licensed.
