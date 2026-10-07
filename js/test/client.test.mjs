// SDK tests, no network. A fake fetch records the request and returns a canned
// response; we assert the SDK builds the right request and parses the right
// shape. Runs against the built package (dist), so build first:
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
} from "../dist/index.js";

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
  await k.search("q", { user_id: "u", run_id: "r1", metadata: { tier: "pro" } });
  const sent = JSON.parse(f.calls[0].init.body);
  assert.equal(sent.run_id, "r1");
  assert.deepEqual(sent.metadata, { tier: "pro" });
  const page = await k.getAll({ user_id: "u", run_id: "r1" });
  assert.match(f.calls[1].url, /run_id=r1/);
  assert.equal(page.total, 9);
  assert.deepEqual([...page].map((m) => m.id), ["m1", "m2"]);
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

// ── the contract as deployed on 2026-09-28 evening (GordonPro d46ab5e, c7cf69c,
//    7d7f0eb; korely-agent 6699c14, ac3a2da, ef44feb) ──────────────────────────

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

test("deleteAgent: slot_freed, and a 404 for a name outside the project", async () => {
  const ok = client([{ status: 200, body: { agent_id: "bot", memories_deleted: 3, facts_deleted: 1, audit_id: "aud_1", slot_freed: false } }]);
  const r = await ok.k.deleteAgent("bot");
  assert.equal(r.slot_freed, false);
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
  // caller's own server (found by the blind installs of korely-agent 0.1.13).
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

// ── 2026-10-06: the gaps window A found comparing every /v1 route with the
//    SDKs (GordonPro app/api/v1_*.py, korely-agent korely_agent/api/*.py) ──────

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

test("deleteAccount() needs { confirm: true } before anything leaves, then DELETE /v1/account?confirm=true", async () => {
  for (const opts of [undefined, {}, { confirm: false }, { confirm: "yes" }, { confirm: 1 }]) {
    const { k, f } = client([]);
    await assert.rejects(() => k.deleteAccount(opts), (e) => {
      assert.ok(e instanceof KorelyError);
      assert.ok(!(e instanceof APIError));
      assert.equal(e.code, "confirmation_required");
      return true;
    });
    assert.equal(f.calls.length, 0);
  }
  const { k, f } = client([
    { status: 200, body: { deleted: true, removed: { memories: 12, facts: 30, keys: 1 } } },
    { status: 409, body: { code: "account_has_login", message: "This key belongs to an account with a Korely login." } },
    { status: 405, body: { detail: "Method Not Allowed", code: "method_not_allowed", message: "Method Not Allowed" } },
  ]);
  const r = await k.deleteAccount({ confirm: true });
  assert.equal(f.calls[0].init.method, "DELETE");
  assert.equal(f.calls[0].url, "https://api.test/v1/account?confirm=true");
  assert.deepEqual(r, { deleted: true, removed: { memories: 12, facts: 30, keys: 1 } });
  await assert.rejects(() => k.deleteAccount({ confirm: true }), (e) => {
    assert.ok(e instanceof ConflictError);
    assert.ok(!(e instanceof StaleWriteError));
    assert.equal(e.code, "account_has_login");
    return true;
  });
  // The Self-hosted has no such route: 405 where it serves its dashboard.
  await assert.rejects(() => k.deleteAccount({ confirm: true }), (e) => e instanceof APIError && e.code === "method_not_allowed");
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
// today's facts (window A's new-customer test). Nothing may be sent.

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
    assert.throws(() => new Korely({ fetch: fakeFetch([]) }), /korely init/);
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
