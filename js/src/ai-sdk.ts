/**
 * korely-memory/ai-sdk: Korely memory for the Vercel AI SDK.
 *
 * Two ways in, and they combine:
 *
 *   import { withKorelyMemory, korelyTools } from "korely-memory/ai-sdk";
 *
 *   // Every call reads the memory first and stores the turn after.
 *   const model = withKorelyMemory("anthropic/claude-sonnet-5.5", { userId: "customer-4812" });
 *
 *   // Or the model decides when to look something up or save it.
 *   const tools = korelyTools({ userId: "customer-4812" });
 *
 * `ai` is an optional peer dependency: only this entry point imports it, so
 * the core client stays without runtime dependencies. The client itself comes
 * from "korely-memory", not from a copy bundled here, so `instanceof
 * KorelyError` holds whichever entry point an error came through.
 *
 * Written against AI SDK 7 (language model specification v4). The parts of
 * the specification read here, prompt messages, text and tool-call parts,
 * stream deltas, are the same in AI SDK 5 and 6, and the one difference that
 * matters (a finish reason that is a string in v5 and an object since v6) is
 * handled, so the peer range covers all three.
 */
import { gateway, jsonSchema, tool, wrapLanguageModel } from "ai";
import type { LanguageModel, LanguageModelMiddleware, Tool } from "ai";
import { Korely, KorelyError } from "korely-memory";
import type { Context } from "korely-memory";

// ── what the API accepts ────────────────────────────────────────────────────
// GET /v1/context and POST /v1/memories/search answer 422 to a query longer
// than 2000 characters, POST /v1/memories to content longer than 16000. A chat
// message is somebody else's text, so it is shortened here instead of turning
// a long paste into an error on every turn.
const QUERY_MAX = 2000;
const CONTENT_MAX = 16000;
/** The user's half of a remembered turn; the reply gets what is left. */
const USER_MAX = 6000;
/** "user: " and "\nassistant: ", which `add()` puts around the two messages. */
const TURN_OVERHEAD = 18;
/** The API's own default for `token_budget`. */
const DEFAULT_TOKEN_BUDGET = 800;
/**
 * How long the context of one turn is reused. A tool loop calls the model once
 * per step with the same latest user message: one read per turn instead of one
 * per step, which is also one quota unit instead of several, and the same
 * system messages on every step, so the provider's prompt cache keeps working.
 */
const CONTEXT_REUSE_MS = 5 * 60_000;
/**
 * A read that failed, or ran past `contextTimeoutMs`, is kept for less,
 * counted from the failure: long enough that the steps of one tool loop do
 * not each wait for the same outage, short enough that the user's next try
 * reads the memory again.
 */
const FAILURE_REUSE_MS = 30_000;
/**
 * How long a call waits for the memory by default. The client's own timeout
 * is 30 s, and a chat that hangs half a minute before answering without its
 * memory is worse than one that answers without it after five seconds.
 */
const DEFAULT_CONTEXT_TIMEOUT_MS = 5000;
/** setTimeout fires at once for a longer delay (2^31 - 1 ms, about 24.8 days). */
const MAX_TIMER_MS = 2 ** 31 - 1;

// ── types, taken from the AI SDK itself ───────────────────────────────────
type TransformArgs = Parameters<NonNullable<LanguageModelMiddleware["transformParams"]>>[0];
type CallParams = TransformArgs["params"];
type Prompt = CallParams["prompt"];
type PromptMessage = Prompt[number];
type StreamResult = Awaited<ReturnType<NonNullable<LanguageModelMiddleware["wrapStream"]>>>;
type StreamPart = StreamResult["stream"] extends ReadableStream<infer P> ? P : never;
type ModelObject = Exclude<LanguageModel, string>;

/** Where a failure happened: reading the context before a call, or storing the turn after it. */
export type KorelyPhase = "context" | "remember";

export interface KorelyToolsOptions {
  /**
   * The end user whose memory is read and written: your user's id, taken
   * from your own auth. Required, and never taken from the model: without
   * it a read would span every end user of the project.
   */
  userId: string;
  /** Agent namespace, when one project runs several agents. */
  agentId?: string;
  /**
   * Session id stored on the memories this integration writes. Reads are not
   * narrowed to it: a new session recalls what the user said in an old one.
   */
  runId?: string;
  /** The client to use. Default: `new Korely()`, configured from the environment (KORELY_API_KEY, KORELY_BASE_URL). */
  client?: Korely;
  /** Token budget of the context block (`token_budget`). Default 800, the API's default. */
  tokenBudget?: number;
  /** Add a "Current date: YYYY-MM-DD" line. Default true: the reader answers better when its prompt carries the date. */
  includeDate?: boolean;
  /** IANA time zone of that date, e.g. "Europe/Rome". Default UTC, the zone of the dates in the facts. */
  timeZone?: string;
}

export interface KorelyMemoryOptions extends KorelyToolsOptions {
  /**
   * Store each finished turn (the latest user message and the reply) as a
   * memory, after the call and without waiting for it. Default true: a memory
   * that nothing writes to has nothing to recall. Turn it off when the app
   * writes memories itself, or has no consent to store the conversation.
   */
  remember?: boolean;
  /**
   * How long a call waits for the memory, in milliseconds. Default 5000: past
   * it the call goes on without the memory, `onError` hears of it (phase
   * "context"), and the answer that arrives later is dropped. 0 or Infinity
   * waits for the client's own `timeoutMs` instead (30 s by default).
   */
  contextTimeoutMs?: number;
  /**
   * Hands over the pending write so a serverless runtime keeps the function
   * alive until it is done, e.g. `waitUntil` from "@vercel/functions" or
   * `after` from "next/server". Without it the write is fire-and-forget.
   */
  waitUntil?: (promise: Promise<unknown>) => void;
  /**
   * Called when reading the context or storing a turn fails. The model call
   * goes on either way: without the context, or without the write. Default:
   * a console warning.
   */
  onError?: (error: unknown, info: { phase: KorelyPhase }) => void;
}

/** The two tools `korelyTools()` returns, ready to spread into `tools`. */
export type KorelyTools = {
  searchMemory: Tool<{ query: string }, string>;
  addMemory: Tool<{ memory: string }, { saved: true; id?: string }>;
};

interface Scope {
  client: Korely;
  userId: string;
  agentId?: string;
  runId?: string;
  tokenBudget: number;
  /** "Current date: ..." for now, or undefined when the date is off. */
  today?: () => string;
}

function scopeOf(options: KorelyToolsOptions, caller: string): Scope {
  const o = options ?? ({} as KorelyToolsOptions);
  if (typeof o.userId !== "string" || !o.userId.trim()) {
    throw new KorelyError(
      `${caller} needs a userId: the id of your end user, from your own auth. ` +
        "Without it a read would span every end user of the project.",
    );
  }
  return {
    client: o.client ?? new Korely(),
    userId: o.userId,
    agentId: o.agentId,
    runId: o.runId,
    tokenBudget: o.tokenBudget ?? DEFAULT_TOKEN_BUDGET,
    today: o.includeDate === false ? undefined : dateLine(o.timeZone),
  };
}

/** The "Current date: YYYY-MM-DD" line, computed at each call. An unknown zone throws now, not on every turn. */
function dateLine(timeZone: string | undefined): () => string {
  if (!timeZone) return () => `Current date: ${new Date().toISOString().slice(0, 10)}`;
  let format: Intl.DateTimeFormat;
  try {
    format = new Intl.DateTimeFormat("en-US", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  } catch {
    throw new KorelyError(`timeZone ${JSON.stringify(timeZone)} is not an IANA time zone, e.g. "Europe/Rome".`);
  }
  return () => {
    const part: Record<string, string> = {};
    for (const { type, value } of format.formatToParts(new Date())) part[type] = value;
    return `Current date: ${part.year}-${part.month}-${part.day}`;
  };
}

/**
 * At most `max` characters: the head and the tail, joined by an ellipsis, so
 * both the request that opens a long message and the question that closes it
 * survive. Never splits a surrogate pair. JavaScript counts UTF-16 units and
 * the server code points, so a string that fits here fits there.
 */
function clip(text: string, max: number): string {
  if (text.length <= max) return text;
  const mark = " … ";
  const headLength = Math.floor((max - mark.length) / 2);
  let head = text.slice(0, headLength);
  let tail = text.slice(text.length - (max - mark.length - headLength));
  if (/[\uD800-\uDBFF]$/.test(head)) head = head.slice(0, -1);
  if (/^[\uDC00-\uDFFF]/.test(tail)) tail = tail.slice(1);
  return head + mark + tail;
}

function isText(part: { type: string }): part is { type: "text"; text: string } {
  return part.type === "text" && typeof (part as { text?: unknown }).text === "string";
}

/** The text of the latest user message, file parts left out. */
function latestUserText(prompt: Prompt): string {
  for (let i = prompt.length - 1; i >= 0; i--) {
    const message = prompt[i];
    if (message.role !== "user") continue;
    const content: unknown = message.content;
    if (typeof content === "string") return content.trim();
    if (!Array.isArray(content)) return "";
    return content
      .filter(isText)
      .map((part) => part.text)
      .join("\n")
      .trim();
  }
  return "";
}

function userMessageCount(prompt: Prompt): number {
  return prompt.filter((message) => message.role === "user").length;
}

/** The block split as the server sends it, or whole from a server older than 2026-10-01. */
interface Parts {
  stable: string;
  volatile: string;
}

function partsOf(ctx: Context): Parts {
  const stable = typeof ctx?.stable === "string" ? ctx.stable : "";
  const volatile = typeof ctx?.volatile === "string" ? ctx.volatile : "";
  if (stable.trim() || volatile.trim()) return { stable, volatile };
  return { stable: "", volatile: typeof ctx?.context === "string" ? ctx.context : "" };
}

/**
 * The memory as system messages. `stable` (the reader note, the profile)
 * reads the same from one call to the next, so it goes first and the
 * provider's prompt cache can reuse it; the date and `volatile` (the facts
 * and memories this question brought) follow, because they change.
 */
function memoryMessages(parts: Parts | undefined, date: string | undefined): PromptMessage[] {
  const messages: PromptMessage[] = [];
  if (parts?.stable.trim()) messages.push({ role: "system", content: parts.stable });
  const turn = [date, parts?.volatile.trim() ? parts.volatile : undefined].filter(Boolean).join("\n\n");
  if (turn) messages.push({ role: "system", content: turn });
  return messages;
}

/**
 * After the app's own system messages, before the conversation. The app's
 * system prompt stays first and as written. System messages also have to sit
 * at the head of the prompt: the Anthropic and Google providers refuse one
 * that comes after a user or assistant message.
 */
function withMemory(prompt: Prompt, messages: PromptMessage[]): Prompt {
  let head = 0;
  while (head < prompt.length && prompt[head].role === "system") head++;
  return [...prompt.slice(0, head), ...messages, ...prompt.slice(head)];
}

/**
 * Whether this call ended the turn. A call that asks for tools is a step of a
 * tool loop, and the reply is still to come. The finish reason is a string in
 * AI SDK 5 and `{ unified, raw }` since 6.
 */
function endsTurn(finishReason: unknown, content: ReadonlyArray<{ type: string }>): boolean {
  const reason =
    typeof finishReason === "string" ? finishReason : (finishReason as { unified?: unknown } | undefined)?.unified;
  if (reason === "tool-calls" || reason === "error") return false;
  return !content.some(
    (part) => part.type === "tool-call" && !(part as { providerExecuted?: boolean }).providerExecuted,
  );
}

/**
 * The wait for the memory, or undefined for no limit of our own (0, Infinity,
 * or a delay setTimeout cannot hold). A value that is not a duration throws
 * now, not on every turn.
 */
function contextLimit(value: number | undefined): number | undefined {
  const ms = value ?? DEFAULT_CONTEXT_TIMEOUT_MS;
  if (typeof ms !== "number" || Number.isNaN(ms) || ms < 0) {
    throw new KorelyError(
      `contextTimeoutMs must be a number of milliseconds, 0 or more, got ${JSON.stringify(value)}. ` +
        "0 or Infinity waits for the client's own timeout.",
    );
  }
  return ms === 0 || ms >= MAX_TIMER_MS ? undefined : ms;
}

function reporter(onError: KorelyMemoryOptions["onError"]): (error: unknown, phase: KorelyPhase) => void {
  return (error, phase) => {
    try {
      if (onError) onError(error, { phase });
      else
        console.warn(
          phase === "context"
            ? "korely-memory/ai-sdk: could not read the memory; this call went on without it."
            : "korely-memory/ai-sdk: could not store the turn.",
          error,
        );
    } catch {
      // A throwing onError must not fail the user's call.
    }
  };
}

/**
 * The middleware behind `withKorelyMemory`, for composing with other
 * middleware in `wrapLanguageModel`. `defaultInstructionsMiddleware` adds its
 * instructions only to a call without system messages, and this adds some, so
 * put that one first in the array (it runs first).
 */
export function korelyMemoryMiddleware(options: KorelyMemoryOptions): LanguageModelMiddleware {
  const scope = scopeOf(options, "korelyMemoryMiddleware");
  const remember = options.remember ?? true;
  const limit = contextLimit(options.contextTimeoutMs);
  const report = reporter(options.onError);
  const waitUntil = options.waitUntil;
  let lastTurn: { key: string; until: number; parts: Promise<Parts | undefined> } | undefined;

  /** The context for this turn: read once, then reused by the turn's other steps. */
  function contextFor(query: string, turn: number): Promise<Parts | undefined> {
    const key = `${turn}\u0000${query}`;
    if (lastTurn && lastTurn.key === key && Date.now() < lastTurn.until) return lastTurn.parts;
    const entry = { key, until: Date.now() + CONTEXT_REUSE_MS, parts: Promise.resolve<Parts | undefined>(undefined) };
    entry.parts = new Promise<Parts | undefined>((resolve) => {
      // The first outcome wins: the answer, a failure, or the limit. What comes
      // after it (the answer past the limit, the client's own timeout) is
      // dropped: it reaches no call, no onError, and does not touch the cache,
      // where a timed-out read stays a failure until its window is over.
      let done = false;
      let timer: ReturnType<typeof setTimeout> | undefined;
      const finish = (parts: Parts | undefined, failure?: { error: unknown }) => {
        if (done) return;
        done = true;
        if (timer !== undefined) clearTimeout(timer);
        if (failure) {
          entry.until = Date.now() + FAILURE_REUSE_MS;
          report(failure.error, "context");
        }
        resolve(parts);
      };
      if (limit !== undefined) {
        timer = setTimeout(
          () => finish(undefined, { error: new KorelyError(`GET /v1/context took longer than ${limit} ms (contextTimeoutMs).`) }),
          limit,
        );
      }
      Promise.resolve()
        .then(() =>
          scope.client.getContext({
            query: clip(query, QUERY_MAX),
            user_id: scope.userId,
            agent_id: scope.agentId,
            token_budget: scope.tokenBudget,
          }),
        )
        .then((ctx) => finish(partsOf(ctx)))
        .catch((error) => finish(undefined, { error }));
    });
    lastTurn = entry;
    return entry.parts;
  }

  function rememberTurn(prompt: Prompt, reply: string): void {
    const user = latestUserText(prompt);
    if (!user || !reply.trim()) return;
    const userPart = clip(user, USER_MAX);
    const replyPart = clip(reply, CONTENT_MAX - TURN_OVERHEAD - userPart.length);
    const write = Promise.resolve()
      .then(() =>
        scope.client.add(
          [
            { role: "user", content: userPart },
            { role: "assistant", content: replyPart },
          ],
          { user_id: scope.userId, agent_id: scope.agentId, run_id: scope.runId },
        ),
      )
      .then(
        () => undefined,
        (error) => report(error, "remember"),
      );
    if (!waitUntil) return;
    try {
      waitUntil(write);
    } catch (error) {
      report(error, "remember");
    }
  }

  return {
    specificationVersion: "v4",

    transformParams: async ({ params }) => {
      const query = latestUserText(params.prompt);
      const parts = query ? await contextFor(query, userMessageCount(params.prompt)) : undefined;
      const added = memoryMessages(parts, scope.today?.());
      return added.length ? { ...params, prompt: withMemory(params.prompt, added) } : params;
    },

    wrapGenerate: async ({ doGenerate, params }) => {
      const result = await doGenerate();
      if (remember) {
        try {
          if (endsTurn(result.finishReason, result.content)) {
            rememberTurn(params.prompt, result.content.filter(isText).map((part) => part.text).join(""));
          }
        } catch (error) {
          report(error, "remember");
        }
      }
      return result;
    },

    wrapStream: async ({ doStream, params }) => {
      const result = await doStream();
      if (!remember) return result;
      let reply = "";
      let toolCall = false;
      let failed = false;
      let finished = false;
      const watch = new TransformStream<StreamPart, StreamPart>({
        transform(part, controller) {
          // Pass the part on first: the write never holds up the stream.
          controller.enqueue(part);
          if (finished) return;
          try {
            if (part.type === "text-delta") reply += part.delta;
            else if (part.type === "tool-call") toolCall ||= !part.providerExecuted;
            else if (part.type === "error") failed = true;
            else if (part.type === "finish") {
              finished = true;
              if (!failed && !toolCall && endsTurn(part.finishReason, [])) rememberTurn(params.prompt, reply);
            }
          } catch (error) {
            report(error, "remember");
          }
        },
      });
      return { ...result, stream: result.stream.pipeThrough(watch) };
    },
  };
}

/** A model id string resolves the way `generateText` resolves it: the global provider, else the AI Gateway. */
function resolveModel(model: LanguageModel): ModelObject {
  if (typeof model !== "string") return model;
  const provider = globalThis.AI_SDK_DEFAULT_PROVIDER ?? gateway;
  return provider.languageModel(model) as ModelObject;
}

/**
 * Wraps a language model so that every call reads the user's memory first and
 * stores the turn after.
 *
 * Before each call, `GET /v1/context` for the latest user message, added as
 * system messages after the app's own: the stable part first, then the
 * current date and the part this question brought. The call waits for that
 * read for at most `contextTimeoutMs` (5 s by default), then goes on without
 * it. After a call that ends the turn, the user message and the reply are
 * stored as one memory (`remember`), and the call does not wait for that
 * write. Neither step can fail the call: a failure or a timeout goes to
 * `onError`, and the call goes on without the context or without the write.
 *
 * Create it per request, with the id of the user making it:
 *
 *   const result = streamText({
 *     model: withKorelyMemory("anthropic/claude-sonnet-5.5", { userId, waitUntil }),
 *     messages,
 *   });
 *
 * A model already wrapped with `defaultInstructionsMiddleware` would lose
 * its default instructions under this one: compose the two with
 * `korelyMemoryMiddleware` instead.
 */
export function withKorelyMemory(
  model: LanguageModel,
  options: KorelyMemoryOptions,
): ReturnType<typeof wrapLanguageModel> {
  const middleware = korelyMemoryMiddleware(options);
  return wrapLanguageModel({ model: resolveModel(model), middleware });
}

type Checked<T> = { success: true; value: T } | { success: false; error: Error };

/**
 * Keeps the one field the model may set. A user, agent or run id the model
 * adds is dropped here, and the ids that reach the API come from the app.
 */
function oneText<K extends string>(value: unknown, key: K, max: number): Checked<Record<K, string>> {
  const raw = value && typeof value === "object" ? (value as Record<string, unknown>)[key] : undefined;
  if (typeof raw !== "string" || !raw.trim()) {
    return { success: false, error: new Error(`${key} must be a non-empty string.`) };
  }
  return { success: true, value: { [key]: clip(raw.trim(), max) } as Record<K, string> };
}

/**
 * Two tools that let the model use the memory on its own initiative:
 * `searchMemory` (what Korely knows that bears on a query: current facts
 * first, then the memories, from `GET /v1/context`) and `addMemory` (store a
 * statement, `POST /v1/memories`).
 *
 * The user, agent and run come from `options`, never from the model: the
 * tools' inputs have no such field, so the model cannot choose whose memory
 * it reads or writes. Create them per request, with the id of the user
 * making it. A failing call rejects, and the AI SDK hands the error to the
 * model as the tool's result.
 */
export function korelyTools(options: KorelyToolsOptions): KorelyTools {
  const scope = scopeOf(options, "korelyTools");
  return {
    searchMemory: tool({
      description:
        "Look up what you know about the user from earlier conversations: facts about their life " +
        "and work, preferences, decisions, plans, and what they said before. Use it when the answer " +
        "may depend on something the user told you in the past.",
      inputSchema: jsonSchema<{ query: string }>(
        {
          type: "object",
          properties: {
            query: {
              type: "string",
              description: 'What to look up, as a short question or a few words, e.g. "preferred contact channel".',
            },
          },
          required: ["query"],
          additionalProperties: false,
        },
        { validate: (value) => oneText(value, "query", QUERY_MAX) },
      ),
      execute: async ({ query }) => {
        const ctx = await scope.client.getContext({
          query,
          user_id: scope.userId,
          agent_id: scope.agentId,
          token_budget: scope.tokenBudget,
        });
        const block =
          typeof ctx?.context === "string" && ctx.context.trim()
            ? ctx.context
            : "Nothing in memory bears on this.";
        return scope.today ? `${scope.today()}\n\n${block}` : block;
      },
    }),
    addMemory: tool({
      description:
        "Save something worth remembering about the user for future conversations: a fact about " +
        "their life or work, a preference, a decision, a plan. One self-contained statement per " +
        "call. Never save passwords, card numbers or other secrets.",
      inputSchema: jsonSchema<{ memory: string }>(
        {
          type: "object",
          properties: {
            memory: {
              type: "string",
              description: 'The statement to remember, e.g. "Maria prefers email over Slack."',
            },
          },
          required: ["memory"],
          additionalProperties: false,
        },
        { validate: (value) => oneText(value, "memory", CONTENT_MAX) },
      ),
      execute: async ({ memory }) => {
        const saved = await scope.client.add(memory, {
          user_id: scope.userId,
          agent_id: scope.agentId,
          run_id: scope.runId,
        });
        return saved?.id ? { saved: true as const, id: saved.id } : { saved: true as const };
      },
    }),
  };
}
