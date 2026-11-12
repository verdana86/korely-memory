// The node's requests, checked without a server: path, verb, body and query
// for every operation, with a fake n8n that records what would be sent and
// answers with canned JSON. `test/run.mjs` is the same idea against a real
// install; this one runs anywhere, so it can guard what that one cannot.
import assert from "node:assert/strict";
import test from "node:test";
import { Korely } from "../dist/nodes/Korely/Korely.node.js";

function fakeN8n(params, { fail, continueOnFail = false } = {}) {
  const sent = [];
  return {
    sent,
    ctx: {
      getInputData: () => [{ json: {} }],
      getNode: () => ({ name: "Korely", type: "korely", typeVersion: 1, parameters: {} }),
      continueOnFail: () => continueOnFail,
      getNodeParameter(name, _i, fallback) {
        return name in params ? params[name] : fallback;
      },
      helpers: {
        httpRequestWithAuthentication: {
          async call(_self, _cred, options) {
            sent.push(options);
            if (fail) throw fail;
            return { ok: true };
          },
        },
      },
    },
  };
}

async function run(params) {
  const { ctx, sent } = fakeN8n(params);
  await Korely.prototype.execute.call(ctx);
  return sent;
}

test("every operation hits the path and verb of the /v1 contract", async () => {
  const cases = [
    [{ resource: "memory", operation: "add", content: "x", userId: "maria" }, "POST", "/v1/memories"],
    [{ resource: "memory", operation: "search", query: "q" }, "POST", "/v1/memories/search"],
    [{ resource: "context", operation: "get", query: "q" }, "GET", "/v1/context"],
    [{ resource: "fact", operation: "list" }, "GET", "/v1/facts"],
    [{ resource: "fact", operation: "write", subject: "s", predicate: "p", object: "o", userId: "maria" }, "POST", "/v1/facts"],
    [{ resource: "fact", operation: "forget", factId: "fct_1" }, "POST", "/v1/facts/fct_1/forget"],
    [{ resource: "fact", operation: "correct", factId: "fct_1", object: "o" }, "PATCH", "/v1/facts/fct_1"],
  ];
  for (const [params, method, url] of cases) {
    const [req] = await run({ extra: {}, ...params });
    assert.equal(req.method, method, `${params.resource}:${params.operation}`);
    assert.equal(req.url, url, `${params.resource}:${params.operation}`);
  }
});

test("run_id goes where the API takes it", async () => {
  const [search] = await run({ resource: "memory", operation: "search", query: "q", extra: { run_id: "r1" } });
  assert.equal(search.body.run_id, "r1");
  const [add] = await run({ resource: "memory", operation: "add", content: "x", userId: "maria", extra: { run_id: "r1" } });
  assert.equal(add.body.run_id, "r1");
  const [write] = await run({
    resource: "fact", operation: "write", subject: "s", predicate: "p", object: "o", extra: { run_id: "r1", agent_id: "bot" },
  });
  assert.equal(write.body.run_id, "r1");
});

test("run_id on a read that cannot filter by it is an error, not a silent no-op", async () => {
  // GET /v1/context and GET /v1/facts have no run_id parameter and ignore
  // unknown query keys, so sending it read across every run while the
  // workflow believed it was scoped to one.
  for (const params of [
    { resource: "context", operation: "get", query: "q" },
    { resource: "fact", operation: "list" },
  ]) {
    const { ctx, sent } = fakeN8n({ ...params, extra: { run_id: "r1" } });
    await assert.rejects(Korely.prototype.execute.call(ctx), /Run ID does not apply/);
    assert.equal(sent.length, 0, "nothing may be sent");
  }
});

const WRITES = [
  { resource: "memory", operation: "add", content: "Maria is on Pro." },
  { resource: "fact", operation: "write", subject: "maria", predicate: "subscribes_to", object: "Pro" },
];

test("a write that says neither id stops before sending, naming the fields", async () => {
  // Korely 1.0.19 refuses it (422 identity_required); reads need no id.
  for (const params of WRITES) {
    for (const blank of [{ extra: {} }, { userId: "  ", extra: { agent_id: "" } }]) {
      const { ctx, sent } = fakeN8n({ ...params, ...blank });
      await assert.rejects(Korely.prototype.execute.call(ctx), /needs an End User ID or an Agent ID/);
      assert.equal(sent.length, 0, "nothing may be sent");
    }
  }
  for (const params of [{ resource: "memory", operation: "search", query: "q" }, { resource: "fact", operation: "list" }]) {
    assert.equal((await run({ ...params, extra: {} })).length, 1);
  }
});

test("either id is enough, and both go", async () => {
  for (const params of WRITES) {
    const [byUser] = await run({ ...params, userId: "maria", extra: {} });
    assert.equal(byUser.body.user_id, "maria");
    assert.equal("agent_id" in byUser.body, false);
    const [byAgent] = await run({ ...params, extra: { agent_id: "bot" } });
    assert.equal(byAgent.body.agent_id, "bot");
    assert.equal("user_id" in byAgent.body, false);
    const [both] = await run({ ...params, userId: "maria", extra: { agent_id: "bot" } });
    assert.deepEqual([both.body.user_id, both.body.agent_id], ["maria", "bot"]);
  }
});

test("the server's refusal arrives in its own words", async () => {
  // What n8n's request helper throws on a 4xx: the status and the body.
  const message = "user_id or agent_id is required: say whose memory this is";
  const fail = Object.assign(new Error("Request failed with status code 422"), {
    httpCode: "422",
    status: 422,
    response: { status: 422, data: { code: "identity_required", message }, headers: {} },
  });
  const params = { resource: "memory", operation: "add", content: "x", userId: "\u200b", extra: {} };
  const { ctx } = fakeN8n(params, { fail });
  await assert.rejects(Korely.prototype.execute.call(ctx), (e) => {
    assert.equal(e.httpCode, "422");
    assert.equal(e.description, message);
    return true;
  });
  const quiet = fakeN8n(params, { fail, continueOnFail: true });
  const [[item]] = await Korely.prototype.execute.call(quiet.ctx);
  assert.equal(item.json.error, message);
});
