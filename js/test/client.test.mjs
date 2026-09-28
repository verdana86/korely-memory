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
  StaleWriteError,
  QuotaExceededError,
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
  assert.throws(() => new Korely({ fetch: async () => ({}) }), KorelyError);
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
  process.env.KORELY_BASE_URL = "https://2-29-27-64.nip.io";
  try {
    const k = new Korely({ apiKey: "kor_self_x", fetch: async () => ({}) });
    assert.equal(k.baseUrl, "https://2-29-27-64.nip.io");
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
