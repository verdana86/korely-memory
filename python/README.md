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

`AsyncKorely` has the same methods, awaitable.

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
| `StaleWriteError` | 409 | `stale_write` |
| `QuotaExceededError` (`.retry_after`) | 429 | `rate_limit_exceeded` (has `retry_after`), `quota_exceeded` (monthly, `retry_after` is None) |
| `APIError` | any other, and the base of all of the above | `invalid_request` (422), `search_unavailable` / `model_unavailable` (503, safe to retry) |

The SDK does not retry on its own.

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
