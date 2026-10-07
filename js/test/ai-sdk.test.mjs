// The Vercel AI SDK integration (korely-memory/ai-sdk), with no network and no
// API key: a fake fetch plays the Korely API, a fake language model plays the
// provider, and the real `generateText` and `streamText` drive them. Runs
// against the built package, so build first:
//   npm run build && npm test
//
// The fake model speaks the specification of whichever `ai` is installed (v2
// for AI SDK 5, v3 for 6, v4 for 7), so this file also checks the older majors
// the peer range accepts: install one over the dev dependency and run it.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { generateText, jsonSchema, stepCountIs, streamText, tool, wrapLanguageModel } from "ai";
import { Korely, KorelyError } from "../dist/index.js";
import { korelyMemoryMiddleware, korelyTools, withKorelyMemory } from "../dist/ai-sdk.js";

const require = createRequire(import.meta.url);
const AI_MAJOR = Number(require("ai/package.json").version.split(".")[0]);
const SPEC = AI_MAJOR <= 5 ? "v2" : AI_MAJOR === 6 ? "v3" : "v4";

// ── a fake language model ───────────────────────────────────────────────────

const USAGE =
  SPEC === "v2"
    ? { inputTokens: 10, outputTokens: 5, totalTokens: 15 }
    : {
        inputTokens: { total: 10, noCache: 10, cacheRead: undefined, cacheWrite: undefined },
        outputTokens: { total: 5, text: 5, reasoning: undefined },
      };
const finish = (reason) => (SPEC === "v2" ? reason : { unified: reason, raw: reason });

/**
 * Answers each call with the next step: `{ text }` or `{ tool, input }`, and
 * optionally `finish` (the finish reason) or `streamError` (an error part
 * before the end of the stream). Records what it was sent.
 */
function fakeModel(...steps) {
  const calls = [];
  let n = 0;
  const next = () => steps[Math.min(n++, steps.length - 1)];
  const toolCall = (s) => ({
    type: "tool-call",
    toolCallId: `call-${n}`,
    toolName: s.tool,
    input: JSON.stringify(s.input ?? {}),
  });
  return {
    specificationVersion: SPEC,
    provider: "fake",
    modelId: "fake-model",
    supportedUrls: {},
    calls,
    async doGenerate(params) {
      calls.push(params);
      const s = next();
      return {
        content: s.tool ? [toolCall(s)] : [{ type: "text", text: s.text }],
        finishReason: finish(s.finish ?? (s.tool ? "tool-calls" : "stop")),
        usage: USAGE,
        warnings: [],
      };
    },
    async doStream(params) {
      calls.push(params);
      const s = next();
      const parts = [{ type: "stream-start", warnings: [] }];
      if (s.tool) parts.push(toolCall(s));
      else {
        parts.push({ type: "text-start", id: "t1" });
        for (const piece of s.text.match(/.{1,5}/gsu)) parts.push({ type: "text-delta", id: "t1", delta: piece });
        parts.push({ type: "text-end", id: "t1" });
      }
      if (s.streamError) parts.push({ type: "error", error: new Error("upstream reset") });
      parts.push({ type: "finish", finishReason: finish(s.finish ?? (s.tool ? "tool-calls" : "stop")), usage: USAGE });
      return {
        stream: new ReadableStream({
          start(controller) {
            for (const part of parts) controller.enqueue(part);
            controller.close();
          },
        }),
      };
    },
  };
}

/** The app's system prompt: `instructions` since AI SDK 7, `system` before. */
const instructions = (text) => (AI_MAJOR >= 7 ? { instructions: text } : { system: text });

// ── a fake Korely API ───────────────────────────────────────────────────────

const STABLE = "_The facts below are a compact profile of the user; the memories are the verbatim source of truth._";
const VOLATILE = "## Known facts\n- Maria prefers_contact email (since 2026-09-08)";
const CONTEXT = {
  context: `${STABLE}\n\n${VOLATILE}`,
  tokens: 40,
  sources: ["fct_1"],
  degraded: false,
  degraded_parts: [],
  stable: STABLE,
  volatile: VOLATILE,
  stable_hash: "0f3c",
};

const answer = (status, body) => ({
  ok: status >= 200 && status < 300,
  status,
  headers: { get: () => null },
  text: async () => JSON.stringify(body),
});

function korely({ context = CONTEXT, contextStatus = 200, write } = {}) {
  const calls = [];
  const fetch = async (url, init) => {
    const u = new URL(url);
    const call = {
      method: init.method,
      path: u.pathname,
      query: Object.fromEntries(u.searchParams),
      body: init.body ? JSON.parse(init.body) : undefined,
    };
    calls.push(call);
    if (u.pathname === "/v1/context") {
      return contextStatus === 200
        ? answer(200, context)
        : answer(contextStatus, { code: "search_unavailable", message: "Search is unavailable." });
    }
    if (u.pathname === "/v1/memories" && init.method === "POST") {
      return write ? write(call) : answer(201, { id: "mem_1", status: "processing" });
    }
    return answer(404, { code: "not_found", message: "No such route." });
  };
  return {
    client: new Korely({ apiKey: "kor_self_test", baseUrl: "https://api.test", fetch }),
    reads: () => calls.filter((c) => c.path === "/v1/context"),
    writes: () => calls.filter((c) => c.path === "/v1/memories"),
  };
}

/** A `waitUntil` that keeps the pending writes, so a test can wait for them. */
function pending() {
  const list = [];
  const waitUntil = (promise) => list.push(promise);
  waitUntil.list = list;
  waitUntil.settled = () => Promise.all(list);
  return waitUntil;
}

const utcDay = () => new Date().toISOString().slice(0, 10);
const tick = () => new Promise((resolve) => setImmediate(resolve));

/** Lets the event loop turn (setImmediate is never mocked here) until `condition` holds. */
async function until(condition) {
  for (let i = 0; i < 200 && !condition(); i++) await tick();
  assert.ok(condition(), "the awaited condition never held");
}

const weather = tool({
  description: "The weather in a city.",
  inputSchema: jsonSchema({ type: "object", properties: { city: { type: "string" } }, required: ["city"] }),
  execute: async () => ({ sky: "sunny", celsius: 24 }),
});

// ── withKorelyMemory ────────────────────────────────────────────────────────

test("the memory goes after the app's system prompt, the stable part first", async () => {
  const k = korely();
  const model = fakeModel({ text: "I'll email you." });
  const before = utcDay();
  await generateText({
    model: withKorelyMemory(model, {
      client: k.client,
      userId: "maria",
      agentId: "support",
      tokenBudget: 1200,
      remember: false,
    }),
    ...instructions("You are a support agent."),
    prompt: "How should you contact me?",
  });
  const after = utcDay();

  assert.equal(k.reads().length, 1);
  const { query } = k.reads()[0];
  assert.equal(query.query, "How should you contact me?");
  assert.equal(query.user_id, "maria");
  assert.equal(query.agent_id, "support");
  assert.equal(query.token_budget, "1200");

  const prompt = model.calls[0].prompt;
  assert.deepEqual(
    prompt.map((m) => m.role),
    ["system", "system", "system", "user"],
  );
  assert.equal(prompt[0].content, "You are a support agent.");
  assert.equal(prompt[1].content, STABLE);
  assert.ok(
    [before, after].some((day) => prompt[2].content === `Current date: ${day}\n\n${VOLATILE}`),
    prompt[2].content,
  );
});

test("the finished turn is stored as one memory, without holding up the call", async () => {
  let release;
  const gate = new Promise((resolve) => (release = resolve));
  const k = korely({
    write: async () => {
      await gate;
      return answer(201, { id: "mem_9", status: "processing" });
    },
  });
  const keep = pending();
  const result = await generateText({
    model: withKorelyMemory(fakeModel({ text: "I'll email you from now on." }), {
      client: k.client,
      userId: "maria",
      agentId: "support",
      runId: "chat-7",
      waitUntil: keep,
    }),
    prompt: "Email me, never Slack.",
  });

  // The reply is back while the server has not answered the write yet.
  assert.equal(result.text, "I'll email you from now on.");
  assert.equal(keep.list.length, 1);
  let settled = false;
  keep.list[0].then(() => (settled = true));
  await tick();
  assert.equal(settled, false);

  release();
  await keep.settled();
  assert.equal(k.writes().length, 1);
  assert.deepEqual(k.writes()[0].body, {
    content: "user: Email me, never Slack.\nassistant: I'll email you from now on.",
    user_id: "maria",
    agent_id: "support",
    run_id: "chat-7",
  });
});

test("streamText: the stream arrives whole, then the turn is stored", async () => {
  const k = korely();
  const keep = pending();
  const model = fakeModel({ text: "Noted: email only, no Slack." });
  const result = streamText({
    model: withKorelyMemory(model, { client: k.client, userId: "maria", waitUntil: keep }),
    prompt: "Use email, not Slack.",
  });
  let text = "";
  for await (const delta of result.textStream) text += delta;

  assert.equal(text, "Noted: email only, no Slack.");
  // No system prompt from the app: the memory leads.
  assert.equal(model.calls[0].prompt[0].content, STABLE);
  await keep.settled();
  assert.equal(k.writes().length, 1);
  assert.equal(k.writes()[0].body.content, "user: Use email, not Slack.\nassistant: Noted: email only, no Slack.");
});

test("a tool loop reads the memory once and stores only the final reply", async () => {
  for (const run of [generateText, streamText]) {
    const k = korely();
    const keep = pending();
    const model = fakeModel({ tool: "weather", input: { city: "Rome" } }, { text: "Sunny in Rome, 24°C." });
    const result = run({
      model: withKorelyMemory(model, { client: k.client, userId: "maria", waitUntil: keep }),
      tools: { weather },
      stopWhen: stepCountIs(3),
      prompt: "Weather in Rome?",
    });
    if (run === streamText) for await (const _ of result.textStream);
    else await result;

    assert.equal(model.calls.length, 2, run.name);
    assert.equal(k.reads().length, 1, run.name);
    for (const call of model.calls) assert.equal(call.prompt[0].content, STABLE, run.name);
    await keep.settled();
    assert.equal(k.writes().length, 1, run.name);
    assert.equal(k.writes()[0].body.content, "user: Weather in Rome?\nassistant: Sunny in Rome, 24°C.", run.name);
  }
});

test("a reply that ends in an error is not stored", async () => {
  const k = korely();
  const keep = pending();
  const options = { client: k.client, userId: "maria", waitUntil: keep };
  await generateText({
    model: withKorelyMemory(fakeModel({ text: "Your plan is", finish: "error" }), options),
    prompt: "What plan am I on?",
  });
  const result = streamText({
    model: withKorelyMemory(fakeModel({ text: "Your plan is", streamError: true }), options),
    prompt: "What plan am I on?",
    onError: () => {},
  });
  for await (const _ of result.textStream);
  await tick();
  assert.equal(keep.list.length, 0);
  assert.equal(k.writes().length, 0);
});

test("a new turn reads the memory again, even with the same words", async () => {
  const k = korely();
  const wrapped = withKorelyMemory(fakeModel({ text: "OK." }), {
    client: k.client,
    userId: "maria",
    remember: false,
  });
  await generateText({ model: wrapped, prompt: "yes" });
  await generateText({
    model: wrapped,
    messages: [
      { role: "user", content: "yes" },
      { role: "assistant", content: "OK." },
      { role: "user", content: "yes" },
    ],
  });
  assert.equal(k.reads().length, 2);
});

test("a failed read does not fail the call: no memory, the date still, and onError says where", async () => {
  const k = korely({ contextStatus: 503 });
  const errors = [];
  const model = fakeModel({ text: "Hello!" });
  const result = await generateText({
    model: withKorelyMemory(model, {
      client: k.client,
      userId: "maria",
      remember: false,
      onError: (error, { phase }) => errors.push({ phase, error }),
    }),
    prompt: "Hi",
  });

  assert.equal(result.text, "Hello!");
  const prompt = model.calls[0].prompt;
  assert.deepEqual(
    prompt.map((m) => m.role),
    ["system", "user"],
  );
  assert.match(prompt[0].content, /^Current date: \d{4}-\d{2}-\d{2}$/);
  assert.equal(errors.length, 1);
  assert.equal(errors[0].phase, "context");
  assert.ok(errors[0].error instanceof KorelyError);
  assert.equal(errors[0].error.code, "search_unavailable");
});

test("a failed read serves the whole tool loop, and the next try reads again", async (t) => {
  t.mock.timers.enable({ apis: ["Date"], now: Date.parse("2026-10-05T10:00:00Z") });
  const k = korely({ contextStatus: 503 });
  const errors = [];
  const model = fakeModel({ tool: "weather", input: { city: "Rome" } }, { text: "Sunny." });
  const wrapped = withKorelyMemory(model, {
    client: k.client,
    userId: "maria",
    remember: false,
    onError: (_error, { phase }) => errors.push(phase),
  });
  const ask = () => generateText({ model: wrapped, tools: { weather }, stopWhen: stepCountIs(3), prompt: "Weather in Rome?" });

  await ask();
  assert.equal(model.calls.length, 2);
  assert.equal(k.reads().length, 1); // the second step did not wait for the outage again
  assert.deepEqual(errors, ["context"]);
  assert.equal(model.calls[0].prompt[0].content, "Current date: 2026-10-05");

  t.mock.timers.tick(31_000);
  await ask();
  assert.equal(k.reads().length, 2);
});

// ── contextTimeoutMs ────────────────────────────────────────────────────────

const NOW = Date.parse("2026-10-05T10:00:00Z");

/** A client whose reads answer as `read` says (the n-th read gets n); writes succeed. */
function slowClient(read) {
  const reads = [];
  return {
    reads,
    getContext: (params) => read(params, reads.push(params)),
    add: async () => ({ id: "mem_1", status: "processing" }),
  };
}

test("contextTimeoutMs: a read that never answers holds the call 5 s by default, then it goes on", { timeout: 3000 }, async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: NOW });
  const client = slowClient(() => new Promise(() => {}));
  const errors = [];
  const model = fakeModel({ text: "Hello!" });
  const call = generateText({
    model: withKorelyMemory(model, {
      client,
      userId: "maria",
      remember: false,
      onError: (error, { phase }) => errors.push({ phase, error }),
    }),
    prompt: "Hi",
  });

  await until(() => client.reads.length === 1);
  t.mock.timers.tick(4999);
  await tick();
  assert.equal(model.calls.length, 0); // still waiting for the memory
  t.mock.timers.tick(1);
  const result = await call;

  assert.equal(result.text, "Hello!");
  assert.deepEqual(
    model.calls[0].prompt.map((m) => m.role),
    ["system", "user"],
  );
  assert.equal(model.calls[0].prompt[0].content, "Current date: 2026-10-05");
  assert.equal(errors.length, 1);
  assert.equal(errors[0].phase, "context");
  assert.ok(errors[0].error instanceof KorelyError);
  assert.match(errors[0].error.message, /took longer than 5000 ms \(contextTimeoutMs\)/);
});

test("contextTimeoutMs: the late answer is dropped, and the turn reads again after the failure window", { timeout: 3000 }, async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: NOW });
  const PHONE = "## Known facts\n- Maria prefers_contact phone (since 2026-10-05)";
  let answerLate;
  const client = slowClient((_params, n) =>
    n === 1
      ? new Promise((resolve) => (answerLate = resolve))
      : Promise.resolve({ ...CONTEXT, context: `${STABLE}\n\n${PHONE}`, volatile: PHONE }),
  );
  const errors = [];
  const model = fakeModel({ tool: "weather", input: { city: "Rome" } }, { text: "Sunny." });
  const wrapped = withKorelyMemory(model, {
    client,
    userId: "maria",
    remember: false,
    contextTimeoutMs: 1000,
    onError: (_error, { phase }) => errors.push(phase),
  });
  const ask = () =>
    generateText({ model: wrapped, tools: { weather }, stopWhen: stepCountIs(3), prompt: "Weather in Rome?" });
  const sawMemory = (call) => call.prompt.some((m) => m.role === "system" && m.content.includes("prefers_contact"));

  // The read runs past the limit: both steps of the loop go on without it,
  // and the second step does not wait again.
  const first = ask();
  await until(() => client.reads.length === 1);
  t.mock.timers.tick(1000);
  await first;
  assert.equal(model.calls.length, 2);
  assert.equal(client.reads.length, 1);
  assert.deepEqual(errors, ["context"]);
  assert.ok(!model.calls.some(sawMemory));

  // The answer arrives late: no onError, and the cache does not take it.
  answerLate(CONTEXT);
  await tick();
  assert.deepEqual(errors, ["context"]);
  await ask(); // the same turn, inside the failure window: no read, no memory
  assert.equal(client.reads.length, 1);
  assert.ok(!model.calls.some(sawMemory));

  // Past the window the turn reads again and gets today's answer, not the late one.
  t.mock.timers.tick(31_000);
  await ask();
  assert.equal(client.reads.length, 2);
  const fresh = model.calls.at(-1).prompt;
  assert.equal(fresh[0].content, STABLE);
  assert.equal(fresh[1].content, `Current date: 2026-10-05\n\n${PHONE}`);

  // A read that answered in time leaves no timer behind to report a timeout later.
  t.mock.timers.tick(5000);
  await tick();
  assert.deepEqual(errors, ["context"]);
});

test("contextTimeoutMs: 0 or Infinity waits for the client's own timeout", { timeout: 3000 }, async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: NOW });
  for (const contextTimeoutMs of [0, Infinity]) {
    // Answers after 6 s: later than the default limit, sooner than the client's 30 s.
    const client = slowClient(() => new Promise((resolve) => setTimeout(() => resolve(CONTEXT), 6000)));
    const errors = [];
    const model = fakeModel({ text: "OK." });
    const call = generateText({
      model: withKorelyMemory(model, {
        client,
        userId: "maria",
        remember: false,
        contextTimeoutMs,
        onError: (_error, { phase }) => errors.push(phase),
      }),
      prompt: "Hi",
    });
    await until(() => client.reads.length === 1);
    t.mock.timers.tick(6000);
    await call;
    assert.equal(model.calls[0].prompt[0].content, STABLE, String(contextTimeoutMs));
    assert.deepEqual(errors, [], String(contextTimeoutMs));
  }
});

test("contextTimeoutMs: the client's own timeout, arriving after the limit, is not reported again", { timeout: 3000 }, async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: NOW });
  const client = slowClient(
    () =>
      new Promise((_resolve, reject) =>
        setTimeout(() => reject(new KorelyError("Request timed out after 30000 ms (GET /v1/context).")), 30_000),
      ),
  );
  const errors = [];
  const call = generateText({
    model: withKorelyMemory(fakeModel({ text: "OK." }), {
      client,
      userId: "maria",
      remember: false,
      onError: (error) => errors.push(error.message),
    }),
    prompt: "Hi",
  });
  await until(() => client.reads.length === 1);
  t.mock.timers.tick(5000);
  assert.equal((await call).text, "OK.");
  t.mock.timers.tick(25_000);
  await tick();
  assert.deepEqual(errors, ["GET /v1/context took longer than 5000 ms (contextTimeoutMs)."]);
});

test("contextTimeoutMs refuses what is not a duration, in both entry points", () => {
  const client = slowClient(() => Promise.resolve(CONTEXT));
  const makers = [(o) => withKorelyMemory(fakeModel({ text: "x" }), o), (o) => korelyTools(o)];
  for (const make of makers) {
    for (const contextTimeoutMs of [-1, Number.NaN, "5000"]) {
      assert.throws(() => make({ client, userId: "maria", contextTimeoutMs }), KorelyError, String(contextTimeoutMs));
    }
  }
  assert.equal(client.reads.length, 0);
});

// ── searchMemory and contextTimeoutMs ───────────────────────────────────────

const UNAVAILABLE =
  "Memory is unavailable right now: earlier conversations cannot be checked. Answer from this conversation alone.";
const search = (tools, query = "contact preference") =>
  tools.searchMemory.execute({ query }, { toolCallId: "c1", messages: [] });

test("searchMemory: past 5 s by default it tells the model the memory is unavailable, and onError hears of it", { timeout: 3000 }, async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: NOW });
  const client = slowClient(() => new Promise(() => {}));
  const errors = [];
  const tools = korelyTools({ client, userId: "maria", onError: (error, { phase }) => errors.push({ phase, error }) });
  let answer;
  search(tools).then((out) => (answer = out));

  await until(() => client.reads.length === 1);
  t.mock.timers.tick(4999);
  await tick();
  assert.equal(answer, undefined); // still waiting
  t.mock.timers.tick(1);
  await until(() => answer !== undefined);

  assert.equal(answer, `Current date: 2026-10-05\n\n${UNAVAILABLE}`);
  assert.equal(errors.length, 1);
  assert.equal(errors[0].phase, "search");
  assert.ok(errors[0].error instanceof KorelyError);
  assert.equal(errors[0].error.message, "GET /v1/context took longer than 5000 ms (contextTimeoutMs).");
});

test("searchMemory: the model reads the unavailable note as the tool's result and goes on", { timeout: 3000 }, async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: NOW });
  const client = slowClient(() => new Promise(() => {}));
  const model = fakeModel({ tool: "searchMemory", input: { query: "contact preference" } }, { text: "I can't check right now." });
  const call = generateText({
    model,
    tools: korelyTools({ client, userId: "maria", includeDate: false, onError: () => {} }),
    stopWhen: stepCountIs(3),
    prompt: "How do I like to be contacted?",
  });
  await until(() => client.reads.length === 1);
  t.mock.timers.tick(5000);
  const result = await call;

  assert.equal(result.text, "I can't check right now.");
  const [toolResult] = result.steps[0].toolResults;
  assert.equal(toolResult.output, UNAVAILABLE); // a result, not a tool error
  assert.equal(model.calls.length, 2);
  assert.ok(JSON.stringify(model.calls[1].prompt).includes("Memory is unavailable right now"));
});

test("searchMemory: a late answer, or the client's own timeout after the limit, is ignored", { timeout: 3000 }, async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: NOW });
  let answerLate;
  const client = slowClient((_params, n) => {
    if (n === 1) return new Promise((resolve) => (answerLate = resolve));
    if (n === 2)
      return new Promise((_resolve, reject) =>
        setTimeout(() => reject(new KorelyError("Request timed out after 30000 ms (GET /v1/context).")), 30_000),
      );
    return Promise.resolve(CONTEXT);
  });
  const errors = [];
  const tools = korelyTools({
    client,
    userId: "maria",
    includeDate: false,
    contextTimeoutMs: 1000,
    onError: (error) => errors.push(error.message),
  });
  const LIMIT = "GET /v1/context took longer than 1000 ms (contextTimeoutMs).";

  // An answer after the limit.
  let first;
  search(tools).then((out) => (first = out));
  await until(() => client.reads.length === 1);
  t.mock.timers.tick(1000);
  await until(() => first !== undefined);
  assert.equal(first, UNAVAILABLE);
  answerLate(CONTEXT);
  await tick();
  assert.deepEqual(errors, [LIMIT]);

  // The client's own timeout, firing after the limit.
  let second;
  search(tools).then((out) => (second = out));
  await until(() => client.reads.length === 2);
  t.mock.timers.tick(1000);
  await until(() => second !== undefined);
  assert.equal(second, UNAVAILABLE);
  t.mock.timers.tick(29_000);
  await tick();
  assert.deepEqual(errors, [LIMIT, LIMIT]);

  // A read in time answers normally, and leaves no timer to report later.
  assert.equal(await search(tools), CONTEXT.context);
  t.mock.timers.tick(5000);
  await tick();
  assert.deepEqual(errors, [LIMIT, LIMIT]);
});

test("searchMemory: 0 or Infinity waits for the client's own timeout", { timeout: 3000 }, async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: NOW });
  for (const contextTimeoutMs of [0, Infinity]) {
    const client = slowClient(() => new Promise((resolve) => setTimeout(() => resolve(CONTEXT), 6000)));
    const errors = [];
    const tools = korelyTools({ client, userId: "maria", includeDate: false, contextTimeoutMs, onError: (e) => errors.push(e) });
    const pendingAnswer = search(tools);
    await until(() => client.reads.length === 1);
    t.mock.timers.tick(6000);
    assert.equal(await pendingAnswer, CONTEXT.context, String(contextTimeoutMs));
    assert.deepEqual(errors, [], String(contextTimeoutMs));
  }
});

test("searchMemory: a failure inside the limit still rejects, and reaches onError", async () => {
  const failure = new KorelyError("Connection error: fetch failed");
  const client = slowClient(() => Promise.reject(failure));
  const errors = [];
  const tools = korelyTools({ client, userId: "maria", onError: (error, { phase }) => errors.push({ phase, error }) });
  await assert.rejects(() => search(tools), (error) => error === failure);
  assert.deepEqual(errors, [{ phase: "search", error: failure }]);
});

test("a failed write goes to onError and leaves the reply alone, even when onError throws", async () => {
  const quota = async () => answer(429, { code: "quota_exceeded", message: "Monthly quota used up." });
  const errors = [];
  const keep = pending();
  const result = await generateText({
    model: withKorelyMemory(fakeModel({ text: "Sure." }), {
      client: korely({ write: quota }).client,
      userId: "maria",
      waitUntil: keep,
      onError: (error, { phase }) => errors.push({ phase, code: error.code }),
    }),
    prompt: "Remember that I like tea.",
  });
  assert.equal(result.text, "Sure.");
  await keep.settled();
  assert.deepEqual(errors, [{ phase: "remember", code: "quota_exceeded" }]);

  const throwing = pending();
  const again = await generateText({
    model: withKorelyMemory(fakeModel({ text: "Sure." }), {
      client: korely({ write: quota, contextStatus: 503 }).client,
      userId: "maria",
      waitUntil: throwing,
      onError: () => {
        throw new Error("a broken error handler");
      },
    }),
    prompt: "Remember that I like tea.",
  });
  assert.equal(again.text, "Sure.");
  await throwing.settled();
});

test("remember: false, includeDate: false, and the single block of an older server", async () => {
  const old = korely({ context: { context: VOLATILE, tokens: 9, sources: ["fct_1"] } });
  const model = fakeModel({ text: "OK." });
  await generateText({
    model: withKorelyMemory(model, { client: old.client, userId: "maria", remember: false, includeDate: false }),
    prompt: "Hi",
  });
  assert.deepEqual(
    model.calls[0].prompt.map((m) => m.role),
    ["system", "user"],
  );
  assert.equal(model.calls[0].prompt[0].content, VOLATILE);
  await tick();
  assert.equal(old.writes().length, 0);

  // Nothing remembered yet and no date: the prompt goes as the app wrote it.
  const empty = korely({ context: { context: "", tokens: 0, sources: [], stable: "", volatile: "" } });
  const bare = fakeModel({ text: "OK." });
  await generateText({
    model: withKorelyMemory(bare, { client: empty.client, userId: "maria", includeDate: false, remember: false }),
    prompt: "Hi",
  });
  assert.deepEqual(
    bare.calls[0].prompt.map((m) => m.role),
    ["user"],
  );
});

test("a long message is cut to what the API accepts, head and tail, surrogate pairs whole", async () => {
  const k = korely();
  const keep = pending();
  await generateText({
    model: withKorelyMemory(fakeModel({ text: "b".repeat(20000) }), { client: k.client, userId: "maria", waitUntil: keep }),
    prompt: "a".repeat(3000) + " What plan am I on?",
  });
  await keep.settled();
  const query = k.reads()[0].query.query;
  assert.ok(query.length <= 2000, String(query.length));
  assert.ok(query.startsWith("aaaa"));
  assert.ok(query.endsWith("What plan am I on?"));
  // The stored turn fits POST /v1/memories: the user's 3000 characters whole,
  // the 20000 of the reply cut in the middle.
  const content = k.writes()[0].body.content;
  assert.ok(content.length <= 16000, String(content.length));
  const [userPart, replyPart] = content.split("\nassistant: ");
  assert.ok(userPart === `user: ${"a".repeat(3000)} What plan am I on?`, "the user message is kept whole");
  assert.ok(/^b+ … b+$/.test(replyPart), "the reply is cut in the middle");

  const emoji = korely();
  await generateText({
    model: withKorelyMemory(fakeModel({ text: "OK." }), { client: emoji.client, userId: "maria", remember: false }),
    prompt: "😀".repeat(1500),
  });
  const cut = emoji.reads()[0].query.query;
  assert.ok(cut.length <= 2000);
  assert.doesNotMatch(cut, /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/);
});

test("a model id string resolves through the global provider", async () => {
  const k = korely();
  const model = fakeModel({ text: "Hi Maria." });
  const before = globalThis.AI_SDK_DEFAULT_PROVIDER;
  const asked = [];
  globalThis.AI_SDK_DEFAULT_PROVIDER = {
    specificationVersion: SPEC,
    languageModel: (id) => (asked.push(id), model),
  };
  try {
    const result = await generateText({
      model: withKorelyMemory("fake/model-1", { client: k.client, userId: "maria", remember: false }),
      prompt: "Hi",
    });
    assert.equal(result.text, "Hi Maria.");
    assert.deepEqual(asked, ["fake/model-1"]);
    assert.equal(model.calls[0].prompt[0].content, STABLE);
  } finally {
    if (before === undefined) delete globalThis.AI_SDK_DEFAULT_PROVIDER;
    else globalThis.AI_SDK_DEFAULT_PROVIDER = before;
  }
});

test("korelyMemoryMiddleware composes with other middleware in wrapLanguageModel", async () => {
  const k = korely();
  const model = fakeModel({ text: "OK." });
  const seen = [];
  const spy = { transformParams: async ({ params }) => (seen.push(params.prompt.length), params) };
  await generateText({
    model: wrapLanguageModel({
      model,
      middleware: [spy, korelyMemoryMiddleware({ client: k.client, userId: "maria", remember: false })],
    }),
    prompt: "Hi",
  });
  // The spy runs first and sees the prompt before the memory is added.
  assert.deepEqual(seen, [1]);
  assert.equal(model.calls[0].prompt.length, 3);
});

// ── korelyTools ─────────────────────────────────────────────────────────────

test("the model cannot pick whose memory the tools read or write", async () => {
  const k = korely();
  const model = fakeModel(
    { tool: "searchMemory", input: { query: "contact preference", user_id: "someone-else", agent_id: "other" } },
    { tool: "addMemory", input: { memory: "Prefers email over Slack.", user_id: "someone-else", run_id: "x" } },
    { text: "Done." },
  );
  const before = utcDay();
  const result = await generateText({
    model,
    tools: korelyTools({ client: k.client, userId: "maria", agentId: "support", runId: "chat-7" }),
    stopWhen: stepCountIs(4),
    prompt: "How do I like to be contacted? And remember it.",
  });
  const after = utcDay();

  assert.equal(k.reads().length, 1);
  assert.deepEqual(k.reads()[0].query, {
    query: "contact preference",
    user_id: "maria",
    agent_id: "support",
    token_budget: "800",
  });
  assert.equal(k.writes().length, 1);
  assert.deepEqual(k.writes()[0].body, {
    content: "Prefers email over Slack.",
    user_id: "maria",
    agent_id: "support",
    run_id: "chat-7",
  });

  const outputs = result.steps.flatMap((step) => step.toolResults).map((r) => r.output);
  assert.ok(
    [before, after].some((day) => outputs[0] === `Current date: ${day}\n\n${CONTEXT.context}`),
    outputs[0],
  );
  assert.deepEqual(outputs[1], { saved: true, id: "mem_1" });
});

test("the tools' inputs have no field for a user, an agent or a run", async () => {
  const tools = korelyTools({ client: korely().client, userId: "maria" });
  assert.deepEqual(Object.keys(tools).sort(), ["addMemory", "searchMemory"]);
  for (const [name, t] of Object.entries(tools)) {
    const schema = await t.inputSchema.jsonSchema;
    assert.deepEqual(Object.keys(schema.properties), [name === "searchMemory" ? "query" : "memory"]);
    assert.equal(schema.additionalProperties, false);
    assert.equal(typeof t.description, "string");
  }
  // And what the model adds anyway is dropped before it reaches execute.
  const checked = await tools.addMemory.inputSchema.validate({ memory: "  Likes tea.  ", user_id: "x" });
  assert.deepEqual(checked, { success: true, value: { memory: "Likes tea." } });
  const refused = await tools.searchMemory.inputSchema.validate({ query: "   " });
  assert.equal(refused.success, false);
});

test("searchMemory says so when nothing matches, and can leave the date out", async () => {
  const k = korely({ context: { context: "", tokens: 0, sources: [], stable: "", volatile: "" } });
  const tools = korelyTools({ client: k.client, userId: "maria", includeDate: false });
  const out = await tools.searchMemory.execute({ query: "favourite editor" }, { toolCallId: "c1", messages: [] });
  assert.equal(out, "Nothing in memory bears on this.");
});

// ── options and packaging ───────────────────────────────────────────────────

test("no userId, no memory: every entry point refuses before any request", () => {
  const k = korely();
  const makers = [
    (o) => withKorelyMemory(fakeModel({ text: "x" }), o),
    (o) => korelyMemoryMiddleware(o),
    (o) => korelyTools(o),
  ];
  for (const make of makers) {
    assert.throws(() => make({ client: k.client }), KorelyError);
    assert.throws(() => make({ client: k.client, userId: "   " }), KorelyError);
  }
  assert.throws(() => korelyTools({ client: k.client, userId: "maria", timeZone: "Mars/Olympus_Mons" }), /time zone/);
  assert.equal(k.reads().length + k.writes().length, 0);
});

test("timeZone sets the day of the date line", async () => {
  const k = korely();
  const model = fakeModel({ text: "OK." });
  const zone = "Pacific/Kiritimati"; // UTC+14: a different day from UTC for ten hours of each day
  const day = () => {
    const p = Object.fromEntries(
      new Intl.DateTimeFormat("en-US", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" })
        .formatToParts(new Date())
        .map(({ type, value }) => [type, value]),
    );
    return `${p.year}-${p.month}-${p.day}`;
  };
  const before = day();
  await generateText({
    model: withKorelyMemory(model, { client: k.client, userId: "maria", remember: false, timeZone: zone }),
    prompt: "Hi",
  });
  const line = model.calls[0].prompt[1].content.split("\n")[0];
  assert.ok([before, day()].some((d) => line === `Current date: ${d}`), line);
});

test("the core entry never loads ai; the integration is a subpath with an optional peer", () => {
  const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  assert.deepEqual(pkg.dependencies ?? {}, {});
  assert.equal(pkg.peerDependenciesMeta?.ai?.optional, true);
  assert.match(pkg.peerDependencies.ai, /\^7\.0\.0/);
  assert.deepEqual(pkg.exports["./ai-sdk"], {
    types: "./dist/ai-sdk.d.ts",
    import: "./dist/ai-sdk.js",
    require: "./dist/ai-sdk.cjs",
  });
  for (const file of ["../dist/index.js", "../dist/index.cjs"]) {
    const code = readFileSync(new URL(file, import.meta.url), "utf8");
    assert.doesNotMatch(code, /from\s*["']ai["']|require\(\s*["']ai["']\s*\)/, file);
  }
});

test(
  "the CommonJS build loads and shares the core classes",
  // AI SDK 7 is ESM only: require() reaches it on Node versions that load ESM synchronously.
  { skip: AI_MAJOR >= 7 && !process.features?.require_module },
  () => {
    const cjs = require("../dist/ai-sdk.cjs");
    const core = require("../dist/index.cjs");
    assert.equal(typeof cjs.withKorelyMemory, "function");
    assert.throws(() => cjs.korelyTools({ client: korely().client }), core.KorelyError);
  },
);

test("an option the entry point does not read is refused, in both entry points (2026-10-07)", () => {
  // `agent_id`, the client's spelling, was dropped: the turns were written
  // outside the agent's namespace.
  const client = slowClient(() => Promise.resolve(CONTEXT));
  const makers = [(o) => withKorelyMemory(fakeModel({ text: "x" }), o), (o) => korelyTools(o)];
  for (const make of makers) {
    assert.throws(() => make({ client, userId: "maria", agent_id: "sales" }),
      (e) => e instanceof KorelyError && /unknown option agent_id\. Did you mean agentId\?/.test(e.message));
  }
  assert.throws(() => korelyTools({ client, userId: "maria", remember: false }), /unknown option remember/);
  assert.equal(client.reads.length, 0);
});
