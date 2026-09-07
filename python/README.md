# korely-memory

The Python SDK for [Korely Agents](https://korely.ai/agents) — memory for AI
agents, with bi-temporal typed facts and contradiction checking built in.

A typed, **zero-dependency** client over the Korely REST API. Every method maps
1:1 onto an endpoint, so anything you can do with curl you can do here, and the
JSON shapes in the [API reference](https://korely.ai/agents/docs/api-reference)
are the attribute shapes you get back. All the intelligence — embeddings, entity
and typed-fact extraction, contradiction checking, bi-temporal validity — runs
server-side, so your install stays small and your process stays light.

## Install

```bash
pip install korely-memory
```

Python 3.9 or later.

## Quickstart

```python
from korely_memory import Korely

korely = Korely(api_key="kor_live_...", region="eu")
# or read the key from the environment (KORELY_API_KEY)
korely = Korely(region="eu")

# Remember — the write path extracts facts and resolves contradictions
korely.add("User prefers TypeScript with strict mode", agent_id="coding-assistant")
korely.add("Actually I switched to Rust", agent_id="coding-assistant")

# Recall — superseded facts drop out automatically
for hit in korely.search("user's preferred language", limit=5):
    print(hit.id, hit.score, hit.snippet)

# One-call, prompt-ready context for your LLM
ctx = korely.get_context(query="plan the project", user_id="dana", token_budget=800)
messages = [{"role": "system", "content": f"You are helpful.\n\n{ctx.context}"}]
```

## Methods

Every method wraps exactly one REST endpoint.

| Method | Endpoint |
|---|---|
| `add(content, *, agent_id=, user_id=, run_id=, metadata=, timestamp=)` | `POST /v1/memories` |
| `search(query, *, user_id=, agent_id=, limit=)` | `POST /v1/memories/search` |
| `get_all(*, user_id=, agent_id=, limit=, offset=)` | `GET /v1/memories` |
| `get(memory_id)` | `GET /v1/memories/:id` |
| `update(memory_id, *, content, expected_updated_at=)` | `PATCH /v1/memories/:id` |
| `delete(memory_id)` | `DELETE /v1/memories/:id` |
| `delete_all(*, user_id)` | `DELETE /v1/users/:user_id/memories` |
| `list_agents(*, limit=, offset=)` | `GET /v1/agents` |
| `delete_agent(agent_id)` | `DELETE /v1/agents/:agent_id` |
| `get_facts(*, subject=, entity=, predicate=, predicate_family=, include_invalidated=, as_of=, …)` | `GET /v1/facts` |
| `get_context(*, query, user_id=, agent_id=, token_budget=)` | `GET /v1/context` |
| `batch(memories)` | `POST /v1/batch` |
| `batch_status(job_id)` | `GET /v1/batch/:id` |

`add(..., timestamp="2026-01-15")` backfills the past: facts extracted inherit
the timestamp as their `valid_from`, so `as_of` point-in-time queries reflect
when things were true, not when they were ingested. `list_agents()` /
`delete_agent(agent_id)` manage your agent namespaces — call `list_agents()`
after an `agent_cap_exceeded` error to reuse an existing `agent_id`, or
`delete_agent()` to purge a throwaway one and free its cap slot.

## Bi-temporal facts

The differentiator: typed `(subject, predicate, object)` facts with validity over
time. Ask what was true on any date.

```python
# Current state
facts = korely.get_facts(entity="Northwind Hosting")
print(facts[0].object)      # 50 euro per month
print(facts[0].invalid_at)  # None — active

# Point-in-time: what did we believe on June 1?
facts = korely.get_facts(entity="Northwind Hosting", as_of="2026-06-01")
print(facts[0].object)      # 40 euro per month
```

## Scoping

Three identifiers, three levels of scope — the same everywhere (SDK, REST, MCP):

- `agent_id` — your application or agent (one namespace per product surface)
- `user_id` — your end user (free-form string; **unlimited on every tier**)
- `run_id` — one session or run (sub-scope inside a user)

```python
korely.add("Asked to be contacted on Slack", agent_id="support-bot", user_id="customer-4812")
results = korely.search("contact preference", user_id="customer-4812")
```

> Always pass `user_id` on reads in multi-tenant products. Filters are additive
> (AND); a search without `user_id` spans every end user in the namespace.

## Error handling

The SDK raises typed exceptions that map onto the REST error codes; all subclass
`KorelyError`.

```python
import time
from korely_memory import Korely, AuthenticationError, NotFoundError, QuotaExceededError

korely = Korely(api_key="kor_live_...")
try:
    memory = korely.get("mem_8f2c1a")
except AuthenticationError:
    raise                       # 401 — check or rotate the key
except NotFoundError:
    memory = None               # 404 — forgotten or never existed
except QuotaExceededError as err:
    time.sleep(err.retry_after) # 429 — back off and retry
    memory = korely.get("mem_8f2c1a")
```

| Exception | Status |
|---|---|
| `AuthenticationError` | 401 |
| `NamespaceForbiddenError` | 403 |
| `NotFoundError` | 404 |
| `StaleWriteError` | 409 |
| `QuotaExceededError` (`.retry_after`) | 429 |
| `APIError` | other |

## Links

- [SDK docs](https://korely.ai/agents/docs/surfaces/sdk)
- [API reference](https://korely.ai/agents/docs/api-reference)
- [Cookbook: a chatbot that remembers](https://korely.ai/agents/docs/cookbooks/chatbot-that-remembers)

MIT licensed.
