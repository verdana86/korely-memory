// A 429 is waited out and the call sent again (0.4.0, decided on 26 Nov 2026),
// as the Python client does: the wait the server asks (Retry-After, or
// `retry_after` in the body), at most `maxRetries` more tries (3) and
// `maxRetryWait` seconds of waiting in all (60). A wait past the bound, or a
// 429 that names none, rejects at once with QuotaExceededError and its
// `retryAfter`. A write keeps its Idempotency-Key. `maxRetries: 0` turns it off.
// Runs against the built package: npm run build && npm test
import test from "node:test";
import assert from "node:assert/strict";
import { Korely, KorelyError, QuotaExceededError } from "korely-memory";

function server(answers) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    const { status = 200, body = {}, headers = {} } = answers.shift() ?? {};
    return {
      ok: status >= 200 && status < 300,
      status,
      async text() {
        return JSON.stringify(body);
      },
      headers: { get: (k) => headers[k.toLowerCase()] ?? null },
    };
  };
  fn.calls = calls;
  return fn;
}

const limited = (seconds, code = "rate_limit_exceeded") => ({
  status: 429,
  body: { code, message: "Too many requests." },
  headers: seconds === undefined ? {} : { "retry-after": String(seconds) },
});

function withSleeps(fn) {
  return async () => {
    const slept = [];
    const real = Korely._sleep;
    Korely._sleep = async (ms) => { slept.push(ms); };
    try {
      await fn(slept);
    } finally {
      Korely._sleep = real;
    }
  };
}

const korely = (f, opts = {}) => new Korely({ apiKey: "kor_self_test", baseUrl: "https://api.test", fetch: f, ...opts });

test("a 429 is sent again after the wait the server asks", withSleeps(async (slept) => {
  const f = server([limited(2), { body: { results: [] } }]);
  await korely(f).search("tea", { user_id: "u" });
  assert.equal(f.calls.length, 2);
  assert.deepEqual(slept, [2000]);
}));

test("the wait in the body counts too", withSleeps(async (slept) => {
  const f = server([{ status: 429, body: { code: "rate_limit_exceeded", retry_after: 3 } },
                    { body: { results: [] } }]);
  await korely(f).search("tea", { user_id: "u" });
  assert.deepEqual(slept, [3000]);
}));

test("a write is sent again with the same Idempotency-Key", withSleeps(async () => {
  const f = server([limited(1), { status: 201, body: { id: "mem_1", content: "c" } }]);
  const m = await korely(f).add("c", { user_id: "u" });
  assert.equal(m.id, "mem_1");
  const keys = f.calls.map((c) => c.init.headers["Idempotency-Key"]);
  assert.equal(keys.length, 2);
  assert.ok(keys[0]);
  assert.equal(keys[0], keys[1]);
  assert.equal(f.calls[0].init.body, f.calls[1].init.body);
}));

test("at most three more tries", withSleeps(async (slept) => {
  const f = server([limited(1), limited(1), limited(1), limited(1), limited(1)]);
  await assert.rejects(korely(f).search("tea", { user_id: "u" }),
    (e) => e instanceof QuotaExceededError && e.retryAfter === 1);
  assert.equal(f.calls.length, 4);
  assert.deepEqual(slept, [1000, 1000, 1000]);
}));

test("at most sixty seconds of waiting in all", withSleeps(async (slept) => {
  const f = server([limited(30), limited(30), limited(30), { body: { results: [] } }]);
  await assert.rejects(korely(f).search("tea", { user_id: "u" }),
    (e) => e instanceof QuotaExceededError && e.retryAfter === 30);
  assert.deepEqual(slept, [30000, 30000]);
  assert.equal(f.calls.length, 3);
}));

test("a wait past the bound rejects at once with its retryAfter", withSleeps(async (slept) => {
  const f = server([limited(61)]);
  await assert.rejects(korely(f).search("tea", { user_id: "u" }),
    (e) => e instanceof QuotaExceededError && e.retryAfter === 61);
  assert.equal(f.calls.length, 1);
  assert.deepEqual(slept, []);
}));

test("a 429 that names no wait is not sent again", withSleeps(async (slept) => {
  for (const code of ["quota_exceeded", "agent_cap_exceeded"]) {
    const f = server([limited(undefined, code)]);
    await assert.rejects(korely(f).add("c", { user_id: "u" }), (e) => e.code === code);
    assert.equal(f.calls.length, 1);
  }
  assert.deepEqual(slept, []);
}));

test("the trial's 429 that asks to adopt the key stays within the bound", withSleeps(async (slept) => {
  const trial = { status: 429, headers: { "retry-after": "60" },
                  body: { code: "rate_limit_exceeded", message: "This key is on trial: adopt it." } };
  const f = server([trial, trial, trial, trial]);
  await assert.rejects(korely(f).add("c", { user_id: "u" }), QuotaExceededError);
  assert.deepEqual(slept, [60000]);
  assert.equal(f.calls.length, 2);
}));

test("maxRetries: 0 sends every call once", withSleeps(async (slept) => {
  const f = server([limited(1)]);
  await assert.rejects(korely(f, { maxRetries: 0 }).search("tea", { user_id: "u" }),
    (e) => e instanceof QuotaExceededError && e.retryAfter === 1);
  assert.equal(f.calls.length, 1);
  assert.deepEqual(slept, []);
}));

test("the bounds are the client's to set, and checked", withSleeps(async (slept) => {
  const f = server([limited(5), limited(5), { body: { results: [] } }]);
  await assert.rejects(korely(f, { maxRetries: 5, maxRetryWait: 9 }).search("tea", { user_id: "u" }),
    QuotaExceededError);
  assert.deepEqual(slept, [5000]);
  for (const bad of [{ maxRetries: -1 }, { maxRetries: 1.5 }, { maxRetries: true },
                     { maxRetryWait: -1 }, { maxRetryWait: "60" }, { maxRetryWait: NaN }]) {
    assert.throws(() => korely(server([]), bad), KorelyError, JSON.stringify(bad));
  }
}));

test("other errors are never waited for", withSleeps(async (slept) => {
  for (const status of [400, 401, 403, 404, 409, 422, 500, 503]) {
    const f = server([{ status, body: { code: "x" }, headers: { "retry-after": "1" } }]);
    await assert.rejects(korely(f).search("tea", { user_id: "u" }));
    assert.equal(f.calls.length, 1, String(status));
  }
  assert.deepEqual(slept, []);
}));
