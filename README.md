# Korely Memory

Memory for AI agents that knows what is still true.

Korely stores what your agent learns as **typed facts with a validity window**. When something changes, the old fact is superseded instead of overwritten. Your agent reads the current truth, and you can still ask what was true on any past date.

```python
import time
from korely_memory import Korely

korely = Korely()  # reads KORELY_API_KEY from the environment

korely.add("Maria is on the Pro plan, billed yearly.", user_id="maria")
korely.add("Maria downgraded to Free.", user_id="maria")

time.sleep(10)  # facts are extracted server-side; see "Writes settle asynchronously"

ctx = korely.get_context(query="what plan is Maria on?", user_id="maria")
print(ctx.context)
# ## Known facts
# - Maria downgraded_to Free (since 2026-09-08)
# - Maria billing_frequency yearly (since 2026-09-08)
#
# Pro is gone from the current facts. It was superseded, not deleted.
```

The exact predicate is chosen by the extractor, so `downgraded_to` here might be
`subscribes_to` or `plan` on your run. What is guaranteed is the behaviour: the
old value leaves the current set, and stays retrievable with its `invalid_at`.

Nothing was deleted. The Pro fact is still there, carrying `invalid_at` and a pointer to what replaced it:

```python
korely.get_facts(user_id="maria", include_invalidated=True)
# Maria downgraded_to Free    valid_from=... invalid_at=None
# Maria plan Pro              valid_from=... invalid_at=2026-09-08T08:31:09Z
```

## Time travel over real dates

Pass `timestamp` when the event happened in the past, and facts inherit it as
`valid_from`. Then `as_of` answers over the world's timeline, not your ingestion
order:

```python
korely.add("Franco signed up on the Pro plan.", user_id="franco", timestamp="2026-01-15")
korely.add("Franco downgraded to Free.",        user_id="franco", timestamp="2026-06-20")

korely.get_facts(user_id="franco", as_of="2026-03-01")   # subscribes_to -> Pro plan
korely.get_facts(user_id="franco", as_of="2026-08-01")   # subscribes_to -> Free
korely.get_facts(user_id="franco")                       # subscribes_to -> Free
```

Without `timestamp` every fact starts being true the moment you write it, so
`as_of` on an earlier date returns nothing. That is correct, and usually not
what you want when you are importing history.

`as_of` answers what was true on a date, as the store knows it today.
`known_as_of` (Korely Agent 0.1.26) answers the other question: what the store
knew on that date. They differ when a change is told late. An agent learns on
5 October that a customer moved on 1 September:

```python
korely.get_facts(user_id="maria", as_of="2026-09-20")        # lives_in -> Turin
korely.get_facts(user_id="maria", known_as_of="2026-09-20")  # lives_in -> Milan
```

On 20 September the move had happened, and the store did not know it yet.
`known_as_of` is on `get_facts`, `get_profile` and `get_context`, which rebuilds
the block as the store could have built it then: what an agent could have
known when it decided. Each fact carries `closed_known_at`, when the store
learned it had ended.

## Writes settle asynchronously

`add()` returns as soon as the memory is stored, then extraction runs server-side. That means:

| Call | Available |
|---|---|
| `search()` over raw memories | immediately |
| `get_facts()` and `get_context()` typed facts | after a few seconds |

The `time.sleep(10)` above exists only so the snippet works when you paste it. **You do not need it in production**: an agent writes at the end of one turn and reads at the start of the next, and by then the facts are there.

If you do need to know exactly when, ask instead of guessing. Every memory carries a `status` of `processing`, `ready`, or `error`, and `events()` reports what is still in flight:

```python
page = korely.events()
page.processing  # 0
page.events[0]   # MemoryEvent(memory_id='mem_...', status='ready', ...)
```

`processing == 0` means none of your project's writes (in the `user_id` scope, if you pass one) is still being extracted, however many there are, so a script can wait on that one number instead of walking every id. After a `batch()` import, wait for `batch_status()` to finish first: memories the job has not stored yet are not counted.

Polling is the only signal: no webhook fires when extraction finishes. The webhook events are `memory.created`, `fact.invalidated` and `quota.warning`.

## Retrying a write

A write that got no answer may still have been made: the server may finish it
after the call gave up, and a proxy in front of it may hold it while the
server restarts and send it on later (the one of Korely Cloud holds requests
for some seconds during a restart). From Python 0.2.2 and npm 0.2.2 the
clients retry such a write once themselves: every `add()` and `batch()`
carries an `Idempotency-Key` (yours if you pass `idempotency_key`, else a new
one per write), and a write whose answer never came (a timeout, a failed
connection) is sent once more, after half a second, with the same key. Korely
1.0.27 and later make it once and answer what the first try made. Only once,
only these two writes: a read, a delete or a call the server answered (a 4xx,
a 5xx) is never sent again by the client.

If the second try does not succeed either (no answer again, or any answer
that is not a success: a 5xx, a 429, a 409 for a first try still being made),
the call raises `KorelyError` with `code == "write_not_confirmed"`, and its
message names the key and the cause. The write may
have been made: send it again with that key (made at most once within 24
hours) or look for it first. To send it again later yourself, pass your own
key:

```python
import uuid
from korely_memory import KorelyError

key = uuid.uuid4().hex  # one key per write, the same on every try of it
try:
    korely.add("Ada prefers tea.", user_id="ada", idempotency_key=key)
except KorelyError as err:
    if err.code != "write_not_confirmed":
        raise
    korely.add("Ada prefers tea.", user_id="ada", idempotency_key=key)  # made at most once
```

```ts
const key = crypto.randomUUID(); // one key per write, the same on every try of it
await korely.add("Ada prefers tea.", { user_id: "ada", idempotency_key: key });
```

`Korely(retry_writes=False)` in Python and `new Korely({ retryWrites: false })`
in npm send every write once; `korely add` and `korely batch` take
`--no-retry` and `--idempotency-key`. A key is 1 to 255 printable ASCII
characters; anything else raises (`KorelyError`) before a request is sent. A
server older than Korely 1.0.27 ignores the key: there a write whose answer
was lost after it was made is made twice by the retry, as it would be by a
retry of your own, so against such a server pass `retry_writes=False`
(`retryWrites: false`). The hooks of `korely init`, which try a session again
later, keep one key per session they send.

## Install

```bash
pip install korely-memory      # Python, plus the `korely` CLI
npm install korely-memory      # Node / TypeScript
```

Both clients have **zero runtime dependencies**.

For LangGraph, the optional `pip install 'korely-memory[langgraph]'` adds a store, memory tools and a context helper: see [python/README.md](python/README.md#langgraph).

## Get a key

A key costs nothing and needs no signup form:

```bash
curl -X POST https://api.korely.ai/v1/agents/init \
  -H 'Content-Type: application/json' \
  -d '{"agent_caller": "your-name-here"}'
```

The response carries a `kor_live_` key, when it stops working (`expires_at`) and, in `note`, the trial's limits until the account is adopted (by signing in to the dashboard with a login of your own and pasting the key on the Account page) and how to adopt it; `quotas` are the monthly numbers once adopted. Set the key as `KORELY_API_KEY` and the SDK, the CLI, and the REST API all authenticate with it. From code, `Korely.init_agent()` (Python) and `Korely.initAgent()` (JS) make the same call.

The account behind that key has no login: the key is the account. Keep it, because it is also the only way to delete the account: `korely delete-account` names the account's id, and `korely delete-account --confirm <that id>` deletes it (`delete_account(confirm="<id>")` in Python, `deleteAccount({ confirm: "<id>" })` in JS).

Or let the CLI do it for you. `korely init --agent` prints when the key ends and the server's note on the trial, saves the key and its end to `~/.korely/config.json`, and the SDK reads it from there when `KORELY_API_KEY` is not set, so this is enough to get going:

```bash
pip install korely-memory
korely init --agent --agent-caller your-name
python -c "from korely_memory import Korely; print(Korely().get_context(query='hi').tokens)"
```

## TypeScript

```ts
import { Korely } from "korely-memory";

const korely = new Korely();

await korely.add("Maria downgraded to Free.", { user_id: "maria" });
const ctx = await korely.getContext({ query: "what plan is Maria on?", user_id: "maria" });
```

With the Vercel AI SDK, `withKorelyMemory(model, { userId })` from `korely-memory/ai-sdk` gives any model this memory in one line: see [js/README.md](js/README.md#vercel-ai-sdk).

## CLI

```bash
korely init --user-id maria                  # whose memory your writes are, said once
korely add "Maria downgraded to Free."
korely context "what plan is Maria on?" --user-id maria
korely facts --as-of 2026-03-01 --user-id maria
korely audit --user-id maria --all --json > maria.json   # everything that touched Maria
```

Every write says whose memory it is: `user_id` (the end user it belongs to) or
`agent_id` (the agent writing it), or both. Korely 1.0.19 and later refuse a
write with neither, with a 422 whose code is `identity_required`, and store
nothing; the SDKs send the ids each call names.

Every command is listed in [python/README.md](python/README.md#cli).

## What Korely does

- **Typed facts.** Subject, predicate, object, extracted server-side. No prompt engineering on your side.
- **Bi-temporal validity.** Every fact carries `valid_from` and `invalid_at`, so the store separates when something was true from when it was recorded.
- **Contradiction resolution.** A new fact that conflicts with an old one supersedes it and records which fact replaced it. Nothing is silently dropped.
- **Point-in-time queries.** `as_of` answers what was true on any past date, and
  `known_as_of` what the store knew on it.
- **Entity graph.** Entities and relations are extracted automatically and available on every tier, including free.
- **Semantic retrieval.** Memories and facts are ranked by meaning (vector similarity), and facts also by how recently a memory confirmed them.
- **Prompt-ready context.** `get_context()` returns a block you can paste straight into a system prompt, with the token count.
- **EU-hosted.** Runs in Helsinki. End users can see, correct, and erase what agents remember about them.

## Async

An agent in production does not make one call at a time. `AsyncKorely` mirrors
every method of `Korely`, so everything you learned carries over:

```python
import asyncio
from korely_memory import AsyncKorely

async def main():
    korely = AsyncKorely()
    contexts = await asyncio.gather(
        korely.get_context(query="what plan?", user_id="a"),
        korely.get_context(query="what plan?", user_id="b"),
        korely.get_context(query="what plan?", user_id="c"),
    )

asyncio.run(main())
```

Six calls against the live API: **5.6s sequential, 1.7s concurrent**.

Calls run on a thread pool rather than an async HTTP library, because keeping
this package at **zero runtime dependencies** is worth more than the last drop
of efficiency. Your event loop is never blocked and requests really do overlap.

## Examples

[`examples/audit_trail.py`](examples/audit_trail.py) answers the question this
store exists for: **what did your agent know on the day it answered?**

A support agent tells a customer in March that they have priority support. In
June the customer moves to a cheaper plan. In September they complain, quoting
your bot back at you. Was the bot wrong, or right at the time?

```
  What the store believed in March, when the agent answered:
      customer-4821 · subscribes_to · Business plan
      Business plan · includes · priority support

  What is true today:
      customer-4821 · subscribes_to · Standard plan
      customer-4821 · lacks · priority support
```

Right in March, right today, and both provable. Run it yourself in about
twenty seconds:

```bash
pip install korely-memory
korely init --agent --agent-caller audit-example
python examples/audit_trail.py
```

It checks its own claims rather than making them, erasure included.

## Repository layout

| Path | Package |
|---|---|
| `python/` | [`korely-memory`](https://pypi.org/project/korely-memory/) on PyPI, includes the `korely` CLI and an MCP stdio server |
| `js/` | [`korely-memory`](https://www.npmjs.com/package/korely-memory) on npm |
| `n8n/` | [`n8n-nodes-korely`](https://www.npmjs.com/package/n8n-nodes-korely), a community node for self-hosted n8n (see its README) |

## Coding assistants

`korely init --tool claude-code` (or `codex`, or `cursor`) in a repository
connects that assistant to one memory per repository: it is handed the
memory when a session starts, and its sessions are saved to it. Every
assistant you connect shares it. See [the Python README](python/README.md#one-memory-for-your-coding-assistants).

## MCP

Korely runs a hosted MCP server, so a coding agent can read and write memory without any package:

```bash
claude mcp add --transport http korely https://api.korely.ai/agent/mcp \
  --header "Authorization: Bearer kor_live_..."
```

## Documentation

Full REST contract, concepts, and integration guides: [korely.ai/agents/docs](https://korely.ai/agents/docs)

## License

MIT. These clients are open source; the hosted service they talk to is not.
