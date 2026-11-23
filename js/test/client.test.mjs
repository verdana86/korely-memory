// SDK tests, no network. A fake fetch records the request and returns a canned
// response; we assert the SDK builds the right request and parses the right
// shape. Runs against the built package (dist), imported by its name as a
// program imports it (on Node, the `node` export condition), so build first:
//   npm run build && npm test
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  Korely,
  KorelyError,
  AuthenticationError,
  NotFoundError,
  NamespaceForbiddenError,
  ConflictError,
  StaleWriteError,
  QuotaExceededError,
  TooManyBatchesError,
  APIError,
  VERSION,
} from "korely-memory";

function fakeFetch(queue) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    const { status = 200, body = {}, headers = {} } = queue.shift() ?? {};
    return {
      ok: status >= 200 && status < 300,
      status,
      async text() {
        return body == null ? "" : JSON.stringify(body);
      },
      headers: { get: (k) => headers[k.toLowerCase()] ?? null },
    };
  };
  fn.calls = calls;
  return fn;
}

function client(queue) {
  const f = fakeFetch(queue);
  const k = new Korely({
    apiKey: "kor_self_test",
    baseUrl: "https://api.test",
    fetch: f,
  });
  return { k, f };
}

test("requires an api key", () => {
  // Hermetic: no variable, and a config home with nothing in it, so a key
  // saved by `korely init` on the machine running the tests does not count.
  const saved = { ...process.env };
  try {
    delete process.env.KORELY_API_KEY;
    process.env.KORELY_CONFIG_HOME = "/nonexistent-korely-config-home";
    assert.throws(() => new Korely({ fetch: async () => ({}) }), KorelyError);
    // What a Node user does (15 Nov 2026): the npm package has no `korely`
    // command, so the message does not send them to one. A key is made once,
    // as the README's cURL does: a call pasted at a program's start made a
    // new account at every start. The file named is the one read.
    assert.throws(() => new Korely({ fetch: async () => ({}) }), (e) => {
      assert.doesNotMatch(e.message, /run `korely init`/);
      assert.doesNotMatch(e.message, /await Korely\.initAgent/);
      assert.match(e.message, /make one once/);
      assert.match(e.message, /curl -X POST https:\/\/api\.korely\.ai\/v1\/agents\/init /);
      assert.match(e.message, /then set KORELY_API_KEY/);
      assert.match(e.message, /Python package/);
      assert.match(e.message, /from \/nonexistent-korely-config-home\/config\.json\./);
      assert.doesNotMatch(e.message, /~\/\.korely/);
      return true;
    });
  } finally {
    for (const v of ["KORELY_API_KEY", "KORELY_CONFIG_HOME"]) {
      if (saved[v] === undefined) delete process.env[v]; else process.env[v] = saved[v];
    }
  }
});

test("add builds POST /v1/memories", async () => {
  const { k, f } = client([{ status: 201, body: { id: "mem_1", facts: [] } }]);
  const m = await k.add("hello", { user_id: "u" });
  assert.equal(f.calls[0].init.method, "POST");
  assert.match(f.calls[0].url, /\/v1\/memories$/);
  const sent = JSON.parse(f.calls[0].init.body);
  assert.equal(sent.content, "hello");
  assert.equal(sent.user_id, "u");
  assert.equal(m.id, "mem_1");
});

test("the header names the library, or the tool given as client", async () => {
  const body = { id: "mem_1", content: "x", user_id: null, agent_id: null, run_id: null,
                 metadata: {}, facts: [] };
  const plain = fakeFetch([{ status: 201, body }]);
  await new Korely({ apiKey: "kor_live_x", fetch: plain }).add("hello");
  assert.equal(plain.calls[0].init.headers["X-Korely-Client"], `korely-js/${VERSION}`);
  const tool = fakeFetch([{ status: 201, body }]);
  await new Korely({ apiKey: "kor_live_x", fetch: tool, client: "cursor" }).add("hello");
  assert.equal(tool.calls[0].init.headers["X-Korely-Client"], "cursor");
});

test("add coerces a message list", async () => {
  const { k, f } = client([{ status: 201, body: { id: "mem_1" } }]);
  await k.add([
    { role: "user", content: "hi" },
    { role: "assistant", content: "yo" },
  ]);
  const sent = JSON.parse(f.calls[0].init.body);
  assert.equal(sent.content, "user: hi\nassistant: yo");
});

test("add rejects empty content before sending", async () => {
  const { k, f } = client([]);
  await assert.rejects(
    () => k.add([{ role: "user", content: "  " }]),
    KorelyError,
  );
  assert.equal(f.calls.length, 0);
});

test("getFacts adds include_invalidated + as_of params", async () => {
  const { k, f } = client([
    { status: 200, body: { facts: [{ id: "fct_1" }] } },
  ]);
  const facts = await k.getFacts({
    entity: "Acme",
    as_of: "2026-01-01",
    include_invalidated: true,
  });
  assert.match(f.calls[0].url, /entity=Acme/);
  assert.match(f.calls[0].url, /as_of=2026-01-01/);
  assert.match(f.calls[0].url, /include_invalidated=true/);
  assert.equal(facts[0].id, "fct_1");
});

test("known_as_of: what the store knew on a date (Korely Agent 0.1.26)", async () => {
  const { k, f } = client([
    { status: 200, body: { facts: [{ id: "fct_1", object: "Milan", closed_known_at: null }], total: 1 } },
    { status: 200, body: { user_id: "u", known_as_of: "2026-09-20", facts: [], by_family: {}, total: 0, truncated: false } },
    { status: 200, body: { context: "", tokens: 0, sources: [] } },
    { status: 200, body: { context: "", tokens: 0, sources: [] } },
  ]);
  const facts = await k.getFacts({ user_id: "maria", known_as_of: "2026-09-20" });
  assert.match(f.calls[0].url, /known_as_of=2026-09-20/);
  assert.doesNotMatch(f.calls[0].url, /[?&]as_of=/);
  assert.equal(facts[0].object, "Milan");
  const p = await k.getProfile({ user_id: "u", known_as_of: "2026-09-20" });
  assert.match(f.calls[1].url, /known_as_of=2026-09-20/);
  assert.equal(p.known_as_of, "2026-09-20");
  await k.getContext({ query: "where?", user_id: "maria", known_as_of: "2026-09-20" });
  assert.match(f.calls[2].url, /known_as_of=2026-09-20/);
  await k.getContext("where?", { known_as_of: "2026-09-20" });
  assert.match(f.calls[3].url, /known_as_of=2026-09-20/);
});

test("addFactTriple sends the triple", async () => {
  const { k, f } = client([
    {
      status: 201,
      body: { id: "fct_1", subject: "Mario", object: "Acme", invalidated: [] },
    },
  ]);
  const fact = await k.addFactTriple("Mario", "works_at", "Acme", {
    user_id: "u",
    valid_from: "2026-01-01",
  });
  const sent = JSON.parse(f.calls[0].init.body);
  assert.equal(sent.subject, "Mario");
  assert.equal(sent.object, "Acme");
  assert.equal(sent.valid_from, "2026-01-01");
  assert.equal(fact.subject, "Mario");
});

test("getProfile passes user_id + as_of", async () => {
  const { k, f } = client([
    { status: 200, body: { user_id: "u", facts: [], by_family: {}, total: 0, truncated: false } },
  ]);
  const p = await k.getProfile({ user_id: "u", as_of: "2026-03-01" });
  assert.match(f.calls[0].url, /user_id=u/);
  assert.match(f.calls[0].url, /as_of=2026-03-01/);
  assert.equal(p.total, 0);
});

test("users returns a page with total", async () => {
  const { k } = client([
    {
      status: 200,
      body: { users: [{ user_id: "maria", memories: 2, facts: 1 }], total: 1 },
    },
  ]);
  const page = await k.users();
  assert.equal(page.total, 1);
  assert.equal(page.users[0].user_id, "maria");
});

test("401 maps to AuthenticationError", async () => {
  const { k } = client([
    { status: 401, body: { code: "unauthorized", message: "bad key" } },
  ]);
  await assert.rejects(() => k.users(), AuthenticationError);
});

test("429 maps to QuotaExceededError with retryAfter", async () => {
  const { k } = client([
    {
      status: 429,
      body: { code: "quota_exceeded", message: "slow down" },
      headers: { "retry-after": "12" },
    },
  ]);
  await assert.rejects(
    () => k.add("x", { user_id: "u" }),
    (e) => {
      assert.ok(e instanceof QuotaExceededError);
      assert.equal(e.retryAfter, 12);
      return true;
    },
  );
});

test("422 maps to generic APIError", async () => {
  const { k } = client([
    { status: 422, body: { code: "invalid_request", message: "nope" } },
  ]);
  await assert.rejects(() => k.add("x", { user_id: "u" }), APIError);
});

// Il server con cui parla. KORELY_BASE_URL era letto dalla CLI e non dal
// client: chi installava Korely sulla propria macchina, esportava le due
// variabili e scriveva `new Korely()` parlava con api.korely.ai. La chiave
// veniva rifiutata con un 401, quindi non si memorizzava niente, ma la memoria
// era gia' partita nel corpo della richiesta. Per un prodotto venduto sul
// fatto che i dati restano sulla tua macchina, non e' una scomodita'.
test("KORELY_BASE_URL viene rispettato", () => {
  const prima = process.env.KORELY_BASE_URL;
  process.env.KORELY_BASE_URL = "https://203-0-113-64.nip.io";
  try {
    const k = new Korely({ apiKey: "kor_self_x", fetch: async () => ({}) });
    assert.equal(k.baseUrl, "https://203-0-113-64.nip.io");
  } finally {
    if (prima === undefined) delete process.env.KORELY_BASE_URL;
    else process.env.KORELY_BASE_URL = prima;
  }
});

test("senza la variabile resta il servizio ospitato", () => {
  // Con una chiave del servizio ospitato, si intende. Una `kor_self_` senza
  // indirizzo adesso viene fermata prima di partire, ed e' il punto.
  const prima = process.env.KORELY_BASE_URL;
  delete process.env.KORELY_BASE_URL;
  try {
    const k = new Korely({ apiKey: "kor_live_x", fetch: async () => ({}) });
    assert.equal(k.baseUrl, "https://api.korely.ai");
  } finally {
    if (prima !== undefined) process.env.KORELY_BASE_URL = prima;
  }
});

test("una chiave di casa senza indirizzo viene fermata", () => {
  // Il caso esatto che ha fatto partire una memoria verso di noi: chiave della
  // propria installazione, nessun indirizzo, e il default che vince.
  const prima = process.env.KORELY_BASE_URL;
  delete process.env.KORELY_BASE_URL;
  try {
    assert.throws(
      () => new Korely({ apiKey: "kor_self_x", fetch: async () => ({}) }),
      /kor_self_/,
    );
  } finally {
    if (prima !== undefined) process.env.KORELY_BASE_URL = prima;
  }
});

test("una chiave ospitata su una macchina altrui viene fermata", () => {
  // Lo specchio: una credenziale emessa da noi non si consegna alla macchina
  // di qualcun altro.
  assert.throws(
    () => new Korely({
      apiKey: "kor_live_x",
      baseUrl: "https://non-e-nostra.example",
      fetch: async () => ({}),
    }),
    /kor_live_/,
  );
});

test("l'opzione esplicita vince sulla variabile", () => {
  const prima = process.env.KORELY_BASE_URL;
  process.env.KORELY_BASE_URL = "https://variabile.example";
  try {
    const k = new Korely({
      apiKey: "kor_self_x",
      baseUrl: "https://esplicito.example",
      fetch: async () => ({}),
    });
    assert.equal(k.baseUrl, "https://esplicito.example");
  } finally {
    if (prima === undefined) delete process.env.KORELY_BASE_URL;
    else process.env.KORELY_BASE_URL = prima;
  }
});

// ── audit 2026-09-28: contract drift, error handling, hygiene ──────────────

test("VERSION matches package.json, and is what goes on the wire", async () => {
  // It said 0.1.1 from 0.1.1 to 0.1.6, so the X-Korely-Client header lied
  // about which build was calling.
  const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  assert.equal(VERSION, pkg.version);
  const { k, f } = client([{ status: 200, body: { users: [], total: 0 } }]);
  await k.users();
  assert.equal(f.calls[0].init.headers["X-Korely-Client"], `korely-js/${pkg.version}`);
});

test("every HTTP error is an APIError, as the public docs teach", async () => {
  // The docs say: `if (!(err instanceof APIError)) throw err;` then branch on
  // err.code. The typed errors were siblings of APIError, so a 401, a 404 or
  // a 429 was rethrown by that very line.
  const cases = [
    [401, "invalid_key", AuthenticationError],
    [403, "agent_cap_exceeded", NamespaceForbiddenError],
    [404, "not_found", NotFoundError],
    [409, "stale_write", StaleWriteError],
    [429, "quota_exceeded", QuotaExceededError],
    [503, "search_unavailable", APIError],
  ];
  for (const [status, code, cls] of cases) {
    const { k } = client([{ status, body: { code, message: "m" } }]);
    await assert.rejects(
      () => k.get("mem_1"),
      (e) => {
        assert.ok(e instanceof APIError, `${status} is not an APIError`);
        assert.ok(e instanceof cls, `${status} is not a ${cls.name}`);
        assert.ok(e instanceof KorelyError);
        assert.equal(e.code, code);
        assert.equal(e.status, status);
        return true;
      },
    );
  }
});

test("a client-side error is a KorelyError but not an APIError", async () => {
  const { k } = client([]);
  await assert.rejects(() => k.add("   "), (e) => {
    assert.ok(e instanceof KorelyError);
    assert.ok(!(e instanceof APIError));
    return true;
  });
});

test("ids are one path segment", async () => {
  // deleteAgent("bot#1") sent DELETE /v1/agents/bot: `#` starts a fragment,
  // which the request never carries, so it purged the namespace `bot`.
  const cases = [
    [(k) => k.deleteAgent("bot#1"), "/v1/agents/bot%231"],
    [(k) => k.deleteAll({ user_id: "a/b c?d" }), "/v1/users/a%2Fb%20c%3Fd/memories"],
    [(k) => k.get("mem_1/history"), "/v1/memories/mem_1%2Fhistory"],
    [(k) => k.update("m 1", { content: "x" }), "/v1/memories/m%201"],
    [(k) => k.delete("m?1"), "/v1/memories/m%3F1"],
    [(k) => k.history("m#1"), "/v1/memories/m%231/history"],
    [(k) => k.batchStatus("job#1"), "/v1/batch/job%231"],
  ];
  for (const [call, path] of cases) {
    const { k, f } = client([{ status: 200, body: {} }]);
    await call(k);
    assert.equal(new URL(f.calls[0].url).pathname, path);
  }
});

test("an empty id never reaches the server", async () => {
  for (const call of [(k) => k.get(""), (k) => k.deleteAgent(""), (k) => k.deleteAll({ user_id: "" })]) {
    const { k, f } = client([]);
    await assert.rejects(() => call(k), KorelyError);
    assert.equal(f.calls.length, 0);
  }
});

test("the timeout covers the response body, not just the headers", { timeout: 3000 }, async () => {
  // The timer was cleared when the headers arrived, so a server that sent
  // headers and stalled left resp.text() waiting forever.
  const stalls = async (_url, init) => ({
    ok: true,
    status: 200,
    headers: { get: () => null },
    text: () =>
      new Promise((_resolve, reject) => {
        init.signal.addEventListener("abort", () => reject(new Error("This operation was aborted")));
      }),
  });
  const k = new Korely({ apiKey: "kor_self_x", baseUrl: "https://api.test", fetch: stalls, timeoutMs: 50 });
  await assert.rejects(() => k.users(), (e) => {
    assert.ok(e instanceof KorelyError);
    assert.match(e.message, /timed out after 50 ms/);
    return true;
  });
});

test("a 200 that is not JSON is an error, not a result", async () => {
  const html = async () => ({
    ok: true,
    status: 200,
    headers: { get: () => null },
    text: async () => "<html>502 Bad Gateway</html>",
  });
  const k = new Korely({ apiKey: "kor_self_x", baseUrl: "https://api.test", fetch: html });
  await assert.rejects(() => k.users(), (e) => {
    assert.ok(e instanceof KorelyError);
    assert.equal(e.status, 200);
    return true;
  });
});

test("search and getAll take the filters the API takes", async () => {
  const { k, f } = client([
    { status: 200, body: { results: [] } },
    { status: 200, body: { memories: [{ id: "m1" }, { id: "m2" }], total: 9 } },
  ]);
  await k.search("q", { user_id: "u", run_id: "r1", metadata: { tier: "pro" }, min_score: 0.2 });
  const sent = JSON.parse(f.calls[0].init.body);
  assert.equal(sent.run_id, "r1");
  assert.deepEqual(sent.metadata, { tier: "pro" });
  assert.equal(sent.min_score, 0.2, "the optional floor of both servers (2026-10-07)");
  const page = await k.getAll({ user_id: "u", run_id: "r1" });
  assert.match(f.calls[1].url, /run_id=r1/);
  assert.equal(page.total, 9);
  assert.deepEqual([...page].map((m) => m.id), ["m1", "m2"]);
});

test("search takes the server's types and full_text, and sends neither unasked", async () => {
  // Korely 1.0.13 searches the facts too and returns the whole text; the SDK
  // refused both options, so a hit was always 280 characters of a memory.
  const fact = { id: "fct_1", subject: "Maria", predicate: "lives_in", object: "Turin" };
  const { k, f } = client([
    { status: 200, body: { results: [
      { id: "mem_1", score: 0.81, snippet: "x".repeat(280), type: "memory", content: "x".repeat(900) },
      { id: "fct_1", score: 0.77, snippet: "Maria · lives_in · Turin", type: "fact",
        content: "Maria · lives_in · Turin", fact },
    ] } },
    { status: 200, body: { results: [] } },
  ]);
  const hits = await k.search("where does Maria live", {
    user_id: "maria", types: ["memories", "facts"], full_text: true,
  });
  const sent = JSON.parse(f.calls[0].init.body);
  assert.deepEqual(sent.types, ["memories", "facts"]);
  assert.equal(sent.full_text, true);
  assert.deepEqual(hits.map((h) => h.type), ["memory", "fact"]);
  assert.equal(hits[0].content.length, 900);
  assert.equal(hits[1].fact.object, "Turin");
  await k.search("where does Maria live");
  const plain = JSON.parse(f.calls[1].init.body);
  assert.equal("types" in plain, false, "an older server answers 422 to it");
  assert.equal("full_text" in plain, false);
});

test("events() reads GET /v1/events", async () => {
  // The Python SDK had it; the Node one did not, and the docs told Node
  // users to call the endpoint by hand.
  const { k, f } = client([
    { status: 200, body: { events: [{ memory_id: "mem_1", status: "ready" }], processing: 0 } },
  ]);
  const out = await k.events({ user_id: "u", status: "ready", limit: 10 });
  assert.equal(f.calls[0].init.method, "GET");
  const url = new URL(f.calls[0].url);
  assert.equal(url.pathname, "/v1/events");
  assert.equal(url.searchParams.get("user_id"), "u");
  assert.equal(url.searchParams.get("status"), "ready");
  assert.equal(url.searchParams.get("limit"), "10");
  assert.equal(out.processing, 0);
  assert.equal(out.events[0].status, "ready");
});

test("getFacts keeps the total the server sends", async () => {
  const { k } = client([{ status: 200, body: { facts: [{ id: "fct_1" }], total: 57 } }]);
  const facts = await k.getFacts({ user_id: "u", limit: 1 });
  assert.ok(Array.isArray(facts));
  assert.equal(facts.length, 1);
  assert.equal(facts.total, 57);
  assert.deepEqual(Object.keys(facts), ["0"]); // total is not an enumerable element
});

test("addFactTriple can say the tense, and leaves it to the server otherwise", async () => {
  const { k, f } = client([{ status: 201, body: { id: "fct_1" } }, { status: 201, body: { id: "fct_2" } }]);
  await k.addFactTriple("u", "p", "o", { tense: "past" });
  assert.equal(JSON.parse(f.calls[0].init.body).tense, "past");
  await k.addFactTriple("u", "p", "o");
  assert.ok(!("tense" in JSON.parse(f.calls[1].init.body)));
});

test("a fractional Retry-After rounds up", async () => {
  const { k } = client([
    { status: 429, body: { code: "rate_limit_exceeded", message: "slow" }, headers: { "retry-after": "1.5" } },
  ]);
  await assert.rejects(() => k.users(), (e) => {
    assert.equal(e.retryAfter, 2);
    return true;
  });
});

test("a monthly quota 429 has no retryAfter", async () => {
  const { k } = client([{ status: 429, body: { code: "quota_exceeded", message: "limit" } }]);
  await assert.rejects(() => k.add("x", { user_id: "u" }), (e) => {
    assert.ok(e instanceof QuotaExceededError);
    assert.equal(e.retryAfter, undefined);
    return true;
  });
});

// ── the contract as deployed on 2026-09-28 evening, on the Cloud and the
//    Self-hosted ───────────────────────────────────────────────────────────────

test("errors read the hosted envelope and a self-hosted detail alike", async () => {
  // Hosted: {code, message}. Self-hosted since 2026-09-28: the same two keys
  // next to FastAPI's `detail`. Older self-hosted: `detail` alone, which
  // used to arrive as "HTTP 409" with no code.
  const cases = [
    [404, { code: "not_found", message: "No memory with that id." }, NotFoundError, "not_found", "No memory with that id."],
    [422, { detail: [{ type: "missing", loc: ["body", "content"], msg: "Field required" }], code: "invalid_request", message: "content: Field required" }, APIError, "invalid_request", "content: Field required"],
    [409, { detail: { code: "stale_write", message: "expected_updated_at does not match" } }, StaleWriteError, "stale_write", "expected_updated_at does not match"],
    [403, { detail: "API key missing required scope(s): memories:write" }, NamespaceForbiddenError, undefined, "API key missing required scope(s): memories:write"],
    [422, { detail: [{ loc: ["body", "memories", 1, "content"], msg: "String should have at least 1 character" }] }, APIError, undefined, "memories.1.content: String should have at least 1 character"],
    [422, { detail: "old words", code: "invalid_request", message: "new words" }, APIError, "invalid_request", "new words"],
    [500, {}, APIError, undefined, "HTTP 500"],
  ];
  for (const [status, body, cls, code, message] of cases) {
    const { k } = client([{ status, body }]);
    await assert.rejects(() => k.get("mem_1"), (e) => {
      assert.ok(e instanceof cls, `${status} ${JSON.stringify(body)} is not a ${cls.name}`);
      assert.equal(e.code, code);
      assert.equal(e.message, message);
      return true;
    });
  }
});

test("deleteAll reads memories_deleted / facts_deleted, and fills the deprecated names", async () => {
  const base = { user_id: "c1", erasure: "permanent", audit_id: "aud_1" };
  const both = client([{ status: 200, body: { ...base, memories_deleted: 5, facts_deleted: 3, memories_forgotten: 5, facts_invalidated: 3 } }]);
  let r = await both.k.deleteAll({ user_id: "c1" });
  assert.deepEqual([r.memories_deleted, r.facts_deleted], [5, 3]);

  // An install older than the new names: they are filled from the old ones.
  const old = client([{ status: 200, body: { ...base, memories_forgotten: 2, facts_invalidated: 1 } }]);
  r = await old.k.deleteAll({ user_id: "c1" });
  assert.deepEqual([r.memories_deleted, r.facts_deleted], [2, 1]);

  // A server that stops sending the deprecated names: zero is a count too.
  const next = client([{ status: 200, body: { ...base, memories_deleted: 4, facts_deleted: 0 } }]);
  r = await next.k.deleteAll({ user_id: "c1" });
  assert.deepEqual([r.memories_forgotten, r.facts_invalidated], [4, 0]);
});

test("listAgents: total is this project, used is the account", async () => {
  const { k } = client([{ status: 200, body: { agents: [{ agent_id: "bot", memories: 1, facts: 0, last_active: null }], total: 1, cap: 2, used: 2 } }]);
  const page = await k.listAgents();
  assert.deepEqual([page.total, page.used, page.cap], [1, 2, 2]);
  assert.deepEqual([...page].map((a) => a.agent_id), ["bot"]);
});

test("deleteAgent: the server's receipt, no slot_freed, and a 404 for a name outside the project", async () => {
  // The server's answer (no Korely server sends slot_freed): the field the
  // type keeps, deprecated, stays undefined.
  const body = { agent_id: "bot", memories_deleted: 3, facts_deleted: 1, facts_reopened: 0,
                 read_marks_deleted: 0, audit_id: "aud_1" };
  const ok = client([{ status: 200, body }]);
  const r = await ok.k.deleteAgent("bot");
  assert.equal(r.memories_deleted, 3);
  assert.equal(r.slot_freed, undefined);
  const missing = client([{ status: 404, body: { code: "not_found", message: "No agent namespace 'bot' in this project." } }]);
  await assert.rejects(() => missing.k.deleteAgent("bot"), NotFoundError);
});

test("batch items carry a timestamp, and an unreadable one names the item", async () => {
  const items = [
    { content: "Franco signed up on Pro.", user_id: "franco", timestamp: "2026-01-15" },
    { content: "Franco downgraded to Free.", user_id: "franco", timestamp: "2026-06-20T09:00:00Z" },
  ];
  const { k, f } = client([{ status: 202, body: { id: "job_1", status: "processing", received: 2 } }]);
  await k.batch(items);
  assert.deepEqual(JSON.parse(f.calls[0].init.body), { memories: items });

  const msg = "memories[1].timestamp 'yesterday' is not an ISO 8601 date or datetime (e.g. '2026-03-01' or '2026-03-01T14:30:00Z')";
  for (const body of [{ code: "invalid_request", message: msg }, { detail: msg, code: "invalid_request", message: msg }, { detail: msg }]) {
    const bad = client([{ status: 422, body }]);
    await assert.rejects(
      () => bad.k.batch([{ content: "a" }, { content: "b", timestamp: "yesterday" }]),
      (e) => {
        assert.ok(e instanceof APIError);
        assert.match(e.message, /memories\[1\]\.timestamp/);
        return true;
      },
    );
  }
});

test("correctFact returns what it superseded, and a no-op correction reconfirms", async () => {
  const { k, f } = client([
    { status: 200, body: { id: "fct_new", subject: "maria", predicate: "lives_in", object: "Rome", invalidated: ["fct_old", "fct_other"] } },
    { status: 200, body: { id: "fct_1", subject: "maria", predicate: "lives_in", object: "Milan", invalidated: [], observation_count: 2 } },
  ]);
  const fixed = await k.correctFact("fct_old", { object: "Rome" });
  assert.equal(f.calls[0].init.method, "PATCH");
  assert.deepEqual(JSON.parse(f.calls[0].init.body), { object: "Rome" });
  assert.deepEqual(fixed.invalidated, ["fct_old", "fct_other"]);
  const same = await k.correctFact("fct_1", { object: "Milan" });
  assert.equal(same.id, "fct_1");
  assert.deepEqual(same.invalidated, []);
});

test("getAll and events pass a limit of 200 through", async () => {
  const { k, f } = client([
    { status: 200, body: { memories: [], total: 0 } },
    { status: 200, body: { events: [], processing: 0 } },
  ]);
  await k.getAll({ user_id: "u", limit: 200 });
  await k.events({ status: "error", limit: 200 });
  assert.equal(new URL(f.calls[0].url).searchParams.get("limit"), "200");
  assert.equal(new URL(f.calls[1].url).searchParams.get("limit"), "200");
  assert.equal(new URL(f.calls[1].url).searchParams.get("status"), "error");
});

test("no shipped text says batch refuses timestamp or that processing counts 200", () => {
  // Both were true until 2026-09-28 and were written in the JSDoc.
  for (const rel of ["../src/client.ts", "../src/types.ts", "../README.md"]) {
    const text = readFileSync(new URL(rel, import.meta.url), "utf8");
    assert.doesNotMatch(text, /`timestamp` included/, rel);
    assert.doesNotMatch(text.replace(/\s+/g, " "), /200 most recent/, rel);
  }
});

test("base_url (the Python spelling) is refused, not ignored", () => {
  // Ignored, the requests would go to the hosted service instead of the
  // caller's own server (found by the blind installs of Korely Agent 0.1.13).
  assert.throws(
    () => new Korely({ apiKey: "kor_self_x", base_url: "https://memory.example.com" }),
    (e) => e instanceof KorelyError && /baseUrl/.test(e.message),
  );
  const k = new Korely({ apiKey: "kor_self_x", baseUrl: "https://memory.example.com/" });
  assert.equal(k.baseUrl, "https://memory.example.com");
});

test("getContext takes a bare query string (the Vercel AI SDK example, 2026-09-29)", async () => {
  const { k, f } = client([
    { status: 200, body: { context: "ok", tokens: 1, sources: [] } },
    { status: 200, body: { context: "ok", tokens: 1, sources: [] } },
  ]);
  await k.getContext("latest on the renewal");
  let u = new URL(f.calls[0].url);
  assert.equal(u.searchParams.get("query"), "latest on the renewal");
  await k.getContext("latest", { user_id: "acme" });
  u = new URL(f.calls[1].url);
  assert.equal(u.searchParams.get("user_id"), "acme");
  await assert.rejects(() => k.getContext(""), KorelyError);
});

test("a read that names no budget asks for the API's 1500 (Korely 1.0.35)", async () => {
  // The client names a budget on every read, so its default must be the
  // API's (the server's services/context.py, DEFAULT_TOKEN_BUDGET): 0.2.1
  // sent 800 and kept the old, smaller block after the server moved.
  const ok = { status: 200, body: { context: "ok", tokens: 1, sources: [] } };
  const { k, f } = client([ok, ok, ok]);
  await k.getContext("latest on the renewal");
  await k.getContext({ query: "latest", user_id: "acme" });
  await k.getContext("latest", { token_budget: 600 });
  assert.deepEqual(
    f.calls.map((c) => new URL(c.url).searchParams.get("token_budget")),
    ["1500", "1500", "600"],
  );
});


test("an id that is not a string never reaches the server", async () => {
  // A function or an object went out as the path through String():
  // "/v1/facts/[object Object]" in the hosted product's logs (2026-10-05).
  for (const bad of [{ id: "m1" }, ["m1"], () => "m1", true, 1.5]) {
    const { k, f } = client([]);
    await assert.rejects(() => k.get(bad), KorelyError);
    assert.equal(f.calls.length, 0);
  }
  const { k, f } = client([{ body: { id: "42" } }]);
  await k.get(42);
  assert.equal(f.calls[0].url, "https://api.test/v1/memories/42");
});

// ── 2026-10-06: the gaps found comparing every /v1 route of the Cloud and the
//    Self-hosted with the SDKs ─────────────────────────────────────────────────

test("only a stale_write 409 is a StaleWriteError; any other 409 is a ConflictError with its code", async () => {
  // Every 409 was a StaleWriteError. DELETE /v1/account answers 409
  // account_has_login, and the Self-hosted names an unnamed 409 `conflict`.
  const cases = [
    [{ code: "account_has_login", message: "This key belongs to an account with a Korely login." }, "account_has_login"],
    [{ detail: "a key with that name already exists", code: "conflict", message: "a key with that name already exists" }, "conflict"],
    [{ detail: "something clashed" }, undefined],
  ];
  for (const [body, code] of cases) {
    const { k } = client([{ status: 409, body }]);
    await assert.rejects(() => k.get("mem_1"), (e) => {
      assert.ok(e instanceof ConflictError, `${JSON.stringify(body)} is not a ConflictError`);
      assert.ok(e instanceof APIError);
      assert.ok(!(e instanceof StaleWriteError), `${JSON.stringify(body)} is called a stale write`);
      assert.equal(e.status, 409);
      assert.equal(e.code, code);
      return true;
    });
  }
  for (const body of [{ code: "stale_write", message: "stale" }, { detail: { code: "stale_write", message: "stale" } }]) {
    const { k } = client([{ status: 409, body }]);
    await assert.rejects(() => k.update("mem_1", { content: "x", expected_updated_at: "2000-01-01T00:00:00Z" }), (e) => {
      assert.ok(e instanceof StaleWriteError);
      assert.ok(e instanceof ConflictError);
      assert.equal(e.name, "StaleWriteError");
      assert.equal(e.code, "stale_write");
      return true;
    });
  }
});

test("a 409 fact_not_current carries the fact to correct as currentFactId", async () => {
  // 2026-10-07, both servers: the id as a field (inside `detail` on the
  // Self-hosted, either place allowed), so nobody reads it off the message.
  const bodies = [
    { detail: { code: "fact_not_current", message: "superseded by fct_new", current_fact_id: "fct_new" },
      code: "fact_not_current", message: "superseded by fct_new" },
    { code: "fact_not_current", message: "superseded by fct_new", current_fact_id: "fct_new" },
  ];
  for (const body of bodies) {
    const { k } = client([{ status: 409, body }]);
    await assert.rejects(() => k.correctFact("fct_old", { object: "Team plan" }), (e) => {
      assert.ok(e instanceof ConflictError && !(e instanceof StaleWriteError));
      assert.equal(e.code, "fact_not_current");
      assert.equal(e.currentFactId, "fct_new");
      return true;
    });
  }
  const { k } = client([{ status: 409, body: { detail: { code: "fact_not_current", message: "write it", current_fact_id: null }, code: "fact_not_current", message: "write it" } }]);
  await assert.rejects(() => k.correctFact("fct_old", { object: "Team plan" }), (e) => {
    assert.equal(e.currentFactId, undefined);
    return true;
  });
});

test("a 503 writes_paused keeps its Retry-After, like a 429", async () => {
  // The Cloud answers 503 writes_paused with Retry-After when its daily model
  // budget is spent; only a 429 kept the header.
  const { k } = client([
    { status: 503, body: { code: "writes_paused", message: "Writes that need a model are paused until 00:00 UTC." }, headers: { "retry-after": "41234" } },
    { status: 503, body: { code: "search_unavailable", message: "retry" } },
  ]);
  await assert.rejects(() => k.update("mem_1", { content: "x" }), (e) => {
    assert.ok(e instanceof APIError);
    assert.ok(!(e instanceof QuotaExceededError));
    assert.equal(e.code, "writes_paused");
    assert.equal(e.retryAfter, 41234);
    return true;
  });
  await assert.rejects(() => k.search("q"), (e) => {
    assert.equal(e.code, "search_unavailable");
    assert.equal(e.retryAfter, undefined);
    return true;
  });
});

test("too_many_batches is a TooManyBatchesError, not the monthly quota", async () => {
  // The Cloud refuses a fourth batch while three are still being imported,
  // with no Retry-After: as a plain QuotaExceededError it looked exactly like
  // the monthly quota used up.
  const { k } = client([
    { status: 429, body: { code: "too_many_batches", message: "3 batches are still being imported; wait for one to finish." } },
    { status: 429, body: { code: "quota_exceeded", message: "limit" } },
  ]);
  await assert.rejects(() => k.batch([{ content: "a" }]), (e) => {
    assert.ok(e instanceof TooManyBatchesError);
    assert.ok(e instanceof QuotaExceededError);
    assert.ok(e instanceof APIError);
    assert.equal(e.name, "TooManyBatchesError");
    assert.equal(e.code, "too_many_batches");
    assert.equal(e.retryAfter, undefined);
    return true;
  });
  await assert.rejects(() => k.batch([{ content: "a" }]), (e) => {
    assert.ok(e instanceof QuotaExceededError);
    assert.ok(!(e instanceof TooManyBatchesError));
    assert.equal(e.code, "quota_exceeded");
    return true;
  });
});

test("ping() reads GET /v1/ping, the same shape on both products", async () => {
  const { k, f } = client([
    { status: 200, body: { ok: true, tier: "hobby", region: "eu-hel1", scopes: ["memories:read", "memories:write"] } },
    { status: 401, body: { code: "invalid_key", message: "Invalid API key." } },
  ]);
  const p = await k.ping();
  assert.equal(f.calls[0].init.method, "GET");
  assert.equal(f.calls[0].url, "https://api.test/v1/ping");
  assert.equal(f.calls[0].init.body, undefined);
  assert.deepEqual(p, { ok: true, tier: "hobby", region: "eu-hel1", scopes: ["memories:read", "memories:write"] });
  await assert.rejects(() => k.ping(), AuthenticationError);
});

const auditEvent = (ts, extra = {}) => ({
  ts, actor: "rest", action: "read", result: "ok", user_id: "maria",
  target_id: null, meta: null, ip: null, read: null, ...extra,
});

test("audit() reads GET /v1/audit: filters, defaults, an iterable page", async () => {
  const { k, f } = client([
    {
      status: 200,
      body: {
        events: [
          auditEvent("2026-10-06T10:00:00.123456Z", { meta: { memories_read: 2 }, read: { memories: ["mem_1", "mem_2"], facts: ["fct_1"] } }),
          auditEvent("2026-10-06T09:00:00Z", { action: "tenant_create", actor: "manage" }),
        ],
        total: 57,
      },
    },
    { status: 200, body: { events: [], total: 0 } },
  ]);
  const page = await k.audit({ user_id: "maria", action: "read", since: "2026-10-01", until: new Date("2026-10-06T12:00:00Z"), limit: 2, offset: 4 });
  const url = new URL(f.calls[0].url);
  assert.equal(f.calls[0].init.method, "GET");
  assert.equal(url.pathname, "/v1/audit");
  assert.deepEqual(Object.fromEntries(url.searchParams), {
    user_id: "maria", action: "read", since: "2026-10-01", until: "2026-10-06T12:00:00.000Z", limit: "2", offset: "4",
  });
  assert.equal(page.total, 57);
  assert.deepEqual(page.events[0].read, { memories: ["mem_1", "mem_2"], facts: ["fct_1"] });
  // actor and action are open strings: the Self-hosted's manage / tenant_create come through
  assert.deepEqual([...page].map((e) => [e.actor, e.action]), [["rest", "read"], ["manage", "tenant_create"]]);
  await k.audit();
  assert.deepEqual(Object.fromEntries(new URL(f.calls[1].url).searchParams), { limit: "100", offset: "0" });
});

test("audit() hands over each call's key and the forget of Korely 1.0.23", async () => {
  // Rows as Korely 1.0.23 answers them (api/audit.py AuditRow); a field a
  // later server adds passes through untouched.
  const key = "6b1d4c0e-2f3a-4e5b-8c7d-9a0b1c2d3e4f";
  const { k, f } = client([{ status: 200, body: { events: [
    auditEvent("2026-11-14T15:00:00Z", { action: "forget", api_key_id: key, target_id: "mem_1" }),
    auditEvent("2026-11-14T14:00:00Z", { action: "erase", api_key_id: key, target_id: "mem_2" }),
    auditEvent("2026-11-14T13:00:00Z", { action: "fact_invalidate", api_key_id: key, target_id: "fct_1" }),
    auditEvent("2026-11-14T12:00:00Z", { action: "key_create", actor: "dashboard", api_key_id: null, later: 1 }),
  ], total: 4 } }]);
  const page = await k.audit({ action: "forget" });
  assert.equal(new URL(f.calls[0].url).searchParams.get("action"), "forget");
  assert.deepEqual(page.events.map((e) => [e.action, e.api_key_id]), [
    ["forget", key], ["erase", key], ["fact_invalidate", key], ["key_create", null]]);
  assert.equal(page.events[3].later, 1);
});

test("audit() refuses an empty filter and what is not a moment, before sending", async () => {
  // Both servers read `user_id=` as no filter: every end user's events.
  for (const opts of [{ user_id: "" }, { action: "" }, { since: 1696118400 }, { until: new Date("nope") }]) {
    const { k, f } = client([]);
    await assert.rejects(() => k.audit(opts), KorelyError);
    await assert.rejects(async () => { for await (const _ of k.iterAudit(opts)) { /* nothing */ } }, KorelyError);
    assert.equal(f.calls.length, 0, JSON.stringify(opts));
  }
});

test("iterAudit() walks every page, offset += page length, and pins until to the first page", async () => {
  const { k, f } = client([
    { status: 200, body: { events: [auditEvent("2026-10-06T10:00:00.5Z"), auditEvent("2026-10-06T09:00:00Z")], total: 5 } },
    { status: 200, body: { events: [auditEvent("2026-10-06T08:00:00Z"), auditEvent("2026-10-06T07:00:00Z")], total: 5 } },
    { status: 200, body: { events: [auditEvent("2026-10-06T06:00:00Z")], total: 5 } },
  ]);
  const seen = [];
  for await (const e of k.iterAudit({ user_id: "maria", page_size: 2 })) seen.push(e.ts.slice(11, 13));
  assert.deepEqual(seen, ["10", "09", "08", "07", "06"]);
  const params = f.calls.map((c) => Object.fromEntries(new URL(c.url).searchParams));
  assert.deepEqual(params.map((p) => p.offset), ["0", "2", "4"]);
  assert.deepEqual(params.map((p) => p.limit), ["2", "2", "2"]);
  assert.equal(params[0].until, undefined);
  assert.equal(params[1].until, "2026-10-06T10:00:00.5Z");
  assert.equal(params[2].until, "2026-10-06T10:00:00.5Z");
});

test("iterAudit() keeps an until given, resumes at an offset, stops on an empty page", async () => {
  const { k, f } = client([
    { status: 200, body: { events: [auditEvent("2026-10-05T09:00:00Z")], total: 10 } },
    { status: 200, body: { events: [], total: 10 } },
  ]);
  const seen = [];
  for await (const e of k.iterAudit({ until: "2026-10-05T12:00:00Z", offset: 3 })) seen.push(e);
  assert.equal(seen.length, 1);
  const params = f.calls.map((c) => Object.fromEntries(new URL(c.url).searchParams));
  assert.deepEqual(params.map((p) => [p.until, p.offset, p.limit]), [
    ["2026-10-05T12:00:00Z", "3", "1000"],
    ["2026-10-05T12:00:00Z", "4", "1000"],
  ]);
});

// DELETE /v1/account and GET /admin/whoami as a server answers them. "id":
// Korely 1.0.23 and later, ?confirm= must be the account's id. "flag": before
// 1.0.23, `confirm` is a boolean query parameter, so the id is a 422 of
// validation and `true` deletes. `whoami` is the answer of GET /admin/whoami.
const ACCOUNT = "3f1c9a52-7b1e-4d4a-9c2e-0b6f5d8e1a77";
const OTHER = "9d2b7c41-0a6e-4f3b-8e1d-5c4a3b2f1e09";
function fakeCloud(confirmsWith, whoami = { status: 200, body: { kind: "api_key", name: "api key", tenant_id: ACCOUNT } }) {
  const state = { calls: [], deleted: false };
  const reply = (status, body) => ({
    ok: status >= 200 && status < 300, status,
    async text() { return JSON.stringify(body); },
    headers: { get: () => null },
  });
  state.deletes = () => state.calls.filter((c) => c[0] === "DELETE").map((c) => c[2]);
  state.fetch = async (url, init) => {
    const u = new URL(url);
    state.calls.push([init.method, u.pathname, Object.fromEntries(u.searchParams)]);
    if (init.method === "GET" && u.pathname === "/admin/whoami") return reply(whoami.status, whoami.body);
    const confirm = u.searchParams.get("confirm");
    let ok;
    if (confirmsWith === "id") {
      ok = confirm === ACCOUNT;
    } else {
      const words = { true: true, 1: true, yes: true, on: true, false: false, 0: false, no: false, off: false };
      if (confirm !== null && !(confirm.toLowerCase() in words)) {
        return reply(422, { detail: [{ type: "bool_parsing", loc: ["query", "confirm"], msg: "Input should be a valid boolean" }],
                            code: "invalid_request", message: "query.confirm: Input should be a valid boolean" });
      }
      ok = words[(confirm ?? "false").toLowerCase()];
    }
    if (!ok) return reply(400, { code: "confirmation_required", message: "Send ?confirm=..." });
    state.deleted = true;
    return reply(200, { deleted: true, removed: { memories: 12, facts: 30, keys: 1 } });
  };
  return state;
}

const TYPED = `To delete account ${ACCOUNT}, pass { confirm: "${ACCOUNT}" }.`;

test("deleteAccount() deletes with the account's id typed (spaces trimmed)", async () => {
  for (const typed of [ACCOUNT, `  ${ACCOUNT}\n`]) {
    const cloud = fakeCloud("id");
    const k = new Korely({ apiKey: "kor_live_test", fetch: cloud.fetch });
    const r = await k.deleteAccount({ confirm: typed });
    assert.deepEqual(cloud.calls, [["GET", "/admin/whoami", {}], ["DELETE", "/v1/account", { confirm: ACCOUNT }]]);
    assert.equal(cloud.deleted, true);
    assert.deepEqual(r, { deleted: true, removed: { memories: 12, facts: 30, keys: 1 } });
  }
});

test("deleteAccount(): no flag and no other id sends the DELETE, and the error names the id", async () => {
  const spellings = [undefined, {}, { confirm: undefined }, { confirm: null }, { confirm: false }, { confirm: true },
    { confirm: "true" }, { confirm: "True" }, { confirm: "1" }, { confirm: 1 }, { confirm: "yes" }, { confirm: "" },
    { confirm: "   " }, { confirm: "not-an-account" }, { confirm: ACCOUNT.replaceAll("-", "") },
    { confirm: `{${ACCOUNT}}` }, { confirm: `urn:uuid:${ACCOUNT}` }, { confirm: ACCOUNT.toUpperCase() },
    { confirm: [ACCOUNT] }, { confirm: OTHER }];
  for (const opts of spellings) {
    for (const server of ["id", "flag"]) {
      const cloud = fakeCloud(server);
      const k = new Korely({ apiKey: "kor_live_test", fetch: cloud.fetch });
      await assert.rejects(() => k.deleteAccount(opts), (e) => {
        assert.ok(e instanceof KorelyError);
        assert.ok(!(e instanceof APIError));
        assert.equal(e.code, "confirmation_required");
        assert.ok(e.message.toLowerCase().includes("nothing was deleted"), e.message);
        assert.ok(e.message.includes(TYPED), e.message);
        return true;
      }, JSON.stringify(opts) + " " + server);
      assert.deepEqual(cloud.deletes(), [], JSON.stringify(opts));
      assert.equal(cloud.deleted, false);
    }
  }
});

test("deleteAccount(): the id of another account says whose the key is", async () => {
  const cloud = fakeCloud("id");
  const k = new Korely({ apiKey: "kor_live_test", fetch: cloud.fetch });
  await assert.rejects(() => k.deleteAccount({ confirm: OTHER }), (e) =>
    e.message.includes(`The account of this key is ${ACCOUNT}, and the confirmation names ${OTHER}`));
  assert.deepEqual(cloud.calls, [["GET", "/admin/whoami", {}]]);
});

test("deleteAccount(): a whoami with no UUID never becomes the confirmation", async () => {
  // A tenant_id of "true" sent as the id deletes on a server before 1.0.23.
  for (const body of [{ tenant_id: "true" }, { tenant_id: "1" }, { tenant_id: "  " }, { tenant_id: 123 },
    { tenant_id: [ACCOUNT] }, { kind: "api_key" }, [ACCOUNT], {}]) {
    for (const typed of [true, "true", ACCOUNT, undefined]) {
      const cloud = fakeCloud("flag", { status: 200, body });
      const k = new Korely({ apiKey: "kor_live_test", fetch: cloud.fetch });
      await assert.rejects(() => k.deleteAccount({ confirm: typed }), (e) => e.code === "confirmation_required");
      assert.deepEqual(cloud.deletes(), [], JSON.stringify([body, typed]));
      assert.equal(cloud.deleted, false);
    }
  }
});

test("deleteAccount(): a key that may not read its account deletes nothing", async () => {
  const refused = { status: 403, body: { code: "forbidden", message: "needs the admin scope" } };
  let cloud = fakeCloud("id", refused);
  let k = new Korely({ apiKey: "kor_live_test", fetch: cloud.fetch });
  await assert.rejects(() => k.deleteAccount({ confirm: ACCOUNT }), (e) => e instanceof NamespaceForbiddenError && e.status === 403);
  assert.deepEqual(cloud.calls, [["GET", "/admin/whoami", {}]]);
  // Without the id the refusal is the confirmation's, and says why it cannot name the id.
  cloud = fakeCloud("id", refused);
  k = new Korely({ apiKey: "kor_live_test", fetch: cloud.fetch });
  await assert.rejects(() => k.deleteAccount({ confirm: true }), (e) =>
    e.code === "confirmation_required" && e.message.includes("could not read: needs the admin scope"));
  assert.deepEqual(cloud.deletes(), []);
});

test("deleteAccount(): only the answer deleted: true is a deletion", async () => {
  // A redirect the fetch did not follow, or a 2xx without `deleted: true`.
  const whoami = { status: 200, body: { kind: "api_key", tenant_id: ACCOUNT } };
  for (const [status, body] of [[301, {}], [302, {}], [303, {}], [307, {}], [308, {}], [200, {}], [202, null],
    [204, null], [200, { deleted: false }], [200, { deleted: "true" }], [200, { removed: { keys: 1 } }]]) {
    const { k, f } = client([whoami, { status, body }]);
    await assert.rejects(() => k.deleteAccount({ confirm: ACCOUNT }), (e) => {
      assert.ok(e instanceof KorelyError);
      if (status >= 300) assert.equal(e.status, status);
      else assert.ok(e.message.includes("without `deleted: true`"), e.message);
      return true;
    }, String(status));
    assert.deepEqual(f.calls.map((c) => c.init.method), ["GET", "DELETE"]);
  }
});

test("deleteAccount() tells a server before 1.0.23 to upgrade, and never sends it the flag", async () => {
  const cloud = fakeCloud("flag");
  const k = new Korely({ apiKey: "kor_live_test", fetch: cloud.fetch });
  await assert.rejects(() => k.deleteAccount({ confirm: ACCOUNT }), (e) => {
    assert.ok(e instanceof APIError);
    assert.equal(e.status, 422);
    assert.equal(e.code, "server_too_old");
    for (const words of ["1.0.23", "korely-memory 0.1.18", "Nothing was deleted"]) assert.ok(e.message.includes(words), words);
    return true;
  });
  assert.equal(cloud.deleted, false);
  assert.deepEqual(cloud.deletes(), [{ confirm: ACCOUNT }]);
});

test("deleteAccount(): a conflict, the server's refusal, the Self-hosted", async () => {
  const whoami = { status: 200, body: { kind: "api_key", tenant_id: ACCOUNT } };
  const { k, f } = client([
    whoami,
    { status: 409, body: { code: "account_has_login", message: "This key belongs to an account with a Korely login." } },
    whoami,
    { status: 400, body: { code: "confirmation_required", message: "Send ?confirm=<this account's id>." } },
    { status: 404, body: { detail: "Not Found", code: "not_found", message: "Not Found" } },
    whoami,
    { status: 405, body: { detail: "Method Not Allowed", code: "method_not_allowed", message: "Method Not Allowed" } },
  ]);
  await assert.rejects(() => k.deleteAccount({ confirm: ACCOUNT }), (e) => {
    assert.ok(e instanceof ConflictError);
    assert.ok(!(e instanceof StaleWriteError));
    assert.equal(e.code, "account_has_login");
    return true;
  });
  await assert.rejects(() => k.deleteAccount({ confirm: ACCOUNT }), (e) => e instanceof APIError && e.status === 400 && e.code === "confirmation_required");
  // The Self-hosted has no such route: 404, or 405 where it serves its dashboard.
  await assert.rejects(() => k.deleteAccount({ confirm: ACCOUNT }), (e) => e instanceof NotFoundError);
  await assert.rejects(() => k.deleteAccount({ confirm: ACCOUNT }), (e) => e instanceof APIError && e.code === "method_not_allowed");
  assert.equal(f.calls.filter((c) => c.init.method === "DELETE").length, 3);
});

// Two real servers on two ports, two origins.
const KEY = "kor_self_redirect_probe_0123456789";
async function twoServers(otherAnswer) {
  const { createServer } = await import("node:http");
  const listen = (handler) => new Promise((resolve) => {
    const s = createServer(handler);
    s.listen(0, "127.0.0.1", () => resolve([s, `http://127.0.0.1:${s.address().port}`]));
  });
  const seen = { ours: [], other: [] };
  const [other, otherUrl] = await listen((req, res) => {
    seen.other.push([req.method, req.url, req.headers]);
    const [status, body] = otherAnswer;
    res.writeHead(status, { "content-type": "application/json" });
    res.end(JSON.stringify(body));
  });
  const state = { status: 302, location: (u) => otherUrl + u };
  const [ours, oursUrl] = await listen((req, res) => {
    seen.ours.push([req.method, req.url, req.headers]);
    res.writeHead(state.status, { location: state.location(req.url) });
    res.end();
  });
  return { seen, state, otherUrl, oursUrl, close: () => { ours.close(); other.close(); } };
}

test("a GET redirected to another server goes without the key (Node's fetch)", async () => {
  // Node's fetch follows a GET's redirect and drops Authorization on the way
  // to another origin (the Fetch standard).
  const s = await twoServers([200, { ok: true, tier: "pro", region: "elsewhere", scopes: [] }]);
  try {
    for (s.state.status of [301, 302, 303, 307, 308]) {
      s.seen.ours.length = 0;
      s.seen.other.length = 0;
      await new Korely({ apiKey: KEY, baseUrl: s.oursUrl }).ping();
      assert.equal(s.seen.ours[0][2].authorization, `Bearer ${KEY}`);
      assert.deepEqual(s.seen.other.map(([m, u]) => [m, u]), [["GET", "/v1/ping"]]);
      assert.ok(!Object.values(s.seen.other[0][2]).some((v) => String(v).includes(KEY)), String(s.state.status));
    }
  } finally {
    s.close();
  }
});

test("a call that is not a GET is never redirected", async () => {
  // fetch follows a POST answered 301, 302 or 303 as a GET with no body, and
  // that GET's answer came back as the POST's: add() said stored from another
  // address's reply. Every redirect of a POST, a PATCH or a DELETE is now its
  // answer, and nothing reaches the other server.
  const s = await twoServers([200, { id: "mem_1", status: "ready", deleted: true }]);
  s.state.location = (u) => s.otherUrl + "/x?token=t";
  const calls = {
    POST: (k) => k.add("hello", { user_id: "dana" }),
    PATCH: (k) => k.update("mem_1", { content: "hello" }),
    DELETE: (k) => k.delete("mem_1"),
  };
  try {
    for (const [method, call] of Object.entries(calls)) {
      for (s.state.status of [301, 302, 303, 307, 308]) {
        s.seen.ours.length = 0;
        s.seen.other.length = 0;
        const k = new Korely({ apiKey: KEY, baseUrl: s.oursUrl });
        await assert.rejects(() => call(k), (e) => {
          assert.ok(e instanceof APIError, String(e));
          assert.equal(e.status, s.state.status);
          assert.ok(e.message.includes(`redirect to ${s.otherUrl} this client does not follow`), e.message);
          assert.ok(!e.message.includes("token"));
          return true;
        }, `${method} ${s.state.status}`);
        assert.deepEqual(s.seen.ours.map(([m]) => m), [method]);
        assert.deepEqual(s.seen.other, [], `${method} ${s.state.status}`);
      }
    }
  } finally {
    s.close();
  }
});

test("a browser's opaque redirect (status 0) of a call that is not a GET rejects", async () => {
  // A browser's fetch with redirect: "manual" hides the redirect: status 0,
  // no Location. Every call but a GET asks for that, a GET follows.
  const { k, f } = client([{ status: 0, body: null }, { status: 0, body: null }, { status: 200, body: { ok: true } }]);
  for (const call of [() => k.add("hello", { user_id: "dana" }), () => k.delete("mem_1")]) {
    await assert.rejects(call, (e) => {
      assert.ok(e instanceof APIError);
      assert.equal(e.status, 0);
      assert.match(e.message, /^The server answered a (POST|DELETE) with a redirect this client does not follow: /);
      return true;
    });
  }
  await k.ping();
  assert.deepEqual(f.calls.map((c) => [c.init.method, c.init.redirect]),
    [["POST", "manual"], ["DELETE", "manual"], ["GET", "follow"]]);
});

test("a GET's redirect that fetch did not follow says it was a GET", async () => {
  const { k } = client([{ status: 300, body: {}, headers: { location: "https://elsewhere.test/v1/ping?t=1" } }]);
  await assert.rejects(() => k.ping(), (e) => {
    assert.ok(e instanceof APIError);
    assert.equal(e.status, 300);
    assert.equal(e.message, "The server answered 300 to a GET, a redirect to https://elsewhere.test " +
      "that fetch did not follow: that is not the answer to the call.");
    return true;
  });
});

test("a 401 after a GET redirected to another server says so", async () => {
  const s = await twoServers([401, { code: "invalid_key", message: "Missing key." }]);
  try {
    await assert.rejects(() => new Korely({ apiKey: KEY, baseUrl: s.oursUrl }).ping(), (e) => {
      assert.ok(e instanceof AuthenticationError);
      assert.equal(e.message, `Missing key. The call was redirected to ${s.otherUrl}, and fetch does ` +
        "not send the key to another origin: if that is the server, use that address as baseUrl.");
      return true;
    });
    await assert.rejects(() => new Korely({ apiKey: KEY, baseUrl: s.otherUrl }).ping(),
      (e) => e instanceof AuthenticationError && e.message === "Missing key.");
  } finally {
    s.close();
  }
});

test("Korely.initAgent() signs up with no key: POST /v1/agents/init, no Authorization", async () => {
  const minted = { api_key: "kor_live_0123456789abcdef", tier: "hobby", region: "eu-hel1", scopes: ["memories:read", "memories:write"], quotas: { agents: 2 } };
  const savedKey = process.env.KORELY_API_KEY;
  const savedBase = process.env.KORELY_BASE_URL;
  delete process.env.KORELY_API_KEY;
  delete process.env.KORELY_BASE_URL;
  try {
    const f = fakeFetch([{ status: 201, body: minted }, { status: 201, body: minted }, { status: 201, body: minted }]);
    const r = await Korely.initAgent("claude-code", { fetch: f });
    assert.equal(f.calls[0].url, "https://api.korely.ai/v1/agents/init");
    assert.equal(f.calls[0].init.method, "POST");
    assert.equal(f.calls[0].init.headers.Authorization, undefined);
    assert.deepEqual(JSON.parse(f.calls[0].init.body), { agent_caller: "claude-code" });
    assert.deepEqual(r, minted);
    await Korely.initAgent(undefined, { fetch: f, baseUrl: "http://localhost:8000/" });
    assert.equal(f.calls[1].url, "http://localhost:8000/v1/agents/init");
    assert.deepEqual(JSON.parse(f.calls[1].init.body), {});
    process.env.KORELY_BASE_URL = "https://staging.example";
    await Korely.initAgent("x", { fetch: f });
    assert.equal(f.calls[2].url, "https://staging.example/v1/agents/init");
  } finally {
    if (savedKey === undefined) delete process.env.KORELY_API_KEY; else process.env.KORELY_API_KEY = savedKey;
    if (savedBase === undefined) delete process.env.KORELY_BASE_URL; else process.env.KORELY_BASE_URL = savedBase;
  }
});

test("Korely.initAgent(): the refusals keep their code; a label that is not text, or base_url, never leaves", async () => {
  const f = fakeFetch([
    { status: 403, body: { code: "signup_disabled", message: "Agent self-signup is currently closed." } },
    { status: 429, body: { code: "signup_rate_limited", message: "Try again tomorrow." }, headers: { "retry-after": "86400" } },
  ]);
  await assert.rejects(() => Korely.initAgent("x", { fetch: f, baseUrl: "https://api.test" }), (e) => e instanceof NamespaceForbiddenError && e.code === "signup_disabled");
  await assert.rejects(() => Korely.initAgent("x", { fetch: f, baseUrl: "https://api.test" }), (e) => {
    assert.ok(e instanceof QuotaExceededError);
    assert.equal(e.code, "signup_rate_limited");
    assert.equal(e.retryAfter, 86400);
    return true;
  });
  const none = fakeFetch([]);
  await assert.rejects(() => Korely.initAgent({ baseUrl: "https://api.test" }, { fetch: none }), KorelyError);
  await assert.rejects(() => Korely.initAgent("x", { fetch: none, base_url: "https://api.test" }), /baseUrl/);
  assert.equal(none.calls.length, 0);
});

// ── options the method does not read are refused (2026-10-07) ──────────────
// In plain JavaScript `{ userId }` was dropped: a search ran over every end
// user of the project, a write landed with no end user, `asOf` answered with
// today's facts (a new customer's first test). Nothing may be sent.

test("an option in camelCase is refused before anything is sent, naming the spelling", async () => {
  const { k, f } = client([]);
  const cases = [
    [() => k.search("x", { userId: "node-user" }), /search: unknown option userId\. Did you mean user_id\?/],
    [() => k.add("x", { userId: "node-user" }), /add: unknown option userId\. Did you mean user_id\?/],
    [() => k.getFacts({ asOf: "2020-01-01" }), /getFacts: unknown option asOf\. Did you mean as_of\?/],
    [() => k.getAll({ agentId: "sales" }), /Did you mean agent_id\?/],
    [() => k.deleteAll({ userId: "node-user" }), /deleteAll: unknown option userId\. Did you mean user_id\?/],
    [() => k.getContext("x", { tokenBudget: 2000 }), /getContext: unknown option tokenBudget\. Did you mean token_budget\?/],
    [() => k.getContext({ query: "x", userId: "u" }), /Did you mean user_id\?/],
    [() => k.events({ user: "u" }), /events: unknown option user\. It takes: user_id, status, limit\./],
    [() => k.addFactTriple("a", "b", "c", { validFrom: "2026-01-01" }), /Did you mean valid_from\?/],
    [() => k.update("m1", { content: "x", expectedUpdatedAt: "t" }), /Did you mean expected_updated_at\?/],
    [() => k.search("x", ["user_id", "u"]), /search: the options must be an object, got an array\./],
  ];
  for (const [call, message] of cases) {
    await assert.rejects(call, (e) => e instanceof KorelyError && message.test(e.message), String(message));
  }
  assert.equal(f.calls.length, 0, "a refused call sends nothing");
});

test("the client's own options are camelCase, and the Python spellings are refused", () => {
  assert.throws(() => new Korely({ api_key: "kor_self_x" }),
    (e) => e instanceof KorelyError && /unknown option api_key\. Did you mean apiKey\?/.test(e.message));
  assert.throws(() => new Korely({ apiKey: "kor_self_x", timeout_ms: 5 }), /Did you mean timeoutMs\?/);
});

test("the known options still go out as before", async () => {
  const { k, f } = client([{ body: { results: [] } }, { body: { id: "m1" } }]);
  await k.search("x", { user_id: "node-user", limit: 3 });
  await k.add("x", { user_id: "node-user", agent_id: "sales" });
  assert.equal(JSON.parse(f.calls[0].init.body).user_id, "node-user");
  assert.equal(JSON.parse(f.calls[1].init.body).agent_id, "sales");
});

test("new Korely() reads what `korely init` saved, as the Python client does (2026-10-07)", async () => {
  // The documented path, `korely init` and then the SDK, threw "No API key" in
  // Node. The argument and the variables still come first.
  const { mkdtempSync, writeFileSync, rmSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const dir = mkdtempSync(join(tmpdir(), "korely-config-"));
  const saved = { ...process.env };
  try {
    writeFileSync(join(dir, "config.json"), JSON.stringify({
      api_key: "kor_self_fromconfig", base_url: "https://memory.example.com/" }));
    delete process.env.KORELY_API_KEY;
    delete process.env.KORELY_BASE_URL;
    process.env.KORELY_CONFIG_HOME = dir;
    const k = new Korely({ fetch: fakeFetch([]) });
    assert.equal(k.apiKey, "kor_self_fromconfig");
    assert.equal(k.baseUrl, "https://memory.example.com");
    process.env.KORELY_BASE_URL = "https://other.example.com";
    assert.equal(new Korely({ fetch: fakeFetch([]) }).baseUrl, "https://other.example.com");
    assert.equal(new Korely({ apiKey: "kor_self_arg", fetch: fakeFetch([]) }).apiKey, "kor_self_arg");
    rmSync(join(dir, "config.json"));
    delete process.env.KORELY_BASE_URL;
    assert.throws(() => new Korely({ fetch: fakeFetch([]) }), /No API key/);
  } finally {
    for (const v of ["KORELY_API_KEY", "KORELY_BASE_URL", "KORELY_CONFIG_HOME"]) {
      if (saved[v] === undefined) delete process.env[v]; else process.env[v] = saved[v];
    }
    rmSync(dir, { recursive: true, force: true });
  }
});

test("addFactTriple says whose fact it is, from the server or from what was sent (2026-10-07)", async () => {
  const { k } = client([
    { status: 201, body: { id: "f1", subject: "maria", predicate: "works_at", object: "Acme",
                           user_id: "maria", agent_id: null } },
    { status: 201, body: { id: "f2", subject: "maria", predicate: "works_at", object: "Acme" } },
  ]);
  const fromServer = await k.addFactTriple("maria", "works_at", "Acme", { user_id: "maria", agent_id: "x" });
  assert.equal(fromServer.user_id, "maria");
  assert.equal(fromServer.agent_id, null, "the server's answer wins");
  const older = await k.addFactTriple("maria", "works_at", "Acme", { user_id: "maria" });
  assert.equal(older.user_id, "maria", "an older server left it out: what was sent");
  assert.equal(older.agent_id, undefined);
});

test("processing_region: chosen at signup when given, read from ping (2026-10-07)", async () => {
  const f = fakeFetch([
    { status: 201, body: { api_key: "kor_live_x", tier: "hobby", region: "eu-hel1", processing_region: "eu", scopes: [], quotas: {} } },
  ]);
  const r = await Korely.initAgent("app", { fetch: f, baseUrl: "https://api.test", processingRegion: "eu" });
  assert.deepEqual(JSON.parse(f.calls[0].init.body), { agent_caller: "app", processing_region: "eu" });
  assert.equal(r.processing_region, "eu");
  const none = fakeFetch([]);
  await assert.rejects(() => Korely.initAgent("app", { fetch: none, processingRegion: "us" }),
    (e) => e instanceof KorelyError && /processingRegion is "eu" or "global"/.test(e.message));
  assert.equal(none.calls.length, 0);
  const { k } = client([{ body: { ok: true, tier: "hobby", region: "eu-hel1", processing_region: "global", scopes: [] } }]);
  assert.equal((await k.ping()).processing_region, "global");
});

test("a quota refusal carries limit, used and resets_at, read from the body, never the message (2026-10-07)", async () => {
  const { k } = client([
    { status: 429, body: { code: "quota_exceeded", message: "2,000 writes on the Developer plan this month...",
                           limit: 2000, used: 2200, resets_at: "2026-11-01" } },
    { status: 429, body: { code: "rate_limit_exceeded", message: "slow down" }, headers: { "retry-after": "7" } },
  ]);
  await assert.rejects(() => k.add("x", { user_id: "u" }), (e) =>
    e instanceof QuotaExceededError && e.limit === 2000 && e.used === 2200 && e.resetsAt === "2026-11-01");
  await assert.rejects(() => k.add("x", { user_id: "u" }), (e) =>
    e instanceof QuotaExceededError && e.limit === undefined && e.retryAfter === 7);
});

// ── Korely Agent 0.1.42: declared names, work sessions ────────────────────────

test("declareName posts the name to the end user's names", async () => {
  const { k, f } = client([{ status: 201, body: { user_id: "maria", name: "Maria Rossi", agent_id: null } }]);
  const d = await k.declareName("Maria Rossi", { user_id: "maria" });
  assert.equal(d.name, "Maria Rossi");
  assert.match(f.calls[0].url, /\/v1\/users\/maria\/names$/);
  assert.equal(f.calls[0].init.method, "POST");
  assert.deepEqual(JSON.parse(f.calls[0].init.body), { name: "Maria Rossi" });
});

test("declareName refuses a blank name and an unknown option before sending", async () => {
  const { k, f } = client([]);
  await assert.rejects(k.declareName("  ", { user_id: "maria" }), KorelyError);
  await assert.rejects(k.declareName("Maria", { user_id: "maria", userId: "x" }), KorelyError);
  assert.equal(f.calls.length, 0);
});

test("add sends the kind of the text", async () => {
  const { k, f } = client([{ status: 201, body: { id: "mem_1" } }]);
  await k.add("Ran the tests.", { user_id: "repo:x", kind: "session" });
  assert.equal(JSON.parse(f.calls[0].init.body).kind, "session");
});

test("exclude_sessions is sent only when true", async () => {
  const { k, f } = client([{ body: { results: [] } }, { body: { results: [] } },
                           { body: { facts: [], total: 0 } }, { body: { memories: [], total: 0 } }]);
  await k.search("editor", { user_id: "maria", exclude_sessions: true });
  await k.search("editor", { user_id: "maria" });
  await k.getFacts({ user_id: "maria", exclude_sessions: true });
  await k.getAll({ user_id: "maria", exclude_sessions: false });
  assert.equal(JSON.parse(f.calls[0].init.body).exclude_sessions, true);
  assert.equal("exclude_sessions" in JSON.parse(f.calls[1].init.body), false);
  assert.match(f.calls[2].url, /exclude_sessions=true/);
  assert.doesNotMatch(f.calls[3].url, /exclude_sessions/);
});

test("a write with no identity: the server's 422 identity_required arrives with its message", async () => {
  // Korely 1.0.19 sends the pair at the top and inside `detail`; an install
  // that sends `detail` alone reads the same. The client sends what the call
  // names and nothing else: whose memory it is stays the program's to say.
  const message =
    "user_id or agent_id is required: say whose memory this is, with user_id (the end user " +
    "it belongs to) or agent_id (the agent writing it); an id of spaces or invisible characters names nobody";
  const pair = { code: "identity_required", message };
  for (const body of [{ detail: pair, ...pair }, { detail: pair }]) {
    const { k, f } = client([{ status: 422, body }]);
    await assert.rejects(k.add("Maria is on Pro."), (e) => {
      assert.ok(e instanceof APIError);
      assert.equal(e.status, 422);
      assert.equal(e.code, "identity_required");
      assert.equal(e.message, message);
      return true;
    });
    const sent = JSON.parse(f.calls[0].init.body);
    assert.equal("user_id" in sent || "agent_id" in sent, false);
  }
});

test("the ids a write names are the ids it sends", async () => {
  const { k, f } = client([{ status: 201, body: { id: "m" } }, { status: 201, body: { id: "f" } },
                           { status: 202, body: { id: "j" } }]);
  await k.add("x", { agent_id: "bot" });
  await k.addFactTriple("maria", "subscribes_to", "Pro", { user_id: "maria" });
  await k.batch([{ content: "a", user_id: "maria" }, { content: "b", agent_id: "bot" }]);
  const [add, fact, batch] = f.calls.map((c) => JSON.parse(c.init.body));
  assert.equal(add.agent_id, "bot");
  assert.equal("user_id" in add, false);
  assert.equal(fact.user_id, "maria");
  assert.deepEqual(batch.memories, [{ content: "a", user_id: "maria" }, { content: "b", agent_id: "bot" }]);
});

test("the package's own examples, run as written, name the user in every write (2026-11-14)", async () => {
  // `add(..., { agent_id })` alone was the example of the client: whoever
  // copied it wrote an agent's note in place of the user's memory. Mem0, Zep
  // and Supermemory name the user in their first write (their quickstarts,
  // read 2026-11-14). The examples are the indented lines of each file's
  // first comment.
  for (const file of ["../src/index.ts", "../src/client.ts"]) {
    const head = readFileSync(new URL(file, import.meta.url), "utf8").split("*/")[0];
    const code = head.split("\n")
      .filter((l) => l.startsWith(" *   ") && !l.includes("import "))
      .map((l) => l.slice(5))
      .join("\n");
    assert.match(code, /korely\.add\(/, file);
    const f = fakeFetch(Array.from({ length: 5 }, () => ({ status: 201, body: { id: "m1" } })));
    class Example extends Korely {
      constructor(opts) {
        super({ ...opts, fetch: f });
      }
    }
    await new Function("Korely", `return (async () => {\n${code}\n})();`)(Example);
    const writes = f.calls.filter((c) => c.init.method === "POST");
    assert.ok(writes.length > 0, file);
    for (const w of writes) assert.ok(JSON.parse(w.init.body).user_id, `${file}: ${w.init.body}`);
  }
});

// A write whose answer never came may have been made (a timeout while a proxy
// held it through a restart of the server, which then made it): sent again
// without a key it is made twice, with the same `idempotency_key` once
// (Korely Agent 1.0.27). The header as draft-ietf-httpapi-idempotency-key-
// header-08 section 2.1 wants it, a quoted String of RFC 9651.
test("add and batch send the Idempotency-Key they are given, and a new one of their own without", async () => {
  const { k, f } = client([
    { status: 201, body: { id: "mem_1" } },
    { status: 202, body: { job_id: "job_1" } },
    { status: 201, body: { id: "mem_2" } },
    { status: 201, body: { id: "mem_3" } },
    { status: 202, body: { job_id: "job_2" } },
    { status: 201, body: { id: "mem_4" } },
  ]);
  await k.add("Ada prefers tea.", { user_id: "ada", idempotency_key: "3f2c9a" });
  await k.batch([{ content: "x", user_id: "ada" }], { idempotency_key: "b-1" });
  await k.add("Ada prefers tea.", { user_id: "ada" });
  await k.add("Ada prefers tea.", { user_id: "ada" });
  await k.batch([{ content: "x", user_id: "ada" }]);
  await k.add("x", { user_id: "ada", idempotency_key: 'a"b\\c' });
  const sent = f.calls.map((c) => c.init.headers["Idempotency-Key"]);
  assert.deepEqual([sent[0], sent[1], sent[5]], ['"3f2c9a"', '"b-1"', '"a\\"b\\\\c"']);
  // 0.2.2: a write given no key gets one of its own, a new one per write.
  for (const own of sent.slice(2, 5)) assert.match(own, /^"[0-9a-f]{32}"$/);
  assert.equal(new Set(sent.slice(2, 5)).size, 3);
});

test("a bad Idempotency-Key is a KorelyError and nothing is sent", async () => {
  // 1 to 255 printable ASCII characters, what the server takes in a quoted
  // String; anything else would reach fetch, which refuses a header with a
  // line break with a TypeError of its own.
  for (const bad of ["", "a".repeat(256), "a\nb", "a\rb", "café", "tab\there", 42]) {
    const { k, f } = client([{ status: 201, body: { id: "mem_1" } }]);
    await assert.rejects(k.add("x", { user_id: "ada", idempotency_key: bad }), KorelyError, String(bad));
    await assert.rejects(k.batch([{ content: "x", user_id: "ada" }], { idempotency_key: bad }), KorelyError);
    assert.equal(f.calls.length, 0, String(bad));
  }
  const { k, f } = client([{ status: 201, body: { id: "mem_1" } }, { status: 201, body: { id: "mem_2" } }]);
  await k.add("x", { user_id: "ada", idempotency_key: "~".repeat(255) });
  await k.add("x", { user_id: "ada", idempotency_key: " spaced key " });
  assert.equal(f.calls.length, 2);
});

// 0.2.2. A write whose answer never came (a timeout, a connection that failed)
// may have been made: the client sends it once more, after half a second,
// with the same Idempotency-Key, which Korely 1.0.27 and later make once.
// Never more than once, never a read, never a call the server answered. The
// Stainless SDKs do the same for every call that is not a GET (openai-python
// 3.58, _base_client.py: one key made before the first try, the same on each
// retry). This fetch honours the key as the server's services/idempotency.py
// does: the same key again answers what the first made, and makes nothing.
// `fate(n)` says what happens to the n-th request: "answer", "lose" (made,
// then the connection fails: the answer lost), "hang" (no answer until the
// client gives up), "in_use" (the 409 of a key whose first request is still
// being made) or a status answered with nothing made.
function keyedFetch(fate = () => "answer") {
  const seen = [];
  const made = new Map();
  let writes = 0;
  const reply = (status, body, headers = {}) => ({
    ok: status >= 200 && status < 300,
    status,
    async text() {
      return JSON.stringify(body);
    },
    headers: { get: (k) => headers[k.toLowerCase()] ?? null },
  });
  const fn = async (url, init) => {
    const key = init.headers["Idempotency-Key"];
    seen.push({ method: init.method, path: new URL(url).pathname, key });
    const what = fate(seen.length);
    if (typeof what === "number") return reply(what, { code: "unavailable", message: "down" });
    if (what === "hang") {
      return new Promise((_resolve, reject) =>
        init.signal.addEventListener("abort", () => reject(new Error("This operation was aborted"))));
    }
    if (what === "in_use") {
      return reply(409, {
        code: "idempotency_key_in_use",
        message: "A request with this Idempotency-Key is still being made; retry it as it is.",
      });
    }
    let status = 200;
    let body = { context: "", tokens: 0, sources: [] };
    const headers = {};
    if (init.method === "POST") {
      let id = key && made.get(key);
      if (id) headers["idempotent-replayed"] = "true";
      else {
        id = `mem_${++writes}`;
        if (key) made.set(key, id);
      }
      [status, body] = new URL(url).pathname === "/v1/memories"
        ? [201, { id, content: "x", status: "processing" }]
        : [202, { id, status: "queued", received: 1 }];
    }
    if (what === "lose") throw new TypeError("fetch failed");
    return reply(status, body, headers);
  };
  fn.seen = seen;
  fn.writes = () => writes;
  fn.posts = () => seen.filter((s) => s.method === "POST");
  return fn;
}

function keyedClient(fate, opts = {}) {
  const f = keyedFetch(fate);
  const k = new Korely({ apiKey: "kor_self_test", baseUrl: "https://api.test", fetch: f, timeoutMs: 50, ...opts });
  return { k, f };
}

const loseTheFirst = (n) => (n === 1 ? "lose" : "answer");

test("an answer lost after the write: one memory, no error, the same key twice", async () => {
  const { k, f } = keyedClient(loseTheFirst);
  const memory = await k.add("Ada prefers tea.", { user_id: "ada" });
  assert.equal(memory.id, "mem_1");
  assert.equal(f.writes(), 1);
  const keys = f.posts().map((p) => p.key);
  assert.equal(keys.length, 2);
  assert.equal(keys[0], keys[1]);
});

test("a batch with its answer lost is made once too", async () => {
  const { k, f } = keyedClient(loseTheFirst);
  const job = await k.batch([{ content: "x", user_id: "ada" }]);
  assert.deepEqual([job.id, f.writes(), f.posts().length], ["mem_1", 1, 2]);
});

test("no answer twice: after one retry, an error that says it may have been made and names the key", async () => {
  const { k, f } = keyedClient(() => "hang");
  await assert.rejects(k.add("Ada prefers tea.", { user_id: "ada" }), (e) => {
    const key = f.posts()[0].key.replace(/"/g, "");
    assert.ok(e instanceof KorelyError);
    assert.equal(e.code, "write_not_confirmed");
    assert.match(e.message, /may have been made/);
    assert.ok(e.message.includes(key), e.message);
    return true;
  });
  assert.equal(f.posts().length, 2);
});

test("a retry that finds the first still being made is not confirmed either", async () => {
  const { k, f } = keyedClient((n) => (n === 1 ? "lose" : "in_use"));
  await assert.rejects(k.add("x", { user_id: "ada" }), (e) => e.code === "write_not_confirmed");
  assert.equal(f.posts().length, 2);
});

test("a retry that fails any other way (5xx, 429, timeout) leaves the write unconfirmed", async () => {
  // The first try was made and its answer lost. Only a success of the retry
  // says so: a 5xx, a 429 or a timeout of the retry tell nothing of the
  // first, so each is write_not_confirmed, with the key and the cause, never
  // the bare 503 that read as "not made". Until 2026-11-23 these rejected
  // with the retry's own error.
  for (const fate of [503, 502, 429, "hang"]) {
    const { k, f } = keyedClient((n) => (n === 1 ? "lose" : fate));
    await assert.rejects(k.add("Ada prefers tea.", { user_id: "ada" }), (e) => {
      const key = f.posts()[0].key.replace(/"/g, "");
      assert.ok(e instanceof KorelyError && !(e instanceof APIError), String(fate));
      assert.equal(e.code, "write_not_confirmed", String(fate));
      assert.match(e.message, /may have been made/);
      assert.ok(e.message.includes(key), e.message);
      assert.ok(e.message.includes(typeof fate === "number" ? `HTTP ${fate}` : "timed out"), e.message);
      return true;
    });
    assert.deepEqual([f.posts().length, f.writes()], [2, 1], String(fate));
  }
});

test("the caller's key is the one sent both times", async () => {
  const { k, f } = keyedClient(loseTheFirst);
  await k.add("Ada prefers tea.", { user_id: "ada", idempotency_key: "k-7" });
  assert.deepEqual(f.posts().map((p) => p.key), ['"k-7"', '"k-7"']);
  assert.equal(f.writes(), 1);
});

test("retryWrites: false sends a write once, and the error names the key", async () => {
  const { k, f } = keyedClient(loseTheFirst, { retryWrites: false });
  await assert.rejects(k.add("x", { user_id: "ada", idempotency_key: "k-8" }), (e) => {
    assert.equal(e.code, "write_not_confirmed");
    assert.ok(e.message.includes("k-8"), e.message);
    return true;
  });
  assert.equal(f.posts().length, 1);
});

test("a read or a delete with no answer is never sent again", async () => {
  const { k, f } = keyedClient(() => "lose");
  await assert.rejects(k.getContext("where?", { user_id: "ada" }), (e) => e.code === "connection_error");
  await assert.rejects(k.delete("mem_1"), KorelyError);
  assert.deepEqual(f.seen.map((s) => s.method), ["GET", "DELETE"]);
});

test("a write the server answered is not sent again", async () => {
  const { k, f } = keyedClient(() => 503);
  await assert.rejects(k.add("x", { user_id: "ada" }), (e) => e.status === 503);
  assert.equal(f.posts().length, 1);
});

test("without crypto.randomUUID (a page over plain http) a write still gets a key of its own", async () => {
  // Such a page still has crypto.getRandomValues, and the key comes from it;
  // Math.random only where there is no crypto at all.
  const kept = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  const real = kept.get ? kept.get.call(globalThis) : kept.value;
  let drawn = 0;
  const onlyValues = { getRandomValues: (a) => (drawn++, real.getRandomValues(a)) };
  try {
    for (const crypto of [onlyValues, {}]) {
      Object.defineProperty(globalThis, "crypto", { value: crypto, configurable: true, writable: true });
      const { k, f } = keyedClient(() => "answer");
      await k.add("x", { user_id: "ada" });
      await k.add("x", { user_id: "ada" });
      const keys = f.posts().map((p) => p.key);
      for (const key of keys) assert.match(key, /^"[0-9a-f]{32}"$/);
      assert.notEqual(keys[0], keys[1]);
    }
    assert.equal(drawn, 2);
  } finally {
    Object.defineProperty(globalThis, "crypto", kept);
  }
});
