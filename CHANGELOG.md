# Changelog

The two clients are versioned independently: `korely-memory` on PyPI (Python,
with the `korely` CLI and the `korely-mcp` stdio server) and `korely-memory` on
npm (Node and TypeScript).

The server side of the entries marked "API" shipped on 2026-09-28. Against an
older server these clients keep working: the old field names are still read,
and a batch `timestamp` is refused there with a 422.

## npm 0.1.17 (2026-10-19)

- `Memory.kind` ("note" or "session") in the type of what the server sends,
  and `kind` in a `batch()` item.

## npm 0.1.16 (2026-10-18)

- `korely.declareName(name, { user_id })`: declare that an end user is also
  called `name` (API: `POST /v1/users/{user_id}/names`, Korely Agent
  0.1.42). The store never merges two names of a person by itself; after the
  declaration the facts about the name read as that user's. A name declared
  for somebody else in that memory rejects with `ConflictError`.
- `add(content, { kind: "session" })`, and `exclude_sessions: true` on
  `search`, `getAll`, `getFacts`, `getProfile` and `getContext` (sent only
  when true): the work sessions apart from the notes, as in Python 0.1.26.

## Python 0.1.27 (2026-10-19)

- `Memory.kind` ("note" or "session") as the server sends it, and `kind` in
  a `batch()` item. The CLI takes `korely add --kind session` and
  `--exclude-sessions` on `search`, `context`, `facts`, `profile` and
  `list`.

- `korely init --name` can be given more than once: the full name and the
  short one the person is also called by (`--name "Tomas Radek" --name Tomas
  --user-id tomas-r`), each declared once, in order; the first refusal stops
  the rest. On the identity bench every "same person" pair then reads as
  one (24 of 24); plans closed were within noise (14 against 12 of about
  40), so it is about names, not a promise on closures.

## Python 0.1.26 (2026-10-18)

- `korely init --name "Maria Rossi" --user-id maria` declares a person's full
  name for their memory, and `Korely.declare_name(name, user_id=...)` (and
  `AsyncKorely.declare_name`) is the call behind it (API: `POST
  /v1/users/{user_id}/names`, korely-agent after 0.1.41). The store never
  merges two names of a person by itself; after the declaration the facts
  about the name read as that user's, those already written included. A name
  declared for somebody else in that memory raises `ConflictError`. `--name`
  needs `--user-id`: a repository's memory is not a person.
- The hooks of `korely init --tool` save each session with `kind="session"`,
  so a read can leave the work sessions out; a server that does not know
  the field (Korely Agent 0.1.41 and earlier) refuses it and the session is
  saved as before, a note. `add(kind=...)`, and `exclude_sessions=True` on
  `search`, `get_all`, `get_facts`, `get_profile` and `get_context` (sent
  only when true).

## Python 0.1.25 (2026-10-18)

- `korely projects` (list, create `--region eu|global`, rename, archive,
  unarchive, delete `--yes [--erase]`, a project named by id or name) and
  `korely keys` (list, create `--scopes read,write,admin --users
  founder,repo:* --expires 30|never [--save-as PROFILE]`, move, revoke
  `--yes`): the dashboard's administration from the terminal, with a key
  that holds `admin` (`--profile admin`). `--save-as` writes the new key
  into a profile and never prints it. The same calls in the client:
  `projects()`, `create_project()`, `update_project()`,
  `archive_project()`, `unarchive_project()`, `delete_project()`, `keys()`,
  `create_key(users=...)`, `revoke_key()`, `move_key()`, and in
  `AsyncKorely`.
- `korely init --api-key ""` (an empty or blank key, with or without
  `--tool`) refuses and saves nothing. It used to fall through to the
  signup and create a second account with no login.
- `korely init --tool claude-code|codex|cursor` connects a coding assistant
  to one memory per repository (`repo:<name>`, the same from a worktree),
  shared by every assistant connected: hooks hand it the memory at the
  start of a session and on its first prompt, and send the prompts and the
  final answers as one memory every five answers, before a compaction and
  at the end, from a process of their own. What looks like a key is masked
  first. `--global` for every repository, `--user-id` for one memory,
  `--no-mcp`, `--remove`. A file the repository commits is never touched.
- `korely hook <event>`: what those hooks run. It reads the event on stdin,
  prints what the tool expects and always exits 0.
- `korely-mcp --user-id <id>` and `--repo-scope`: the user_id of a call
  that names none.
- `korely-mcp --also-read-user <id>` (repeatable), and `korely init --tool
  ... --also-read-user <id>` passes it to the server as well as to the hooks:
  a context, a search or a facts read without user_id covers the
  repository's memory and each id (one block per id, one ranking for the
  search, one section per id for the facts), and the tools' descriptions
  tell the model that what holds beyond the repository goes to one of
  those ids. Before, only the hooks read them, and a tool called in the
  middle of a session saw the repository alone.
- Named profiles: `korely init --api-key ... --profile private` saves a
  second key next to the default one in `~/.korely/config.json`; every
  command, `Korely(profile=)`, `AsyncKorely(profile=)` and `korely-mcp` take
  `--profile` (or `KORELY_PROFILE`). A profile named is a decision: the
  environment's key does not override it. `korely init --tool ...
  --also-read private` makes the session start read that project too (its
  own block) and adds an MCP server `korely-private`; sessions are saved to
  the default profile only. `--also-read-user <id>` reads a user_id of the
  same project next to `repo:<name>` (a memory across repositories); the
  session is still saved to the repository. The CLI and the client read the
  config file through one function now (`profile_config`).
- `Korely(client="claude-code")` (or `KORELY_CLIENT`) names the tool
  writing, sent as `X-Korely-Client`; left out, the header names the
  library, `korely-python/<version>`. Korely Agent from 0.1.36 keeps it on
  each memory as `client`, a label no agent cap counts (an older server
  ignores the header). `korely hook` sends the assistant's name.

## npm 0.1.16 (not released)

- `new Korely({ client: "cursor" })` (or `KORELY_CLIENT`) replaces the
  library's name in `X-Korely-Client`, the header this client has always
  sent (`korely-js/<version>`). Korely Agent from 0.1.36 keeps it on each
  memory as `client`.

## Python 0.1.24 (2026-10-12)

- `korely init --processing-region eu|global`: the CLI can choose where the
  new account's memories are read by a model, as the API and both SDKs
  already could (`init_agent(processing_region="eu")`, `processingRegion:
  "eu"`). `eu` is gpt-oss-120b on Scaleway, Paris; left out, the server's
  default, Global (Gemini). It prints the region it got. With `--api-key`
  it is refused: a key you already have keeps its project's region.

## Python 0.1.23 and npm 0.1.15 (2026-10-11)

- `korely init --agent` says what creating the account accepts: the Terms
  and the Privacy Policy, with their addresses, as the Cloud names them from
  Korely Agent 0.1.27. Both clients read them on the init result
  (`terms_url`, `privacy_url`); a server older than that names none, and
  nothing is printed.
- `korely facts` and `korely profile` head a read at a moment with its own
  question: "what was true on" a date, "what the store knew on" a date, or
  what it knew on one day about another. With `--known-as-of` alone it
  printed "what was as known on".

## Python 0.1.22 and npm 0.1.14 (2026-10-11)

Both clients, the Python CLI and MCP server, and the n8n node:

- `known_as_of` (ISO date): what the store knew on a date, next to `as_of`,
  what was true on it. An agent learns in October that a customer moved in
  September: `as_of` of 20 September answers the new address, `known_as_of`
  of 20 September the old one. On `get_facts()` / `getFacts()`,
  `get_profile()` / `getProfile()` and `get_context()` / `getContext()`
  (the block as the store could have built it then), `korely facts`,
  `korely profile` and `korely context --known-as-of`, the MCP tools
  `korely_get_facts` and `korely_get_context`, and the n8n node's fact list
  and context. A fact carries `closed_known_at`, when the store learned it
  had ended; a profile echoes `known_as_of`. Korely Agent answers it from
  0.1.26 (Self-hosted and Cloud); an older server ignores the parameter and
  answers about now.

## Python 0.1.21 and npm 0.1.13 (2026-10-08)

Both clients, and the Python CLI and MCP server:

- `search()` takes `min_score` (0 to 1): the memories scored below it are
  left out, by the server, compared as the response shows the score. Absent,
  nothing changes. The Self-hosted takes it since 0.1.22 and the Cloud from
  its next release; a server without it answers 422. `korely search
  --min-score` and the MCP `korely_search` tool take it too.

## Python 0.1.20 and npm 0.1.12 (2026-10-07)

Both clients:

- `ConflictError` carries `current_fact_id` (`currentFactId` in JS) on a
  409 `fact_not_current`: `correct_fact()` of a fact that is history answers
  with the id of the fact that holds now, the one to correct instead; None or
  undefined when nothing replaced it (write the value of now with
  `add_fact_triple()`), on any other conflict, and from a server that does
  not send it. The Cloud sends it since 7 Oct 2026 (19:06), the Self-hosted
  from 0.1.21.

- `QuotaExceededError` carries the Cloud's machine fields of a monthly
  `quota_exceeded`: `limit` (the month's allowance, the plan plus the month's
  top-ups), `used` and `resets_at` (`resetsAt` in JS), None or undefined from
  a server that does not send them. The message says the same in words that
  change; these do not.

## Python 0.1.19 and npm 0.1.11 (2026-10-07)

npm:

- **An option the method does not read is refused.** In plain JavaScript an
  option spelled otherwise was dropped in silence: `search("x", { userId })`
  searched every end user of the project, `add("x", { userId })` wrote a
  memory with no end user (which `deleteAll({ user_id })` then never found),
  and `getFacts({ asOf })` answered with today's facts. Every method now
  throws a `KorelyError` before sending anything, naming the spelling it
  takes: "search: unknown option userId. Did you mean user_id?". The same in
  `korely-memory/ai-sdk`, where `agent_id` (the client's spelling) lost the
  agent's namespace, and in the constructor (`api_key`, `timeout_ms`, besides
  `base_url`). The REST API answers 422 to an unknown field and the Python
  client raises `TypeError`: the three now agree.
- In Node, `new Korely()` reads what `korely init` saved
  (`~/.korely/config.json`, or `$KORELY_CONFIG_HOME/config.json`), as the
  Python client does: an argument first, then the environment, then the file.
- The package's author address is info@korely.ai, the one the site gives.

Both clients:

- `region` was documented as "data stored and processed in the EU", which a
  Global project (the default) is not: there the model that reads a memory is
  Google's. The data is stored in the EU (Helsinki); where a memory is read is
  the project's region. `ping()` and `initAgent()` / `init_agent()` read the
  new `processing_region` ("eu" or "global"; absent from an older server), and
  a signup can choose it (`processingRegion` in JS, `processing_region` in
  Python), sent only when given. The CLI shows it in `ping`, `auth` and `init`.
- `addFactTriple()` / `add_fact_triple()` say whose fact it is: the server's
  `user_id` and `agent_id` when it answers them (since 2026-10-07), else what
  was sent. Python handed back `user_id=None` for a fact written for an end
  user.

## Python 0.1.18 and npm 0.1.10 (2026-10-06)

Both clients:

- A 409 is a `StaleWriteError` only when its code is `stale_write`. Any other
  409 is a `ConflictError` (new, and the parent of `StaleWriteError`) carrying
  the server's code: `account_has_login` on the Cloud, `conflict` on the
  Self-hosted. Every 409 used to be a `StaleWriteError`, so a refusal that has
  nothing to do with an update read as a lost write.
- Every `APIError` carries the server's `Retry-After`, in seconds
  (`retry_after` in Python, `retryAfter` in JS), whatever the status. Only a
  429 kept it, so the Cloud's 503 `writes_paused` (writes that need a model
  are paused until 00:00 UTC) lost the one number that says when to retry.
- `TooManyBatchesError`, a `QuotaExceededError`: the Cloud's 429
  `too_many_batches` from `batch()`, while three imports are still running.
  With no Retry-After it looked exactly like a monthly `quota_exceeded`, which
  the docs say to stop on, and it clears as soon as one batch finishes.
- `ping()`, `GET /v1/ping` on both products: whether a key works, and its
  `tier`, `region` and `scopes`, with no scope needed, no rate limit and no
  quota spent.
- `audit()`, `GET /v1/audit` on both products: the trail of the key's
  project, newest first, filtered by `user_id`, `action`, `since` and `until`
  (ISO text, or a `datetime` / `date` in Python and a `Date` in JS, sent as
  UTC). `actor` and `action` stay open strings: the Cloud has `dashboard`, the
  Self-hosted `manage` and `tenant_create`. An empty `user_id` or `action` is
  refused before sending, because both servers read an empty filter as none:
  `user_id=""` answered with every end user's events. `iter_audit()` (Python, `async for`
  on `AsyncKorely`) and `iterAudit()` (JS) walk every page for an export,
  with `until` pinned to the newest event when they start, so events written
  meanwhile do not shift the pages.
- `delete_account(confirm=True)` / `deleteAccount({ confirm: true })`,
  `DELETE /v1/account?confirm=true`, Cloud only: deletes the account of the
  key for good, the way to close an account `korely init --agent` made.
  Without the confirmation nothing is sent (`code` is
  `confirmation_required`, as the server's own refusal); an account with a
  login is a `ConflictError` (`account_has_login`).
- `Korely.init_agent(agent_caller)` (Python, a class method; `AsyncKorely`
  too) and `Korely.initAgent(agentCaller)` (JS, static), `POST
  /v1/agents/init` with no key, Cloud only: a free hobby key and its quotas.
  Refusals keep their code: 403 `signup_disabled`, 429 `signup_rate_limited`
  with `retry_after`. In Python the key stays out of `repr()`.
- On the Self-hosted, which has neither `/v1/account` nor `/v1/agents/init`,
  both calls answer 404, or 405 where the install serves its dashboard.

Python:

- `Context` keeps `stable`, `volatile`, `stable_hash`, `degraded` and
  `degraded_parts`, which both servers send and the JS client typed in 0.1.8:
  they were dropped, because a model keeps only the fields it declares. Each
  is None when an older server does not send it.
- `events()` returns an `EventsResponse` (`.events`, a list of `MemoryEvent`,
  and `.processing`) and `forget_fact()` a `ForgetReceipt` (`.id`, `.status`,
  `.invalid_at`, `.audit_id`): they were the only two methods answering a
  bare dict. Both are still that dict, so `r["processing"]` and `json.dumps`
  keep working.

`korely` CLI:

- `korely init` no longer replaces a key already saved in
  `~/.korely/config.json` without `--force`, and says why: the account
  `init --agent` makes has no login, so its key is the only way to use it and
  the only way to delete it. A second `init` overwrote it and left the first
  account on the server, impossible to close. The check comes before the
  signup, so a refused `init` creates no account. Saving the same key again
  needs no flag. `init --agent` now signs up through `Korely.init_agent()`.
- `korely auth` checks the key with `GET /v1/ping` and prints its tier,
  region and scopes. It called `GET /v1/users`, which needs `memories:read`
  (a write-only key failed the check) and counts as a query. `--json` has
  `tier`, `region` and `scopes` instead of `end_users`.
- `korely search` sends no `--limit` unless given, so the server applies the
  API's default, 15. It sent 10.
- New commands for the memory routes the CLI lacked: `list` (`GET
  /v1/memories`, `--limit`, `--offset`, `--run-id`), `update ID TEXT` (`PATCH`,
  stdin with `-`, `--expected-updated-at`), `history ID` and `events`
  (`--status`, `--limit`). Timelines print UTC times.
- `agents` and `delete-agent --agent-id A --yes` (`GET` / `DELETE
  /v1/agents`); `add-fact SUBJECT PREDICATE OBJECT`, `correct-fact ID` and
  `forget-fact ID [--at DATE]`, the fact writes, which the CLI could only
  read; `batch FILE` (a JSON array, `{"memories": [...]}` or JSON Lines, `-`
  for stdin; `--user-id` / `--agent-id` scope the items that name none) and
  `batch-status JOB`.
- `ping`; `audit` (`--user-id`, `--action`, `--since`, `--until`, `--limit`,
  `--offset`), with `--all` to export every page, as one streamed JSON
  document with `--json`; `delete-account --yes` (Cloud only), which also
  removes the dead key from `~/.korely/config.json` when it is the one saved
  there.

`korely-mcp`:

- `korely_search` asks for 10 hits by default, as `korely_search` does on
  both hosted MCP servers (Cloud and Self-hosted). It asked for 15, the REST
  default, so the same tool call gave a model more hits here.

## Python 0.1.17 and npm 0.1.9 (2026-10-06)

Both clients:

- An id that is not a string (or a whole number) is refused before any call,
  with a `KorelyError` that names the argument. It used to be turned into
  text: an object went out as `/v1/facts/[object Object]` and a function as
  its source, and came back as a 401 or a 404 that said nothing useful (the
  hosted product saw 5,224 of them on 2026-10-05, from automated callers right
  after the release).

## Python 0.1.16 (2026-10-05)

- LangGraph integration, `pip install 'korely-memory[langgraph]'` (Python
  3.10+, LangGraph's own floor; the core package keeps zero dependencies and
  Python 3.9). `korely_memory.integrations.langgraph` has `korely_context()`,
  the `GET /v1/context` block for a `SystemMessage` under a `Current date:`
  line; `create_korely_tools()`, `search_memory` and `save_memory` bound to an
  end user the model cannot change; and `KorelyStore`, a LangGraph `BaseStore`
  on the API. The store does `put`, `get`, `delete` and `search`; each
  namespace is a Korely run of the user, so the store reads only its own items
  while Korely extracts facts from them. `list_namespaces`, searching a prefix
  across namespaces, `ttl` and `index=False` raise `NotImplementedError`.
- `Korely(ca_file=...)` (or `KORELY_CA_FILE`): the certificate authority to
  trust for https, for a server whose certificate a private CA signed, like
  Caddy's local CA on a laptop install. The Python 3.9 of macOS does not read
  `SSL_CERT_FILE`. `verify=False` turns the check off, with a warning, only as
  an argument. `AsyncKorely` takes both.
- `korely-mcp` needs nothing and runs on Python 3.9: the MCP stdio protocol is
  implemented with the standard library. It used to import `mcp`, which needs
  Python 3.10 and whose 2.0 removed the module the server used. The `[mcp]`
  extra stays, empty.
- Found by the public conformance test of 2026-09-29 (production, docs as written):
  - `korely facts --json` answers `{"facts": [...], "total": n}` and `korely search --json` answers `{"results": [...]}`, the API's own shapes, so the documented `jq '.facts[]'` and `jq '.results[0].snippet'` work (they were bare lists).
  - With `--json`, an error is a JSON object on stderr with `code`, `message` and `status` (it was the plain line).
  - `korely add --timestamp DATE`: the facts take the date the events happened, like `add(timestamp=)` in the SDK.
  - `get_context("...", user_id=...)`: the query may be passed by position (it raised TypeError).
  - README quickstart: a city move instead of "switched to Rust", which the hosted engine does not supersede yet (being measured).

## Node 0.1.8 (2026-10-05)

- The README documents `baseUrl` and `KORELY_BASE_URL` for a self-hosted
  install, and `NODE_EXTRA_CA_CERTS` for a private CA.
- `new Korely({ base_url })` (the Python spelling) throws instead of being
  ignored, which sent the requests to the hosted service.
- `korely-memory/ai-sdk`, for the Vercel AI SDK 5, 6 and 7.
  `withKorelyMemory(model, { userId })` reads `/v1/context` for the latest
  user message before each call and adds it as system messages after the
  app's own: the stable part first, then `Current date: YYYY-MM-DD` and the
  part this question brought. It waits at most `contextTimeoutMs` (5000 ms
  by default) for that read, then goes on without it and reports the timeout
  to `onError`; the late answer is dropped. After a reply that ends the turn
  it stores the user message and the reply as one memory, without waiting
  for the write. `korelyTools({ userId })` gives the model `searchMemory`
  and `addMemory`, scoped to the user the app names; `searchMemory` also
  waits at most `contextTimeoutMs`, then tells the model the memory is
  unavailable right now and reports to `onError` (phase `search`). `ai` is
  an optional peer dependency (`^5 || ^6 || ^7`) and the core entry still
  loads nothing: with npm, an app on `ai` 4 or older needs
  `--legacy-peer-deps` to install this version.
- `Context` types the fields `/v1/context` already sends: `stable`,
  `volatile`, `stable_hash`, `degraded` and `degraded_parts`.
- `getContext("...")` takes a bare string (it answered 422), found by the public conformance test of 2026-09-29.

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
