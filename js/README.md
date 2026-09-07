# korely-memory

The JavaScript / TypeScript SDK for [Korely Agents](https://korely.ai/agents) —
memory for AI agents, with a typed bi-temporal knowledge graph behind every
write. A thin, **zero-dependency** client over the Korely REST API (uses the
native `fetch`). Same package name as the Python twin on
[PyPI](https://pypi.org/project/korely-memory/).

```bash
npm install korely-memory
```

## Quickstart

```ts
import { Korely } from "korely-memory";

const korely = new Korely({ apiKey: "kor_live_..." }); // or set KORELY_API_KEY

// Remember something your agent learned about an end user
await korely.add("Maria prefers email over Slack, and her renewal is in October.", {
  user_id: "customer-4812",
});

// Later — even in a new session — pull a prompt-ready block back
const ctx = await korely.getContext({
  query: "how does Maria like to be contacted?",
  user_id: "customer-4812",
});
console.log(ctx.context); // drop straight into your system prompt
```

That's the whole loop: `add` to remember, `getContext` (or `search`) to recall.
Behind `add`, Korely extracts typed, bi-temporal facts and builds a graph — you
just hand it text.

## Methods

Every method maps to one REST endpoint.

| Method | Endpoint | |
|---|---|---|
| `add(content, opts?)` | `POST /v1/memories` | Write. `content` is a string or a list of chat messages. `opts.timestamp` (ISO) backfills the past — facts inherit it as `valid_from`. |
| `search(query, opts?)` | `POST /v1/memories/search` | Hybrid search over memories. |
| `getAll(opts?)` | `GET /v1/memories` | List a scope, newest first. |
| `get(id)` | `GET /v1/memories/:id` | One memory, with its facts. |
| `update(id, { content })` | `PATCH /v1/memories/:id` | Re-runs extraction. |
| `delete(id)` | `DELETE /v1/memories/:id` | Forget one (audited). |
| `deleteAll({ user_id })` | `DELETE /v1/users/:user_id/memories` | Forget everything for a user (GDPR). |
| `history(id)` | `GET /v1/memories/:id/history` | A memory's timeline + the facts it produced. |
| `users(opts?)` | `GET /v1/users` | Your end users, with counts. |
| `listAgents(opts?)` | `GET /v1/agents` | Your agent namespaces, with counts + the tier `cap`/`used`. Call after `agent_cap_exceeded` to reuse an id. |
| `deleteAgent(agentId)` | `DELETE /v1/agents/:agent_id` | Purge an agent namespace and free its cap slot. |
| `getFacts(opts?)` | `GET /v1/facts` | Typed facts; `as_of` for point-in-time. |
| `addFactTriple(s, p, o, opts?)` | `POST /v1/facts` | Write a fact directly (bi-temporal). |
| `getProfile({ user_id, as_of? })` | `GET /v1/profile` | The assembled profile of one end user. |
| `getContext({ query, user_id? })` | `GET /v1/context` | One call → a prompt-ready context block. |
| `batch(memories)` | `POST /v1/batch` | Bulk import, for migrations. |
| `batchStatus(jobId)` | `GET /v1/batch/:id` | Poll an import job. |

## Bi-temporal facts (the moat)

Every write extracts typed `(subject, predicate, object)` facts with validity in
time. Ask what was true on a past date:

```ts
// What did we know about this user on March 1st?
const past = await korely.getProfile({ user_id: "customer-4812", as_of: "2026-03-01" });

// Write a fact directly, dated in the past
await korely.addFactTriple("Marco", "works_at", "Acme GmbH", {
  user_id: "customer-4812",
  valid_from: "2026-06-01",
});
```

## Errors

Every error subclasses `KorelyError`, so one `catch` covers them all:

```ts
import { Korely, QuotaExceededError, AuthenticationError } from "korely-memory";

try {
  await korely.add("...", { user_id: "u" });
} catch (e) {
  if (e instanceof QuotaExceededError) {
    console.log(`Rate limited — retry after ${e.retryAfter}s`);
  } else if (e instanceof AuthenticationError) {
    console.log("Bad or missing API key");
  } else {
    throw e;
  }
}
```

`AuthenticationError` (401) · `NamespaceForbiddenError` (403) · `NotFoundError`
(404) · `StaleWriteError` (409) · `QuotaExceededError` (429, carries
`retryAfter`) · `APIError` (everything else).

## Configuration

```ts
new Korely({
  apiKey: "kor_live_...",  // or the KORELY_API_KEY env var
  region: "eu",             // EU only — data stored and processed in the EU
  timeoutMs: 30000,
});
```

Requires Node 18+ (native `fetch`), or any runtime with a global `fetch`. On
older runtimes, pass one via `{ fetch }`.

## Docs

- [SDK reference](https://korely.ai/agents/docs/surfaces/sdk)
- [REST API reference](https://korely.ai/agents/docs/api-reference)

MIT © Korely
