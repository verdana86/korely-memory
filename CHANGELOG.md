# Changelog

The two clients are versioned independently: `korely-memory` on PyPI (Python,
with the `korely` CLI and the `korely-mcp` stdio server) and `korely-memory` on
npm (Node and TypeScript).

The server side of the entries marked "API" shipped on 2026-09-28. Against an
older server these clients keep working: the old field names are still read,
and a batch `timestamp` is refused there with a 422.

## Unreleased

Python:

- `Korely(ca_file=...)` (or `KORELY_CA_FILE`): the certificate authority to
  trust for https, for a server whose certificate a private CA signed, like
  Caddy's local CA on a laptop install. The Python 3.9 of macOS does not read
  `SSL_CERT_FILE`. `verify=False` turns the check off, with a warning, only as
  an argument. `AsyncKorely` takes both.
- `korely-mcp` needs nothing and runs on Python 3.9: the MCP stdio protocol is
  implemented with the standard library. It used to import `mcp`, which needs
  Python 3.10 and whose 2.0 removed the module the server used. The `[mcp]`
  extra stays, empty.

npm:

- The README documents `baseUrl` and `KORELY_BASE_URL` for a self-hosted
  install, and `NODE_EXTRA_CA_CERTS` for a private CA.
- `new Korely({ base_url })` (the Python spelling) throws instead of being
  ignored, which sent the requests to the hosted service.

## Python 0.1.15 (2026-09-28)

API:

- Errors: every 4xx and 5xx is an `APIError` (or one of its subclasses) with
  `code` and `message`, whether the server sends the hosted `{code, message}`,
  a self-hosted `{detail, code, message}`, or an older self-hosted `detail`
  alone (a sentence, a `{code, message}` pair, a validation list). `err.body`
  keeps the response as sent.
- `delete_all()`: the receipt has `memories_deleted` and `facts_deleted`.
  `memories_forgotten` and `facts_invalidated` are deprecated aliases with the
  same numbers; whichever pair the server sends fills both.
- `delete_agent()` raises `NotFoundError` for a namespace outside the key's
  project, and the receipt has `slot_freed`. In `list_agents()`, `total` counts
  this project's namespaces and `used` the cap slots of the whole account.
- `batch()`: each item takes `timestamp`, as `add()` does (new `BatchMemory`
  type). An unreadable one refuses the batch with a 422 naming
  `memories[i].timestamp`.
- `correct_fact()`: `invalidated` lists every fact the correction superseded;
  a correction that restates the fact reconfirms it and supersedes nothing.
- `get_all()` and `events()`: `limit` up to 200; `processing` counts every
  write still in flight, not only the latest 200.
- `korely-mcp`: `korely_add` takes `timestamp`, and fact lines read as on the
  hosted MCP: `[until DATE]` for an end still to come, UTC days, the current
  name of an entity, how often a fact was confirmed.
- CLI: `delete-all` prints the new counts, fact dates are UTC days, and
  `init` prints a `detail`-only error as text.

Fixes from the audit of the same day:

- The `[mcp]` extra is pinned to `mcp>=1.2.0,<2`. mcp 2.0 removed
  `mcp.server.fastmcp`, so a fresh install could not start `korely-mcp`.
- 401, 403, 404, 409 and 429 errors subclass `APIError`, so `except APIError`
  with `err.code`, as the docs teach, catches them.
- Ids are percent-encoded as one path segment: `delete_agent("bot#1")` used to
  delete `bot`. An empty id is refused before sending.
- A read timeout, a connection reset or a 200 that is not JSON raise
  `KorelyError` instead of a bare stdlib exception. `err.body` is the server's
  body again; a fractional `Retry-After` rounds up.
- `Fact` keeps `tense`, `observation_count`, `last_confirmed_at`, the
  canonical names and `source_memory_ids`; `get_facts()` returns a list with
  `.total`; `add_fact_triple()` takes `tense`.
- The docs no longer promise a webhook when extraction finishes: none fires,
  and `events()` is how to know.
- CLI: a `kor_self_` key with no address is named instead of "no API key", and
  `init --api-key` refuses a key and address that cannot work together.

## Node 0.1.7 (2026-09-28)

API:

- Errors: the same reading of `{code, message}` and of a self-hosted `detail`
  as the Python client. An older self-hosted error no longer arrives as
  "HTTP 422" with no code.
- `deleteAll()`: `memories_deleted` and `facts_deleted`, with
  `memories_forgotten` and `facts_invalidated` marked `@deprecated`; both
  pairs are filled.
- `deleteAgent()` rejects with `NotFoundError` outside the key's project and
  resolves with `slot_freed`. `AgentsPage.total` is this project,
  `AgentsPage.used` the account.
- `batch()`: new `BatchMemory` type with `timestamp`.
- `correctFact()`: `invalidated` documented as the ids superseded.
- `getAll()` and `events()`: `limit` up to 200; `processing` counts every
  write still in flight.

Fixes from the audit of the same day:

- 401, 403, 404, 409 and 429 errors subclass `APIError`.
- Ids are percent-encoded as one path segment; an empty id is refused.
- The timeout covers the response body, and a 200 that is not JSON is an
  error.
- New `events()`. `search()` takes `run_id` and `metadata`, `getAll()` takes
  `run_id` and returns an iterable page, `getFacts()` keeps `total`,
  `addFactTriple()` takes `tense`.
- `VERSION`, sent as `X-Korely-Client`, matches `package.json`. It said 0.1.1
  from 0.1.1 to 0.1.6.
