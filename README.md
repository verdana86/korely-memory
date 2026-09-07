# Korely Memory

Memory for AI agents that knows what is still true.

Korely stores what your agent learns as **typed facts with a validity window**. When something changes, the old fact is superseded instead of overwritten. Your agent reads the current truth, and you can still ask what was true on any past date.

```python
from korely_memory import Korely

korely = Korely()  # reads KORELY_API_KEY from the environment

korely.add("Maria is on the Pro plan, billed yearly.", user_id="maria")
korely.add("Maria downgraded to Free.", user_id="maria")

ctx = korely.get_context(query="what plan is Maria on?", user_id="maria")
print(ctx.context)
# "The user subscribes_to Free plan (since 2026-09-05)"
```

The Pro fact is not gone. It carries `invalid_at` and a pointer to the fact that replaced it:

```python
korely.get_facts(user_id="maria", as_of="2026-03-01")
# maria subscribes_to -> Pro plan

korely.get_facts(user_id="maria")
# maria subscribes_to -> Free plan
```

## Install

```bash
pip install korely-memory      # Python, plus the `korely` CLI
npm install korely-memory      # Node / TypeScript
```

Both clients have **zero runtime dependencies**.

## Get a key

The hobby tier is free and needs no signup form:

```bash
curl -X POST https://api.korely.ai/v1/agents/init \
  -H 'Content-Type: application/json' \
  -d '{"agent_caller": "your-name-here"}'
```

The response carries a `kor_live_` key. Set it as `KORELY_API_KEY` and the SDK, the CLI, and the REST API all authenticate with it.

## TypeScript

```ts
import { Korely } from "korely-memory";

const korely = new Korely();

await korely.add("Maria downgraded to Free.", { user_id: "maria" });
const ctx = await korely.getContext({ query: "what plan is Maria on?", user_id: "maria" });
```

## CLI

```bash
korely add "Maria downgraded to Free." --user-id maria
korely context "what plan is Maria on?" --user-id maria
korely facts --as-of 2026-03-01 --user-id maria
```

## What Korely does

- **Typed facts.** Subject, predicate, object, extracted server-side. No prompt engineering on your side.
- **Bi-temporal validity.** Every fact carries `valid_from` and `invalid_at`, so the store separates when something was true from when it was recorded.
- **Contradiction resolution.** A new fact that conflicts with an old one supersedes it and records which fact replaced it. Nothing is silently dropped.
- **Point-in-time queries.** `as_of` answers what the store believed on any past date.
- **Entity graph.** Entities and relations are extracted automatically and available on every tier, including free.
- **Hybrid retrieval.** Keyword, vector, and graph signals fused for recall.
- **Prompt-ready context.** `get_context()` returns a block you can paste straight into a system prompt, with the token count.
- **EU-hosted.** Runs in Helsinki. End users can see, correct, and erase what agents remember about them.

## Repository layout

| Path | Package |
|---|---|
| `python/` | [`korely-memory`](https://pypi.org/project/korely-memory/) on PyPI, includes the `korely` CLI and an MCP stdio server |
| `js/` | [`korely-memory`](https://www.npmjs.com/package/korely-memory) on npm |

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
