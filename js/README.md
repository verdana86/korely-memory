# korely-memory

The JavaScript / TypeScript SDK for [Korely Agents](https://korely.ai/agents):
memory for AI agents, with typed bi-temporal facts behind every write. A thin,
**zero-dependency** client over the Korely REST API (uses the native `fetch`).
Same package name as the Python twin on
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

// Later, even in a new session, pull a prompt-ready block back
const ctx = await korely.getContext({
  query: "how does Maria like to be contacted?",
  user_id: "customer-4812",
});
console.log(ctx.context); // drop straight into your system prompt
```

That's the whole loop: `add` to remember, `getContext` (or `search`) to recall.
Behind `add`, Korely extracts typed, bi-temporal facts; you just hand it text.

## Vercel AI SDK

`korely-memory/ai-sdk` gives any [AI SDK](https://ai-sdk.dev) model (AI SDK 5,
6 or 7) a memory of each of your users. Install `ai` next to this package; the
core client never loads it.

```ts
// app/api/chat/route.ts
import {
  convertToModelMessages,
  createUIMessageStreamResponse,
  streamText,
  toUIMessageStream,
  type UIMessage,
} from "ai";
import { waitUntil } from "@vercel/functions";
import { withKorelyMemory } from "korely-memory/ai-sdk";

export async function POST(req: Request) {
  const { messages }: { messages: UIMessage[] } = await req.json();
  const userId = await currentUserId(req); // from your auth, never from the request body

  const result = streamText({
    model: withKorelyMemory("anthropic/claude-sonnet-5.5", { userId, waitUntil }),
    instructions: "You are the support assistant of Acme.",
    messages: await convertToModelMessages(messages),
  });

  return createUIMessageStreamResponse({ stream: toUIMessageStream({ stream: result.stream }) });
}
```

Before each call, the wrapper reads `getContext` for the latest user message
and adds what Korely knows as system messages after your own: first the part
that is the same on every call, so the provider's prompt cache keeps it, then
`Current date: YYYY-MM-DD` and the facts and memories this question brought.
After a reply that ends the turn, it stores the user message and the reply as
one memory, without waiting for the write (`waitUntil` keeps a serverless
function alive until it lands). If Korely cannot be reached, or takes longer
than `contextTimeoutMs` (5 s) to answer, the call goes on without memory,
`onError` is told, and an answer that arrives later is dropped.

Options: `userId` (required), `agentId`, `runId`, `client` (a configured
`Korely`, default `new Korely()`), `tokenBudget` (800), `contextTimeoutMs`
(5000; 0 waits for the client's own 30 s), `includeDate` (true), `timeZone`
("UTC"), `remember` (true), `waitUntil`, `onError`.

To let the model decide when to look something up or save it, give it the
tools instead: `tools: korelyTools({ userId })` adds `searchMemory` and
`addMemory`, and takes the same options except `remember` and `waitUntil`.
`searchMemory` also waits at most `contextTimeoutMs`, then tells the model the
memory is unavailable right now (`onError` hears of it, phase `"search"`).
Either way the user comes from your code: the model has no field to name
another one.

## Methods

Every method maps to one REST endpoint.

| Method | Endpoint | |
|---|---|---|
| `add(content, opts?)` | `POST /v1/memories` | Write. `content` is a string or a list of chat messages. `opts.timestamp` (ISO) backfills the past: facts inherit it as `valid_from`. |
| `search(query, opts?)` | `POST /v1/memories/search` | Semantic search over memories; filter by `user_id`, `agent_id`, `run_id`, `metadata`. |
| `getAll(opts?)` | `GET /v1/memories` | List a scope, newest first. The page is iterable; `limit` up to 200. |
| `get(id)` | `GET /v1/memories/:id` | One memory, with its facts. |
| `update(id, { content })` | `PATCH /v1/memories/:id` | Re-runs extraction. |
| `delete(id)` | `DELETE /v1/memories/:id` | Forget one (audited). |
| `deleteAll({ user_id })` | `DELETE /v1/users/:user_id/memories` | Erase everything for a user (GDPR). The receipt counts `memories_deleted` and `facts_deleted`; `memories_forgotten` and `facts_invalidated` are deprecated aliases. |
| `history(id)` | `GET /v1/memories/:id/history` | A memory's timeline + the facts it produced. |
| `users(opts?)` | `GET /v1/users` | Your end users, with counts. |
| `listAgents(opts?)` | `GET /v1/agents` | This project's agent namespaces, with counts. `total` counts this project's names, `used` the cap slots taken across the account. Call after `agent_cap_exceeded` to reuse an id. |
| `deleteAgent(agentId)` | `DELETE /v1/agents/:agent_id` | Purge an agent namespace of this project. `NotFoundError` for a name the project does not use; `slot_freed` says whether the cap slot is free now. |
| `getFacts(opts?)` | `GET /v1/facts` | Typed facts; `as_of` for point-in-time. The array also carries `total`. |
| `addFactTriple(s, p, o, opts?)` | `POST /v1/facts` | Write a fact directly (bi-temporal; `tense: "past"` for one that is over). |
| `correctFact(id, changes)` | `PATCH /v1/facts/:id` | Supersede a fact with a corrected one; `invalidated` lists what it closed. Restating the fact as it stands reconfirms it (`invalidated: []`). |
| `forgetFact(id, { at? })` | `POST /v1/facts/:id/forget` | Close a fact; it stays in history. |
| `getProfile({ user_id, as_of? })` | `GET /v1/profile` | The assembled profile of one end user. |
| `getContext({ query, user_id? })` | `GET /v1/context` | One call, a prompt-ready context block. |
| `events(opts?)` | `GET /v1/events` | Which writes are still being extracted; `limit` up to 200. |
| `batch(memories)` | `POST /v1/batch` | Bulk import, for migrations. Each item takes `timestamp`, as `add()` does. |
| `batchStatus(jobId)` | `GET /v1/batch/:id` | Poll an import job. |
| `ping()` | `GET /v1/ping` | Check a key without spending anything (no scope, rate limit or quota): its `tier`, `region` and `scopes`. Both products. |

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

Every error the server answers with is an `APIError` carrying the stable `code`
and the `message` of the REST error envelope (`{code, message}`; a self-hosted
install that answers FastAPI's `detail` is read the same way). The common
statuses also have their own subclass.
Everything subclasses `KorelyError`, which is also what a client-side problem
throws (no key, a connection error, a timeout).

```ts
import { Korely, APIError, QuotaExceededError } from "korely-memory";

try {
  await korely.add("...", { user_id: "u" });
} catch (e) {
  if (e instanceof QuotaExceededError && e.retryAfter !== undefined) {
    console.log(`Rate limited, retry after ${e.retryAfter}s`);
  } else if (e instanceof APIError && e.code === "invalid_key") {
    console.log("Bad or missing API key");
  } else {
    throw e;
  }
}
```

`AuthenticationError` (401) · `NamespaceForbiddenError` (403, e.g.
`agent_cap_exceeded`) · `NotFoundError` (404) · `ConflictError` (409, e.g.
`account_has_login`; `stale_write` is its subclass `StaleWriteError`) ·
`QuotaExceededError` (429: `retryAfter` is set for `rate_limit_exceeded`,
undefined for the monthly `quota_exceeded`) · `TooManyBatchesError` (a
`QuotaExceededError`: `too_many_batches` from `batch()`, Cloud only, three
imports still running; send again when one finishes) · `APIError` (the base of all of
these, and everything else: 422, 503). Every `APIError` has `retryAfter` when
the server sent `Retry-After`: a rate limit, and the Cloud's 503
`writes_paused` (writes that need a model are paused until 00:00 UTC). The SDK
does not retry on its own.

## Configuration

```ts
new Korely({
  apiKey: "kor_live_...",  // or the KORELY_API_KEY env var
  region: "eu",             // EU only: data stored and processed in the EU
  timeoutMs: 30000,         // covers the whole request, response body included
});
```

### Your own server (korely-agent)

Point the client at your install with `baseUrl`, or with the `KORELY_BASE_URL`
env var, and use the key your install issued (`kor_self_...`):

```ts
const korely = new Korely({
  apiKey: "kor_self_...",                  // or KORELY_API_KEY
  baseUrl: "https://memory.example.com",   // or KORELY_BASE_URL
});
```

The option is `baseUrl`, in camelCase. `base_url` is the Python spelling and
this SDK refuses it, rather than ignore it and send your memories to the hosted
service. A `kor_self_` key is never sent to the hosted service, and a
`kor_live_` key is never sent anywhere else.

For a server whose certificate a private CA signed (Caddy's local CA on a
laptop install), Node reads the CA from `NODE_EXTRA_CA_CERTS`:

```sh
NODE_EXTRA_CA_CERTS=/path/to/root.crt node agent.js
```

Requires Node 18+ (native `fetch`), or any runtime with a global `fetch`. On
older runtimes, pass one via `{ fetch }`.

## Docs

- [SDK reference](https://korely.ai/agents/docs/surfaces/sdk)
- [REST API reference](https://korely.ai/agents/docs/api-reference)

MIT © Korely
