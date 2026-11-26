// The node, run against a real server, with a fake n8n around it.
//
// A node that compiles is not a node that works: what breaks is the paths,
// the field names and the HTTP verb, and the compiler sees none of the three.
// Here is the least of n8n that `execute` needs to run, and the requests
// really go over the network.
import assert from "node:assert/strict";
import test from "node:test";
import { Korely } from "../dist/nodes/Korely/Korely.node.js";

const BASE = process.env.KORELY_BASE_URL;
const KEY = process.env.KORELY_API_KEY;
if (!BASE || !KEY) {
  console.log("needs KORELY_BASE_URL and KORELY_API_KEY: skipped");
  process.exit(0);
}

function fakeN8n(params) {
  const sent = [];
  return {
    sent,
    ctx: {
      getInputData: () => [{ json: {} }],
      getNode: () => ({ name: "Korely", type: "korely" }),
      continueOnFail: () => false,
      getNodeParameter(name, _i, fallback) {
        return name in params ? params[name] : fallback;
      },
      helpers: {
        httpRequestWithAuthentication: {
          async call(_self, _cred, options) {
            sent.push(options);
            const url = new URL(BASE + options.url);
            for (const [k, v] of Object.entries(options.qs ?? {})) {
              if (v !== undefined && v !== "") url.searchParams.set(k, String(v));
            }
            const r = await fetch(url, {
              method: options.method,
              headers: {
                Authorization: "Bearer " + KEY,
                "Content-Type": "application/json",
              },
              body: options.body === undefined ? undefined : JSON.stringify(options.body),
            });
            const text = await r.text();
            if (!r.ok) throw new Error(`${r.status} ${text.slice(0, 200)}`);
            return text ? JSON.parse(text) : {};
          },
        },
      },
    },
  };
}

async function run(params) {
  const { ctx, sent } = fakeN8n(params);
  const [out] = await Korely.prototype.execute.call(ctx);
  return { out, sent };
}

const user = "n8n-" + Math.random().toString(36).slice(2, 8);

test("writes a memory", async () => {
  const { out, sent } = await run({
    resource: "memory", operation: "add", userId: user,
    content: "The customer moved to the Gold plan.",
    extra: { timestamp: "2026-03-15" },
  });
  assert.equal(sent[0].method, "POST");
  assert.equal(sent[0].url, "/v1/memories");
  assert.ok(out[0].json.id, "no id in the answer");
});

test("writes a fact with no model, and closes it on the right day", async () => {
  // With a start date: a fact that began today cannot end in June, and the
  // product now refuses it. That was the defect this bench found.
  const written = await run({
    resource: "fact", operation: "write", userId: user,
    subject: user, predicate: "is_on", object: "Priority Support plan",
    validFrom: "2026-01-10", extra: {},
  });
  const id = written.out[0].json.id;
  assert.ok(id, "the fact has no id");

  const closed = await run({
    resource: "fact", operation: "forget", userId: user,
    factId: id, at: "2026-06-20", extra: {},
  });
  assert.equal(closed.sent[0].method, "POST");
  assert.equal(closed.out[0].json.status, "forgotten");
  assert.ok(String(closed.out[0].json.invalid_at).startsWith("2026-06-20"));

  const before = await run({
    resource: "fact", operation: "list", userId: user,
    asOf: "2026-04-01", extra: {},
  });
  const objects = before.out[0].json.facts.map((f) => f.object);
  assert.ok(objects.includes("Priority Support plan"), `in April: ${objects}`);

  const today = await run({
    resource: "fact", operation: "list", userId: user, asOf: "", extra: {},
  });
  const now = today.out[0].json.facts.map((f) => f.object);
  assert.ok(!now.includes("Priority Support plan"), `now: ${now}`);
});

test("corrects a fact, and an empty correction is refused", async () => {
  const written = await run({
    resource: "fact", operation: "write", userId: user,
    subject: user, predicate: "lives_in", object: "Rome", extra: {},
  });
  const id = written.out[0].json.id;

  const corrected = await run({
    resource: "fact", operation: "correct", userId: user,
    factId: id, subject: "", predicate: "", object: "Milan", extra: {},
  });
  assert.equal(corrected.sent[0].method, "PATCH");
  assert.equal(corrected.out[0].json.object, "Milan");

  await assert.rejects(
    run({
      resource: "fact", operation: "correct", userId: user,
      factId: id, subject: "", predicate: "", object: "", extra: {},
    }),
    /at least one of subject/,
  );
});

test("the context ready for a prompt", async () => {
  const { out, sent } = await run({
    resource: "context", operation: "get", userId: user,
    query: "which plan is the customer on", extra: {},
  });
  assert.equal(sent[0].method, "GET");
  assert.equal(sent[0].url, "/v1/context");
  assert.ok(typeof out[0].json.context === "string");
});

test("a fact cannot end before it began", async () => {
  const written = await run({
    resource: "fact", operation: "write", userId: user,
    subject: user, predicate: "drives", object: "Fiat", extra: {},
  });
  const id = written.out[0].json.id;
  await assert.rejects(
    run({
      resource: "fact", operation: "forget", userId: user,
      factId: id, at: "2020-01-01", extra: {},
    }),
    /true on no date at all|cannot stop being true/,
  );
});
