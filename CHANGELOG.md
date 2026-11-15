# Changelog

The two clients are versioned independently: `korely-memory` on PyPI (Python,
with the `korely` CLI and the `korely-mcp` stdio server) and `korely-memory` on
npm (Node and TypeScript).

The server side of the entries marked "API" shipped on 2026-09-28. Against an
older server these clients keep working: the old field names are still read,
and a batch `timestamp` is refused there with a 422.

## Unreleased

Code written for Python 0.2.0 or npm 0.2.0 needs no change: every addition
is sent only when asked for. One change for scripts: `korely init --tool
<tool> --no-mcp`, and `--remove --no-mcp`, no longer remove a `korely` MCP
server (the last entry of Python below).

### Python

- `search()` takes `types` and `full_text`, the two fields Korely 1.0.13
  added to `POST /v1/memories/search`: `types=["memories", "facts"]` searches
  the facts true now next to the memories (`["facts"]` them only), and
  `full_text=True` returns each result's whole text. `SearchHit` gains
  `type` (`"memory"` or `"fact"`), `content` (the whole text) and `fact`
  (the fact as `get_facts()` shows it, in a fact's result), None when the
  search did not ask. Up to 0.2.0 the SDK sent neither and dropped the
  three fields, so a hit was always the first 280 characters of a memory.
  Both are sent only when given: an older server answers 422 to them.
- `korely search` takes `--types memories,facts` (or `facts`) and
  `--full-text`: the text output prints each hit's type next to its id and
  the whole text in place of the snippet, and `--json` carries `type`,
  `content` and `fact` (null when not asked).
- `korely hook` says when a read failed. A read of the session start or of
  the first prompt that failed (a timeout, a 401 of a key that ended, no key)
  left its memory out and said nothing to the assistant, as if the memory
  were empty; only `~/.korely/hook.log` knew. In its place the context now
  has one line, `Korely memory of user_id repo:<name> was not read (<the
  error>): it is missing from this context`, with what looks like a key in
  the error masked, on one line of at most 300 characters; the other reads
  still go, and the line in `hook.log` stays. When every read fails, the assistant is now handed these lines
  where it was handed nothing.
- `Korely.key_expires_at`: when the key the client sends ends, as Korely
  1.0.23 and later say on every answer to a key that expires
  (`Korely-Key-Expires-At`). None before the first answer, for a key that
  never ends and from an older server; an answer without the header keeps
  the value.
- `korely hook` warns before the key ends: when its end is 14 days away or
  less (the window of the server's `key.expiring` webhook), the context it
  hands the assistant ends with one line saying when, and how to put a new
  key where the hooks read this one: `korely init --api-key <new key>
  --force` with the profile it was read from (`--profile <name>`, the one of
  `KORELY_PROFILE` included) and `--base-url` for a server other than
  api.korely.ai, or, for a key from `KORELY_API_KEY`, that variable. On the
  Cloud it adds that a key `korely init --agent` made needs no new key:
  signing in to the dashboard with it lifts its end. Up to 0.2.0 the end
  reached a session as 401s in `hook.log`. An end the hook cannot read
  adds nothing and takes nothing out.
- `korely init --tool <tool> --no-mcp` touches no MCP setting. Up to 0.2.0
  it removed a server named `korely` (`claude mcp remove`, or the entry in
  `.cursor/mcp.json` and `.codex/config.toml`) and added none, so a hooks-only
  install deleted a `korely` server added by hand, such as the remote one.
  Now that server stays, and the report says `MCP settings not touched
  (--no-mcp)`. With `--remove --no-mcp` the hooks are taken out and the MCP
  server stays: to take out the server an earlier `korely init --tool` added,
  run `--remove` without `--no-mcp`. A committed MCP file no longer stops a
  `--no-mcp` install, and it is not added to `.git/info/exclude`.
- `korely init --agent` says what the server answered about the key. Up to
  0.2.0 it printed "a free hobby key was minted" and the monthly quotas, and
  never that the key ends (7 days after it is made, on Korely Cloud) nor the
  trial's limits (40 writes in all, until the account is adopted), which the
  201 carries in `expires_at` and `note`. Now it prints the end (`expires
  <UTC moment> UTC`), the server's `note` whole, and the quotas as the
  numbers that apply once the account is adopted; it saves the end next to
  the key in `~/.korely/config.json` (`expires_at`, as the server wrote it).
  Adopting the account moves the end, and the saved one stays the end given
  at signup: `korely auth` says the current one (below). An end that this
  Python cannot read as a moment is printed as the server wrote it, without
  "UTC". A key replaced in a profile
  (`init --force`, `init --api-key`, `keys create --save-as`, or taken out
  by `delete-account`) no longer leaves its end behind, as its tier did not.
  `AgentInitResult` gains `expires_at` and `note` (None from a server older
  than Korely Agent 1.0.10), and `--json` prints them.
- `korely auth` prints when the key ends (`key ends <UTC moment> UTC`, and
  `key_expires_at` with `--json`), as the server says it now on the answer's
  `Korely-Key-Expires-At` header (Korely 1.0.23 and later). Up to 0.2.0 no
  command showed a key's end. No header, no line: a key that never ends and
  an older server are not told apart.
- `korely delete-account` removes the dead key from every profile of the
  config file that holds it. Up to 0.2.0 it compared only the file's top
  level, so a key saved with `--profile` stayed in its profile and every
  command of that profile was answered 401.

### npm

- `search()` takes `types` and `full_text` (the new type `SearchKind` is
  `"memories" | "facts"`), and `SearchHit` gains `type`, `content` and
  `fact`, as in Python. Up to 0.2.0 `search()` refused both options
  (`search: unknown option types.`).
- The "No API key" error says what a Node user does. Up to 0.2.0 it said to
  run `korely init`, a command this package does not have (it comes with
  the Python package). Now it says to make a key once, by hand, with the
  README's cURL (or `Korely.initAgent()` run once, since each call makes a
  new account), then to set `KORELY_API_KEY`; that a Self-hosted install
  makes its keys in its dashboard; and that a key `korely init` saved is
  read on Node, naming the file read (`$KORELY_CONFIG_HOME/config.json` when
  that variable is set), and no file where none is read (a browser).
- `AgentInitResult` types `expires_at` and `note`, which the server sends
  since Korely Agent 1.0.10: when the key stops working, and the trial's
  limits until the account is adopted.

## Python 0.2.0, npm 0.2.0 (2026-11-14)

Minor releases, after Python 0.1.29 and npm 0.1.18, because two changes break
code written for those versions:

1. **Deleting an account takes the account's id, typed:**
   `delete_account(confirm="<id>")` in Python, `deleteAccount({ confirm:
   "<id>" })` in npm, `korely delete-account --confirm <id>` on the command
   line. `confirm=True`, `{ confirm: true }` and `--yes` no longer delete
   anything, and a deletion needs Korely 1.0.23 or later on the server.
2. **The `korely` command line refuses an id it would not send:** a command
   given `--user-id` or `--agent-id` that it does not send stops with exit 2
   and does nothing. Remove the flag.

Also in both: a call that is not a `GET` is never redirected (a server or a
proxy that redirects a write now gets an error, not a silent answer from
elsewhere), and the key is never sent after a redirect. The npm package has a
new `node` export condition and its `sideEffects` changed.

### Python 0.2.0

- `delete()` and `delete_agent()` (and `AsyncKorely`'s) keep
  `facts_reopened`, which Korely 1.0.2 and later send: the facts that are
  true again because the deleted memory (or namespace) had closed them, given
  back as new rows. `DeleteReceipt` and `AgentDeleteReceipt` dropped it, so a
  program could not see them. None from an older server. `korely delete` and
  `korely delete-agent` print it (`2 fact(s) true again`) when it is above
  zero, and `--json` carries it.
- `AgentDeleteReceipt.slot_freed` is deprecated and documented as what it
  is: always None. No Korely server sends it, and none ever has; the
  docstrings promised it. It stays, so code that reads it keeps working.
  Whether a cap slot is free is `cap` and `used` of `list_agents()`.
  `korely delete-agent` no longer has a line for it (it could never print).
- The CLI's commands take `--user-id` and `--agent-id` only when they send
  them, and refuse them otherwise (exit 2, `unrecognized arguments`, nothing
  done). Every command took both, from the flags they share, and some sent
  neither: `korely delete-all --user-id U --agent-id A --yes` erased U in
  every agent of the project, the agent id unread, because the erasure of an
  end user takes no agent. Now `delete-all` takes `--user-id` alone (to purge
  one agent: `korely delete-agent --agent-id A`), `delete-agent`,
  `users` take `--agent-id` alone, `events` and `audit` `--user-id` alone,
  and `auth`, `ping`, `update`, `get`, `history`, `delete`, `correct-fact`,
  `forget-fact`, `agents`, `batch-status`, `projects`, `keys` and
  `delete-account` neither. **A script that passed an id one of these
  commands never sent stops with exit 2: remove the flag.** With `--json`,
  a command line the CLI refuses (this one, or any other) is now the JSON
  error the README promises, on stderr with exit 2:
  `{"code": "invalid_arguments", "message": "korely: unrecognized arguments:
  --agent-id A", "status": null}`; it was argparse's text.
- `korely hook session-start` and the first `prompt` (the coding
  assistants' hooks): the reads of one hook (the repository's memory, each
  `--also-read-user`, each `--also-read`) share the 8 seconds of
  `NETWORK_TIMEOUT` instead of each having 8. With a slow server a start
  with three reads kept the hook up to 24 s, past the 10 s Claude Code gives
  it, and the whole context was lost; now the reads end within about 8 s
  and the hook keeps what it read in time (measured on a Mac with a server
  answering each read in 5 s, three reads: the whole hook 15.6 s before,
  8.5 s after, with the repository's block kept). A read with less than a
  second left is not begun, and is a line in `~/.korely/hook.log`. A single
  read (the default install) is unchanged: it still gives up at 8 s. The
  timeout bounds each wait for the server, not the whole reply: a server
  that keeps sending a few bytes at a time held a read 12.2 s. A read that
  fails, the repository's included, is a line in `hook.log` and the other
  blocks still go: a failed repository read used to end the hook with no
  context, the other projects never read.
- `korely hook` masks keys made of hex digits alone before a session
  leaves the machine, as it masks the mixed-case ones: a run of 24 or more
  hex digits of one case, letters and digits mixed and spread like a random
  string, alone, joined to the rest of a run by `=`, `_`, `-` or `+`
  (`TOKEN=<hex>`, `dd_<hex>`), or after `0x`. None of 2,000 random hex keys
  was masked; now, of 2,000 each (seeded, in the tests), at least 1,800 of
  24 digits, 1,980 of 32 and all of 40 and 64. **Hex identifiers have the
  same shape and are masked too:** full git commit hashes, sha256 and md5
  digests, Docker image ids, MongoDB ObjectIds, trace ids, UUIDs written
  without dashes (a Notion page link becomes
  `https://app.notion.com/[redacted]`) and Ethereum addresses. Not masked:
  UUIDs with dashes, short hashes (under 24 hex digits), numbers. Not
  caught, listed in the README: hex keys of 20 to 23 characters, a prefix
  glued to hex digits with no separator, keys of letters alone, base32 and
  other upper-case-and-digit keys, dashed-UUID-shaped keys, keys split into
  pieces under 24 characters, some base64 with `/`, URL-encoded keys. The
  limit of five failed sends stays: after the fifth the turns are dropped
  with a line in `~/.korely/hook.log`; the README now says so.
- **`delete_account(confirm="<the account's id>")`** (and `AsyncKorely`'s)
  and **`korely delete-account --confirm <the account's id>`**: the person
  types the id of the account (`tenant_id` of `GET /admin/whoami`), as the
  dashboard has the email typed. `confirm=True` and `--yes` no longer
  delete. Korely 1.0.23's `DELETE /v1/account` wants the id so that a key
  that leaked, or a script pointed at the wrong key, is not enough with a
  flag; until now the client read the id itself and sent it, so a flag was
  enough again. Now the client sends the DELETE only when `confirm` is a
  UUID (8-4-4-4-12 hex digits) equal, spaces stripped, to the `tenant_id`
  that `GET /admin/whoami` gives the same key. A missing, `True`, `"true"`,
  non-UUID or other id sends no DELETE and raises `KorelyError` with the
  code `confirmation_required`, whose message names the id to type
  (`To delete account <id>, pass confirm="<id>"`; the CLI says `re-run it
  with --confirm <id>`) when the key may read `/admin/whoami`. A key that
  may not read it (403, a key without the `admin` scope) deletes nothing:
  with no id typed it gets that `confirmation_required`, whose message says
  `/admin/whoami` could not be read; with the id typed, the server's refusal
  of the whoami, `NamespaceForbiddenError` (an `APIError`, `status` 403,
  `code` `forbidden`). A `tenant_id` that
  is not a UUID (a whoami answering `"true"`) deletes nothing. **A script
  that ran `korely delete-account --yes` stops with exit 2
  (`unrecognized arguments: --yes`), nothing sent: pass `--confirm <id>`;
  `korely delete-account` alone prints the id.** A refused confirmation
  exits 1 with `[confirmation_required]` (with `--json`, `{"code":
  "confirmation_required", ...}` on stderr). A server older than 1.0.23
  takes only `?confirm=true` and refuses the id with a 422: the client does
  not fall back to the flag, deletes nothing and raises `APIError` with
  `status` 422 and `code` `server_too_old`, whose message says to upgrade
  the server or to delete the account with korely-memory 0.1.29 (the CLI
  exits 1 with `[server_too_old]` and keeps the key in
  `~/.korely/config.json`).
- An account is counted as deleted only on the server's answer that says
  so, `deleted: true`. A `DELETE /v1/account` answered with a redirect, or
  with a 2xx without `deleted: true` (an empty 202 of a proxy), returned a
  receipt, and `korely delete-account` removed the key from the config file
  while the account still existed (already in 0.1.29). Now it is an
  error (`APIError` with the redirect's status, or `KorelyError`), and the
  CLI keeps the key. More generally, every call takes only a 2xx as its
  answer: a 3xx that is not followed is an `APIError` with that status, no
  longer an empty result.
- A call that is not a `GET` is never redirected. urllib followed a `POST`
  answered 301, 302 or 303 as a `GET` with no body, and the answer of that
  `GET`, from wherever the redirect pointed, came back as the `POST`'s:
  `add()` could report a memory stored that no server stored. Now any
  redirect of a `POST`, `PATCH` or `DELETE` is its answer: an `APIError`
  with the redirect's status, whose message names where it pointed (the
  scheme, host and port of its `Location`, never its path or query), and
  nothing is sent there.
- The key goes only to the server the client was given. urllib copies a
  request's headers onto the request a redirect makes, so a `GET` answered
  302 by the server sent `Authorization` to whatever host, scheme or port
  the redirect named. The key is now sent as an unredirected header (as
  urllib's own `HTTPBasicAuthHandler` sends it): a `GET`'s redirect is still
  followed, never with the key, on another server or on the same one. A
  server that wants the key after a redirect answers 401
  (`AuthenticationError`); when the redirect went to another server
  (another scheme, host or port), the message says so, names that address
  and says to use it as `base_url`. Tested with two local servers. A `GET`'s
  redirect that urllib does not follow (a 300, a 304, a 308 before Python
  3.11) is an `APIError` that says so.
- `audit()`'s events keep `api_key_id`, which Korely 1.0.23 sends: the key
  that made each call, by the id the dashboard's Keys page lists it with
  (never the key); None for an act with no key and from an older server.
  `AuditEvent` dropped it, as it drops any field it does not declare: a
  field a later server adds is still left out, not an error. `korely audit
  --json` carries it. The docs and `korely audit --action` name `forget`,
  the action 1.0.23 files a memory forgotten under (its text kept), apart
  from `erase` (gone for good) and `fact_invalidate` (a fact closed); rows
  written before 1.0.23 keep `erase` for a forget.
- The examples in the package's docstrings (`import korely_memory`,
  `Korely`) name the end user in their write:
  `korely.add("User prefers TypeScript", user_id="dana",
  agent_id="coding-assistant")`. With `agent_id` alone, the write was an
  agent's note (with no `user_id` the store reads "I" as the agent), and
  since Korely 1.0.19 a write must name an id. A test runs the examples and
  checks that every write names the user.

### npm 0.2.0

- `DeleteReceipt` and `AgentDeleteReceipt` type `facts_reopened`. The field
  already reached the program at run time; a program reading it under
  `strict` did not compile.
- On Node 18, and on Node 20 before 20.16, `new Korely()` loaded with
  `import` reads the key and the server that `korely init` saved
  (`~/.korely/config.json`). It threw `No API key`: an ES module there has
  neither `process.getBuiltinModule` nor `require` to read the file with.
  The package's exports now have a `node` condition, an entry that imports
  the Node built-ins it needs (`dist/index.node.js` and `.cjs`); browsers,
  edge runtimes and bundlers for them keep the entry that imports none. The
  ES module entries share one copy of the client, so `korely-memory` and
  `korely-memory/ai-sdk` still use the same classes. `sideEffects`, which
  was `false` for the whole package, now names the Node entry, whose one
  statement (handing over the built-ins) is a side effect. esbuild, the one
  bundler tried, keeps that call when it bundles for Node and leaves the
  built-ins out when it bundles for a browser; others are not tested.
  `require()` worked before and works the same. Checked in Docker on Node
  18.20.8.
- **`deleteAccount({ confirm: "<the account's id>" })`**: the person types
  the id of the account (`tenant_id` of `GET /admin/whoami`); `{ confirm:
  true }` no longer deletes, and `confirm` is typed `string`, so TypeScript
  refuses `true` at compile time. Korely 1.0.23 wants the id so that a key
  that leaked, or a script pointed at the wrong key, is not enough with a
  flag; until now the client read the id itself and sent it. Now the client
  sends the DELETE only when `confirm` is a UUID (8-4-4-4-12 hex digits)
  equal, spaces trimmed, to the `tenant_id` that `GET /admin/whoami` gives
  the same key. A missing, `true`, `"true"`, non-UUID or other id sends no
  DELETE and rejects with a `KorelyError` whose `code` is
  `confirmation_required` and whose message names the id to type (`To
  delete account <id>, pass { confirm: "<id>" }`) when the key may read
  `/admin/whoami`. A key that may not read it (403, a key without the
  `admin` scope) deletes nothing: with no id typed it rejects with that
  `confirmation_required`, whose message says `/admin/whoami` could not be
  read; with the id typed, with the server's refusal of the whoami,
  `NamespaceForbiddenError` (an `APIError`, `status` 403, `code`
  `forbidden`). A `tenant_id` that is not a UUID deletes nothing. A
  server older than 1.0.23 takes only `?confirm=true`: the client does not
  fall back to the flag, deletes nothing and rejects with an `APIError`
  (`status` 422, `code` `server_too_old`) saying to upgrade the server or
  to use korely-memory 0.1.18.
- `deleteAccount()` counts the account as deleted only on the server's
  answer `deleted: true`: a 2xx without it (an empty 202 of a proxy)
  returned it as a receipt; now it rejects with a `KorelyError`.
- A call that is not a `GET` is never redirected: it is sent with
  `redirect: "manual"`. `fetch` followed a `POST` answered 301, 302 or 303
  (and a `DELETE` answered 303) as a `GET` with no body, and that `GET`'s
  answer came back as the call's: `add()` could resolve with a memory no
  server stored. Now any redirect of a `POST`, `PATCH` or `DELETE` rejects
  with an `APIError` with the redirect's status, whose message names where
  it pointed (the origin of its `Location`, never its path or query); in a
  browser, where `fetch` hides a redirect it does not follow, `status` is 0.
  A `GET` still follows a redirect, and Node's `fetch` drops
  `Authorization` on the way to another origin (tested with two local
  servers); its 401 there now says the call was redirected, to which
  origin, and to use it as `baseUrl`.
- `AuditEvent` types `api_key_id` (the key that made the call, Korely
  1.0.23), and `AuditAction` names `forget`, the action 1.0.23 files a
  memory forgotten under, apart from `erase`. The fields already reached
  the program at run time; a program reading `api_key_id` under `strict` did
  not compile.
- The example at the top of the client names the end user in its write
  (`{ user_id: "dana", agent_id: "coding-assistant" }`), as the README's do;
  a test runs the examples of `index.ts` and `client.ts` and checks every
  write.
- `AgentDeleteReceipt.slot_freed` is marked `@deprecated` and documented as
  always undefined: no Korely server sends it, and none ever has. It stays
  in the type, so a program reading it still compiles; read `cap` and
  `used` from `listAgents()` instead.

## Python 0.1.29, npm 0.1.18, n8n-nodes-korely 0.1.1 (2026-11-13)

For Korely 1.0.19, which refuses a write that names neither `user_id` (the
end user the memory belongs to) nor `agent_id` (the agent writing it): a 422
with the code `identity_required`, and nothing is stored. A batch with one
such item is refused whole, the item named (`memories[3]`). Older servers
accept such a write.

### Python 0.1.29

- `korely init --user-id <id>` (or `--agent-id <id>`, or both) saves whose
  memory your writes are, next to the profile's key. `korely add`,
  `korely add-fact` and `korely batch` send it when a write names neither id,
  and only with that key: a key from `KORELY_API_KEY`, `--api-key` or another
  profile may be another project's or another account's, and takes no saved
  id (the write is refused as below, with the flags to pass). With no key
  saved in the profile (the key only in `KORELY_API_KEY`), `korely init
  --user-id` saves nothing and says to save the key with it.
  `--user-id` or `--agent-id` on a command still win, and the ids given are
  the whole identity: with `user_id maria` saved, `--agent-id bot` writes as
  the agent alone. In a batch only the items that name neither id take it.
  With a key already saved, `korely init --user-id <id>` saves the id and
  nothing else; the ids of a later `init` replace the saved ones; each profile
  has its own (`korely init --profile work --agent-id bot`); an empty id is
  refused and nothing is saved. `init --name ... --user-id` and
  `init --tool ... --user-id` save the id too.
- With no id given and none saved, `add`, `add-fact` and `batch` stop before
  sending: `error: user_id or agent_id is required: ... [identity_required]`,
  exit 1, naming `--user-id <id>`, `--agent-id <id>` and
  `korely init --user-id <id>`; with `--json`, `{"code": "identity_required",
  ...}` on stderr, the code the server gives. **This is new against an older
  server too**, which stored such a write with no id: saving an id once is
  enough for both. A new account's "Next:" lines say so.
- `korely-mcp`: `korely_add` sends the `user_id` or `agent_id` the model
  passes, else the server's `--user-id`, `--repo-scope` or the new
  `--agent-id`, else the id `korely init` saved for its profile. With none of
  them it answers an error and sends nothing, and the tool's description tells
  the model to pass one. The saved id goes only with the key saved next to
  it, as on the command line: started with `KORELY_API_KEY` set to another
  key, it takes none. `korely init --tool` starts it with `--repo-scope`
  (or `--user-id`) and needs nothing; outside a git repository `--repo-scope`
  gives no id, and the `korely-<profile>` servers of `--also-read` use the id
  saved for that profile. Reads are unchanged.
- The SDK (`Korely`, `AsyncKorely`) sends the ids each call names and no
  others, and does not read the saved id: a program says whose memory each of
  its writes is. Against Korely 1.0.19, `add()`, `add_fact_triple()` and a
  `batch()` item with neither id raise `APIError` with `status` 422, `code`
  `"identity_required"` and the server's message; the docstrings say so.
- `korely hook` (the coding assistants' hooks) and the LangGraph integration
  already write with a `user_id`: unchanged.
- `~/.korely/config.json`, which holds the keys, is created readable by its
  owner alone (0600) from its first byte, in a folder made 0700 when it is
  new; it is written to a temporary file in the same folder and renamed over
  the old one, so it is whole or unchanged, and a symbolic link at its place
  is replaced, not written through. Until now a new file held the key for a
  moment with the default mode (0644 with the usual umask). The buffers of
  `korely hook` are written the same way.
- A config file that cannot be read (not JSON, or not a JSON object) is no
  longer read as empty and replaced, keys and profiles lost: `korely init`,
  `korely keys create --save-as` and `korely delete-account` stop with its
  path and what to do, before anything is done on the server. An empty file
  is still an empty config.

### npm 0.1.18

- The types and docs of `add()`, `addFactTriple()` and `batch()` say a write
  needs `user_id` or `agent_id` on Korely 1.0.19, which refuses one with
  neither: an `APIError` with `status` 422, `code === "identity_required"` and
  the server's message. The fields stay optional in the type, so a program
  that talks to an older server still compiles. Nothing else changes; the
  client sends the ids each call names. `withKorelyMemory` and `korelyTools`
  (`korely-memory/ai-sdk`) already require a `userId`.

### n8n-nodes-korely 0.1.1

- Memory Add and Fact Write need an End User ID or an Agent ID (under
  Additional Fields): with neither, the node stops before sending, naming the
  fields. A workflow that wrote with neither stops after this update, against
  an older server too, until one is filled in.
- With "Continue On Fail", a failed item carries the server's message (for a
  write with no id, which id is missing) instead of "Request failed with
  status code 422".

## Python 0.1.28 (2026-10-20)

- `korely facts`, `korely profile` and the facts printed by `add`, `update`
  and `get` start each line with the fact's id (`fct_...`), the id
  `forget-fact` and `correct-fact` take. Until now only `--json` showed it.
  A script that reads the plain listing sees one more column; `--json` is
  unchanged.

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
  /v1/users/{user_id}/names`, Korely Agent after 0.1.41). The store never
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
