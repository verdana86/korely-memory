// SDK tests — no network. A fake fetch records the request and returns a canned
// response; we assert the SDK builds the right request and parses the right
// shape. Runs against the built package (dist), so build first:
//   npm run build && npm test
import test from "node:test";
import assert from "node:assert/strict";
import {
  Korely,
  KorelyError,
  AuthenticationError,
  QuotaExceededError,
  APIError,
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
    apiKey: "kor_live_test",
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
    const k = new Korely({ apiKey: "kor_live_x", fetch: async () => ({}) });
    assert.equal(k.baseUrl, "https://2-29-27-64.nip.io");
  } finally {
    if (prima === undefined) delete process.env.KORELY_BASE_URL;
    else process.env.KORELY_BASE_URL = prima;
  }
});

test("senza la variabile resta il servizio ospitato", () => {
  const prima = process.env.KORELY_BASE_URL;
  delete process.env.KORELY_BASE_URL;
  try {
    const k = new Korely({ apiKey: "kor_live_x", fetch: async () => ({}) });
    assert.equal(k.baseUrl, "https://api.korely.ai");
  } finally {
    if (prima !== undefined) process.env.KORELY_BASE_URL = prima;
  }
});

test("l'opzione esplicita vince sulla variabile", () => {
  const prima = process.env.KORELY_BASE_URL;
  process.env.KORELY_BASE_URL = "https://variabile.example";
  try {
    const k = new Korely({
      apiKey: "kor_live_x",
      baseUrl: "https://esplicito.example",
      fetch: async () => ({}),
    });
    assert.equal(k.baseUrl, "https://esplicito.example");
  } finally {
    if (prima === undefined) delete process.env.KORELY_BASE_URL;
    else process.env.KORELY_BASE_URL = prima;
  }
});
