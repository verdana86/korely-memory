/**
 * The Korely client. A thin, dependency-free HTTP wrapper over the Korely REST
 * API: every method maps 1:1 onto an endpoint. All the intelligence
 * (embeddings, entity and typed-fact extraction, contradiction checking) runs
 * server-side, so this stays a small client over the native `fetch`.
 *
 *   import { Korely } from "korely-memory";
 *   const korely = new Korely({ apiKey: "kor_live_..." });
 *   await korely.add("User prefers TypeScript", { agent_id: "coding-assistant" });
 */
import {
  APIError,
  AuthenticationError,
  ConflictError,
  KorelyError,
  NamespaceForbiddenError,
  NotFoundError,
  QuotaExceededError,
  StaleWriteError,
  TooManyBatchesError,
} from "./errors.js";
import { keysOf, unknownOption } from "./options.js";
import type {
  AccountDeleteReceipt,
  AddFactTripleOptions,
  AddOptions,
  AgentDeleteReceipt,
  AgentInitResult,
  AgentScope,
  AgentsPage,
  AuditEvent,
  AuditOptions,
  AuditPage,
  BatchJob,
  BatchMemory,
  BulkReceipt,
  Context,
  DeleteReceipt,
  EventsOptions,
  EventsResponse,
  Fact,
  FactList,
  ForgetReceipt,
  GetContextOptions,
  GetFactsOptions,
  GetProfileOptions,
  IterAuditOptions,
  ListAgentsOptions,
  ListOptions,
  Memory,
  MemoryHistory,
  MemoryPage,
  Message,
  PingResponse,
  Profile,
  SearchHit,
  SearchOptions,
  UpdateOptions,
  UsersOptions,
  UserScope,
  UsersPage,
} from "./types.js";

/** Must equal `version` in package.json (a test checks). It goes out in the
 *  X-Korely-Client header, and said 0.1.1 from 0.1.1 to 0.1.6. */
export const VERSION = "0.1.12";

const REGIONS: Record<string, string> = { eu: "https://api.korely.ai" };

export interface KorelyOptions {
  /** Your `kor_live_...` key. Falls back to the KORELY_API_KEY env var. */
  apiKey?: string;
  /**
   * The API to call: "eu" (api.korely.ai), the only one today. The data is
   * stored in the EU (Helsinki). The model that reads a memory is the
   * project's region, set per project in the dashboard: Europe (gpt-oss-120b
   * on Scaleway, in Paris) or Global (Gemini, by Google), the default.
   */
  region?: "eu";
  /** Override the base URL (a self-hosted install, or testing). */
  baseUrl?: string;
  /** Per-request timeout in milliseconds, response body included. Default 30000. */
  timeoutMs?: number;
  /** Inject a fetch implementation (mainly for testing / older runtimes). */
  fetch?: typeof fetch;
}

/** Where and how `Korely.initAgent()` signs up: the client options a call
 *  without a key can use. */
export interface InitAgentOptions extends Pick<KorelyOptions, "region" | "baseUrl" | "timeoutMs" | "fetch"> {
  /**
   * Where the new project's memories are read by a model: "eu" (gpt-oss-120b
   * on Scaleway, Paris) or "global" (Gemini, by Google), the server's default
   * when left out. Sent only when given (servers since 2026-10-07).
   */
  processingRegion?: "eu" | "global";
}

// The options each method reads (options.ts): the compiler holds every list to
// its type, and every method refuses a key that is not on its list.
const CLIENT_KEYS = keysOf<KorelyOptions>()("apiKey", "region", "baseUrl", "timeoutMs", "fetch");
const INIT_KEYS = keysOf<InitAgentOptions>()("region", "baseUrl", "timeoutMs", "fetch", "processingRegion");
const ADD_KEYS = keysOf<AddOptions>()("agent_id", "user_id", "run_id", "metadata", "timestamp");
const SEARCH_KEYS = keysOf<SearchOptions>()("user_id", "agent_id", "run_id", "metadata", "limit");
const LIST_KEYS = keysOf<ListOptions>()("user_id", "agent_id", "run_id", "limit", "offset");
const UPDATE_KEYS = keysOf<UpdateOptions>()("content", "expected_updated_at");
const USERS_KEYS = keysOf<UsersOptions>()("agent_id", "limit", "offset");
const AGENTS_KEYS = keysOf<ListAgentsOptions>()("limit", "offset");
const EVENTS_KEYS = keysOf<EventsOptions>()("user_id", "status", "limit");
const AUDIT_KEYS = keysOf<AuditOptions>()("user_id", "action", "since", "until", "limit", "offset");
const ITER_AUDIT_KEYS = keysOf<IterAuditOptions>()("user_id", "action", "since", "until", "offset", "page_size");
const FACTS_KEYS = keysOf<GetFactsOptions>()(
  "subject", "entity", "predicate", "predicate_family", "include_invalidated", "as_of",
  "user_id", "agent_id", "limit", "offset");
const TRIPLE_KEYS = keysOf<AddFactTripleOptions>()(
  "user_id", "agent_id", "run_id", "subject_type", "object_is_literal", "confidence",
  "valid_from", "tense");
const PROFILE_KEYS = keysOf<GetProfileOptions>()("user_id", "agent_id", "as_of");
const CONTEXT_KEYS = keysOf<GetContextOptions>()("query", "user_id", "agent_id", "token_budget");
const CONTEXT_EXTRA_KEYS = keysOf<Omit<GetContextOptions, "query">>()("user_id", "agent_id", "token_budget");

/** Refuse an option this method does not read (options.ts). */
function checkOptions(method: string, opts: unknown, known: readonly string[]): void {
  const problem = unknownOption(method, opts, known);
  if (problem) throw new KorelyError(problem);
}

/** add() accepts a string or a list of chat messages, joined to one block. */
function coerceContent(content: string | Message[]): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    const parts: string[] = [];
    for (const m of content) {
      if (m && typeof m === "object") {
        const role = String(m.role ?? "").trim();
        const raw = m.content;
        const body = raw == null ? "" : String(raw).trim();
        if (role && body) parts.push(`${role}: ${body}`);
        else if (body) parts.push(body);
        // role-only / empty message: dropped
      } else {
        const s = String(m).trim();
        if (s) parts.push(s);
      }
    }
    return parts.join("\n");
  }
  return String(content);
}

/**
 * One id as a URL path segment, percent-encoded.
 *
 * Ids went into the path as they came. An end user id is usually somebody
 * else's string, and one carrying `/`, `?` or `#` changed which endpoint was
 * called: `deleteAgent("bot#1")` sent `DELETE /v1/agents/bot`, because `#`
 * starts a fragment the request never sends, and purged the namespace `bot`.
 * An empty id is refused: `get("")` asked for the list endpoint.
 */
function seg(value: unknown, what: string): string {
  // Only a string, or a whole number, is an id. Anything else used to be
  // turned into text by String(): an object became "[object Object]" and a
  // function its source, so the call went out as /v1/facts/[object Object]
  // and came back a confusing 404 or 401 (hosted product's logs, 2026-10-05).
  if (value != null && typeof value !== "string"
      && !(typeof value === "number" && Number.isInteger(value))) {
    throw new KorelyError(`${what} must be a string id, not ${Array.isArray(value) ? "an array" : `a ${typeof value}`}.`);
  }
  const s = value == null ? "" : String(value);
  if (!s) throw new KorelyError(`${what} is empty.`);
  return encodeURIComponent(s);
}

type Params = Record<string, string | number | boolean | undefined | null>;

/**
 * `since` / `until` as the ISO 8601 text the API reads. A string goes as
 * written; a Date as `toISOString()`, which is UTC, so the server never reads
 * it in its own database session's zone.
 */
function moment(value: unknown, what: string): string | undefined {
  if (value == null) return undefined;
  if (typeof value === "string") return value;
  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) throw new KorelyError(`${what} is an invalid Date.`);
    return value.toISOString();
  }
  throw new KorelyError(`${what} must be an ISO 8601 string or a Date, not a ${typeof value}.`);
}

/**
 * The filters of GET /v1/audit, checked before anything is sent. An empty
 * `user_id` or `action` is refused: both servers read an empty filter as no
 * filter, so `audit({ user_id: uid })` with a `uid` that happened to be empty
 * answered with the events of every end user, and the trail is what an access
 * request for ONE person is answered from.
 */
function auditFilters(opts: AuditOptions): Params {
  for (const name of ["user_id", "action"] as const) {
    if (opts[name] === "") {
      throw new KorelyError(
        `${name} is empty: the server reads an empty filter as no filter. ` +
          `Leave it out to mean every ${name.split("_")[0]}.`,
      );
    }
  }
  return {
    user_id: opts.user_id,
    action: opts.action,
    since: moment(opts.since, "since"),
    until: moment(opts.until, "until"),
  };
}

/** FastAPI's `detail` as one line: a sentence as it is, a validation list as
 *  `field: reason` for the first three entries. */
function detailLine(detail: unknown): string | undefined {
  if (typeof detail === "string") return detail.trim() ? detail : undefined;
  if (Array.isArray(detail) && detail.length) {
    const parts = detail.slice(0, 3).map((e: any) => {
      if (!e || typeof e !== "object") return String(e);
      const loc: unknown[] = Array.isArray(e.loc) ? e.loc : [];
      const where = loc.filter((x) => x !== "body").map(String).join(".");
      const msg = String(e.msg ?? "invalid");
      return where ? `${where}: ${msg}` : msg;
    });
    const more = detail.length - 3;
    return parts.join("; ") + (more > 0 ? ` (and ${more} more)` : "");
  }
  return undefined;
}

/**
 * The `code` and `message` of an error answer, whichever server sent it.
 *
 * The hosted service answers `{code, message}` on every 4xx and 5xx. A
 * self-hosted install answers the same two keys next to FastAPI's `detail`
 * (from 2026-09-28 on), and an older one `detail` alone: a sentence, a
 * `{code, message}` pair, or the list of fields that failed validation.
 * The top-level keys win and `detail` is the fallback for each. Reading only
 * the top level turned every older self-hosted error into "HTTP 422" with no
 * code.
 */
function errorFields(status: number, body: any): { code?: string; message: string } {
  const text = (v: unknown): string | undefined =>
    typeof v === "string" && v.trim() ? v : undefined;
  const b = body && typeof body === "object" ? body : {};
  const pair = b.detail && typeof b.detail === "object" && !Array.isArray(b.detail) ? b.detail : {};
  const code = text(b.code) ?? text(pair.code);
  const message =
    text(b.message) ?? text(pair.message) ?? detailLine(b.detail) ?? code ?? `HTTP ${status}`;
  return { code, message };
}

/** The machine fields of a 429 `quota_exceeded` (the Cloud, 2026-10-07), at
 *  the top of the body or inside `detail`; absent ones stay undefined. */
function quotaFields(body: any): { limit?: number; used?: number; resetsAt?: string } {
  const b = body && typeof body === "object" ? body : {};
  const d = b.detail && typeof b.detail === "object" && !Array.isArray(b.detail) ? b.detail : {};
  const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
  const str = (v: unknown) => (typeof v === "string" && v ? v : undefined);
  return { limit: num(b.limit ?? d.limit), used: num(b.used ?? d.used),
           resetsAt: str(b.resets_at ?? d.resets_at) };
}

/** The fields of a 409 (2026-10-07), at the top or inside `detail`, as quotaFields. */
function conflictFields(body: any): { currentFactId?: string } {
  const b = body && typeof body === "object" ? body : {};
  const d = b.detail && typeof b.detail === "object" && !Array.isArray(b.detail) ? b.detail : {};
  const v = b.current_fact_id ?? d.current_fact_id;
  return { currentFactId: typeof v === "string" && v ? v : undefined };
}

/** Retry-After in whole seconds, rounded up; undefined when absent or unreadable. */
function retryAfterSeconds(raw: unknown): number | undefined {
  if (raw == null) return undefined;
  const n = Math.ceil(Number(raw));
  return Number.isFinite(n) && n >= 0 ? n : undefined;
}

/** Fill a renamed field from its deprecated twin, and the twin from it, so
 *  either name reads the number whichever of the two the server sent. */
function fillPair(obj: Record<string, unknown>, current: string, deprecated: string): void {
  if (obj[current] == null && obj[deprecated] != null) obj[current] = obj[deprecated];
  if (obj[deprecated] == null && obj[current] != null) obj[deprecated] = obj[current];
}


/** Give a `{items: T[], …}` response an iterator over its items.
 *
 *  The Python SDK returns pages you can loop over directly; this one returned
 *  the wrapper, so the same call read differently in the two languages while
 *  the documentation said "same idea". The wrapper keeps all its fields, so
 *  nothing that worked before stops working. */
function iterableOver<T, K extends string, P extends Record<K, T[]>>(
  page: P,
  key: K,
): P & Iterable<T> {
  const out = page as P & Iterable<T>;
  Object.defineProperty(out, Symbol.iterator, {
    value: () => (page[key] ?? [])[Symbol.iterator](),
    enumerable: false,
  });
  return out;
}

/** Hosts that are ours. A key we issued must not be handed to somebody else's
 *  machine, and a key issued by somebody else's install must not be sent to us. */
const OUR_HOSTS = ["korely.ai"];

function isOurs(baseUrl: string): boolean {
  let host = "";
  try {
    host = new URL(baseUrl).hostname.toLowerCase();
  } catch {
    return false;
  }
  return OUR_HOSTS.some((h) => host === h || host.endsWith("." + h));
}

/**
 * Stop before the request, not after the 401.
 *
 * A key carries where it came from in its first nine characters: `kor_live_`
 * from the hosted service, `kor_self_` from an install somebody runs
 * themselves. When the key says one thing and the address says another, the
 * only outcomes are a refusal and a memory that travelled to reach it.
 *
 * That is the failure 0.1.11 half-solved. It made the environment variable win
 * over the region default, so somebody who had set both stopped talking to us
 * by accident. It did nothing for the case where only one of the two is set,
 * which is the same mistake with one hand tied.
 *
 * No override on purpose. If somebody is genuinely fronting the hosted service
 * with their own domain this refuses them, and we will hear about it, which is
 * a better way to learn the case is real than shipping a switch for a user who
 * may not exist.
 */
function refuseAMismatchedPair(apiKey: string, baseUrl: string): void {
  const ours = isOurs(baseUrl);
  if (apiKey.startsWith("kor_self_") && ours) {
    throw new KorelyError(
      "This key was issued by an install you run yourself (kor_self_), and " +
        `${baseUrl} is the hosted Korely service. It would be refused there, ` +
        "but the memory would arrive first. Point baseUrl at your own server, " +
        "or set KORELY_BASE_URL.",
    );
  }
  if (apiKey.startsWith("kor_live_") && !ours) {
    throw new KorelyError(
      "This key was issued by the hosted Korely service (kor_live_), and " +
        `${baseUrl} is not it. Sending it there hands a credential we issued ` +
        "to a machine that is not ours. Use a key minted by that install " +
        "(kor_self_), or drop baseUrl to reach the hosted service.",
    );
  }
}

/** What one exchange needs: where, how long, with which fetch, and the key
 *  (none only for `Korely.initAgent()`, the call that is how a key is got). */
interface Transport {
  baseUrl: string;
  timeoutMs: number;
  fetchImpl: typeof fetch;
  apiKey?: string;
}

/** One HTTP exchange: the answer's JSON, or the error the status maps to. */
async function exchange(
  t: Transport,
  method: string,
  path: string,
  opts: { params?: Params; body?: unknown } = {},
): Promise<any> {
  let url = t.baseUrl + path;
  if (opts.params) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(opts.params)) {
      if (v !== undefined && v !== null) qs.append(k, String(v));
    }
    const s = qs.toString();
    if (s) url += "?" + s;
  }

  const headers: Record<string, string> = {
    Accept: "application/json",
    "X-Korely-Client": `korely-js/${VERSION}`,
  };
  // No key only for initAgent(): that call is how a key is obtained, and an
  // empty `Bearer ` is not "no key" to a server.
  if (t.apiKey) headers.Authorization = `Bearer ${t.apiKey}`;
  let body: string | undefined;
  if (opts.body !== undefined) {
    body = JSON.stringify(opts.body);
    headers["Content-Type"] = "application/json";
  }

  // The timer covers the whole exchange, body included. It used to be
  // cleared as soon as the headers arrived, so a server that sent headers
  // and then stalled kept `await resp.text()` waiting forever, whatever
  // `timeoutMs` said.
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, t.timeoutMs);
  const failed = (e: any): KorelyError =>
    new KorelyError(
      timedOut
        ? `Request timed out after ${t.timeoutMs} ms (${method} ${path}).`
        : `Connection error: ${e?.message ?? String(e)}`,
    );
  let resp: Response;
  let text: string;
  try {
    try {
      resp = await t.fetchImpl(url, {
        method,
        headers,
        body,
        signal: controller.signal,
      });
    } catch (e: any) {
      throw failed(e);
    }
    try {
      text = await resp.text();
    } catch (e: any) {
      throw failed(e);
    }
  } finally {
    clearTimeout(timer);
  }

  let parsed: any = {};
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      if (resp.ok) {
        // A 200 carrying a proxy's HTML page is not a result.
        throw new KorelyError(
          `The server answered ${resp.status} to ${method} ${path} with a body ` +
            `that is not JSON: ${JSON.stringify(text.slice(0, 200))}`,
          { status: resp.status },
        );
      }
      parsed = { message: text };
    }
  }
  if (!resp.ok) {
    raiseFor(resp.status, parsed, resp.headers.get("retry-after"));
  }
  return parsed && typeof parsed === "object" ? parsed : {};
}

/** The error an answer that is not a success maps to, by status and code. */
function raiseFor(status: number, body: any, retryAfter: string | null): never {
  const { code, message: msg } = errorFields(status, body);
  // Retry-After on every status (2026-10-06), not only on a 429: the Cloud's
  // 503 `writes_paused` sends it too, and it is the one number that says
  // when writes resume.
  const opts = {
    status,
    code,
    retryAfter: retryAfterSeconds(retryAfter ?? body?.retry_after ?? body?._retry_after),
  };
  if (status === 401) throw new AuthenticationError(msg, opts);
  if (status === 403) throw new NamespaceForbiddenError(msg, opts);
  if (status === 404) throw new NotFoundError(msg, opts);
  if (status === 409) {
    // Only `stale_write` is a stale write (2026-10-06). Every 409 was thrown
    // as StaleWriteError, so `deleteAccount()`'s `account_has_login` read as
    // a lost update. Both servers name `stale_write` on every stale update,
    // the self-hosted one since its first release (inside `detail` on an
    // older install, which errorFields reads).
    if (code === "stale_write") throw new StaleWriteError(msg, opts);
    throw new ConflictError(msg, { ...opts, ...conflictFields(body) });
  }
  if (status === 429) {
    // `too_many_batches` has its own class (2026-10-06): with no Retry-After
    // it was indistinguishable by class from a monthly `quota_exceeded`, and
    // it clears as soon as a batch finishes.
    const quota = { ...opts, ...quotaFields(body) };
    if (code === "too_many_batches") throw new TooManyBatchesError(msg, quota);
    throw new QuotaExceededError(msg, quota);
  }
  throw new APIError(msg, opts);
}


/**
 * What `korely init` saved: the key and the server, in ~/.korely/config.json
 * (or $KORELY_CONFIG_HOME/config.json). The Python client reads them; this one
 * did not, so the documented path, `korely init` and then `new Korely()`,
 * threw "No API key" in Node (window A's new-customer test, 2026-10-07). Node
 * only and best effort: a browser or an edge runtime has no such file, and a
 * file that cannot be read leaves the usual missing-key error.
 */
function savedConfig(): { api_key?: unknown; base_url?: unknown } {
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const proc = typeof process !== "undefined" ? (process as any) : undefined;
    if (!proc?.versions?.node) return {};
    // process.getBuiltinModule from Node 20.16 and 22.3, in ESM and CJS alike;
    // `require` in the CJS build of an older Node.
    const builtin = (id: string) =>
      typeof proc.getBuiltinModule === "function"
        ? proc.getBuiltinModule(id)
        : typeof require === "function"
          ? require(id)
          : undefined;
    const fs = builtin("node:fs");
    const os = builtin("node:os");
    const path = builtin("node:path");
    if (!fs || !os || !path) return {};
    const home = proc.env?.KORELY_CONFIG_HOME || path.join(os.homedir(), ".korely");
    const data = JSON.parse(fs.readFileSync(path.join(home, "config.json"), "utf8"));
    return data && typeof data === "object" ? data : {};
  } catch {
    return {};
  }
}

function text(v: unknown): string | undefined {
  return typeof v === "string" && v ? v : undefined;
}

export class Korely {
  readonly apiKey: string;
  readonly baseUrl: string;
  readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(opts: KorelyOptions = {}) {
    // `base_url` and `api_key`, the Python spellings, included: dropped, the
    // requests went to the hosted service instead of the server meant.
    checkOptions("Korely", opts, CLIENT_KEYS);
    const envKey =
      typeof process !== "undefined" ? process.env?.KORELY_API_KEY : undefined;
    // Same order as the Python client: an argument is a decision, a variable
    // a setting, the file what `korely init` was told once.
    const envBase =
      typeof process !== "undefined" ? process.env?.KORELY_BASE_URL : undefined;
    const saved = opts.apiKey && (opts.baseUrl || envBase) ? {} : savedConfig();
    const key = opts.apiKey ?? envKey ?? text(saved.api_key);
    if (!key) {
      throw new KorelyError(
        "No API key. Pass { apiKey: 'kor_live_...' }, set KORELY_API_KEY, " +
          "or run `korely init`, which saves one to ~/.korely/config.json.",
      );
    }
    this.apiKey = key;
    // KORELY_BASE_URL comes before the region default, and not for convenience.
    // Somebody who installed Korely on their own machine sets KORELY_API_KEY and
    // KORELY_BASE_URL, writes `new Korely()`, and expects to be talking to their
    // own server. Without this they are talking to ours: the key is rejected with
    // a 401, so nothing is stored, but the memory travelled in the body of the
    // request before being refused. For a product sold on "your data stays on
    // your machine" that is the one failure that cannot be waved through.
    this.baseUrl = (
      opts.baseUrl ??
      envBase ??
      text(saved.base_url) ??
      REGIONS[opts.region ?? "eu"] ??
      REGIONS.eu
    ).replace(/\/+$/, "");
    refuseAMismatchedPair(this.apiKey, this.baseUrl);
    this.timeoutMs = opts.timeoutMs ?? 30000;
    const f = opts.fetch ?? (globalThis.fetch as typeof fetch | undefined);
    if (!f) {
      throw new KorelyError(
        "No fetch available. On Node < 18 pass a fetch implementation via { fetch }.",
      );
    }
    this.fetchImpl = f;
  }

  /**
   * POST /v1/agents/init, with no key: sign up for a free hobby key.
   *
   * The one call that runs without a key, because it is how one is obtained,
   * so it is static: `await Korely.initAgent("my-app")`, then
   * `new Korely({ apiKey: result.api_key })`. `agentCaller` is a free-form
   * label of who signed up, kept for your reference. The answer carries the
   * key, shown once (save it), its `tier`, `region`, `scopes` and `quotas`.
   * The account it creates has no login: the key is the account, and
   * `deleteAccount({ confirm: true })` closes it.
   *
   * Cloud only. The server is `baseUrl`, else KORELY_BASE_URL, else the
   * region's. Refusals: 403 `signup_disabled` (NamespaceForbiddenError) when
   * self-signup is closed; 429 `signup_rate_limited` (QuotaExceededError, with
   * `retryAfter`) past the new accounts a network may open in a day. The
   * Self-hosted has no such route and answers 404, or 405 where it serves its
   * dashboard: its keys come from its own dashboard.
   */
  static async initAgent(agentCaller?: string, opts: InitAgentOptions = {}): Promise<AgentInitResult> {
    checkOptions("initAgent", opts, INIT_KEYS);
    const where = opts.processingRegion;
    if (where !== undefined && where !== "eu" && where !== "global") {
      throw new KorelyError(`processingRegion is "eu" or "global", not ${JSON.stringify(where)}.`);
    }
    if (agentCaller != null && typeof agentCaller !== "string") {
      // `initAgent({ baseUrl })` with the options first would send them as the
      // label, and the server would refuse a body it cannot read.
      throw new KorelyError(
        `agentCaller is a label, a string, not ${Array.isArray(agentCaller) ? "an array" : `a ${typeof agentCaller}`}: ` +
          "Korely.initAgent('my-app', { baseUrl }).",
      );
    }
    const envBase =
      typeof process !== "undefined" ? process.env?.KORELY_BASE_URL : undefined;
    const baseUrl = (
      opts.baseUrl ??
      envBase ??
      REGIONS[opts.region ?? "eu"] ??
      REGIONS.eu
    ).replace(/\/+$/, "");
    const fetchImpl = opts.fetch ?? (globalThis.fetch as typeof fetch | undefined);
    if (!fetchImpl) {
      throw new KorelyError(
        "No fetch available. On Node < 18 pass a fetch implementation via { fetch }.",
      );
    }
    return exchange({ baseUrl, timeoutMs: opts.timeoutMs ?? 30000, fetchImpl },
      "POST", "/v1/agents/init", {
        body: { agent_caller: agentCaller ?? undefined, processing_region: opts.processingRegion },
      });
  }

  // ── transport ─────────────────────────────────────────────────────────────
  private request(
    method: string,
    path: string,
    opts: { params?: Params; body?: unknown } = {},
  ): Promise<any> {
    return exchange(
      { baseUrl: this.baseUrl, timeoutMs: this.timeoutMs, fetchImpl: this.fetchImpl, apiKey: this.apiKey },
      method, path, opts,
    );
  }

  // ── the key and its account ───────────────────────────────────────────────
  /**
   * GET /v1/ping: does this key work, and what may it do? Both products answer
   * it, with the same shape.
   *
   * The cheapest authenticated call there is: no scope, no rate limit, no
   * quota, so it is how to check a key that was just minted or rotated without
   * spending anything. `users()` was the usual stand-in, and it needs
   * `memories:read` and counts as a query.
   */
  async ping(): Promise<PingResponse> {
    return this.request("GET", "/v1/ping");
  }

  /**
   * DELETE /v1/account?confirm=true: delete the account of this key for good,
   * with every key, project, memory, fact and webhook of it; the key stops
   * working. Cloud only, for an account made by `Korely.initAgent()` or
   * `korely init --agent`, which nobody signs in to: the key is the account,
   * and this is how it is closed (GDPR Art. 17).
   *
   * `{ confirm: true }` is required, and checked here, before anything is
   * sent: without it, a KorelyError whose `code` is `confirmation_required`,
   * the code the server gives the same refusal.
   *
   * An account with a Korely login rejects with 409 `account_has_login`
   * (ConflictError): a key that ended up in a log must not be able to delete
   * it, so it is closed from the app (Settings, Account). The Self-hosted has
   * no such route and answers 404 (NotFoundError), or 405 where it serves its
   * dashboard. No audit event survives: the trail is part of what goes.
   */
  async deleteAccount(opts: { confirm?: boolean } = {}): Promise<AccountDeleteReceipt> {
    checkOptions("deleteAccount", opts, ["confirm"]);
    if (opts?.confirm !== true) {
      throw new KorelyError(
        "deleteAccount() deletes this key's account, every key, memory and fact " +
          "of it, for good. Pass { confirm: true } to mean it.",
        { code: "confirmation_required" },
      );
    }
    return this.request("DELETE", "/v1/account", { params: { confirm: "true" } });
  }

  // ── audit ─────────────────────────────────────────────────────────────────
  /**
   * GET /v1/audit: what this key's project did, and what its agents read,
   * newest first (`ts` descending, then the event's id, so paging with
   * `offset` is stable). Both products; the key needs `memories:read`.
   *
   * `user_id` keeps the events that touched one end user, the shape of an
   * access or erasure request; `action` one kind ("read", "write", "erase"...;
   * an unknown one answers an empty page); `since` and `until` bound `ts`,
   * both inclusive, as ISO 8601 text or a Date. `limit` is 1 to 1000. To read
   * everything, use `iterAudit()`. The page is iterable.
   *
   * Reading the trail counts against no quota and is not itself written to
   * it; it does count against the rate limit. Scoped to the key's account and
   * project, like every read.
   */
  async audit(opts: AuditOptions = {}): Promise<AuditPage> {
    checkOptions("audit", opts, AUDIT_KEYS);
    const page: { events: AuditEvent[]; total: number } = await this.request(
      "GET", "/v1/audit", {
        params: { ...auditFilters(opts), limit: opts.limit ?? 100, offset: opts.offset ?? 0 },
      });
    return iterableOver(page, "events");
  }

  /**
   * Every event `audit()` would page through, for an export:
   * `for await (const e of korely.iterAudit({ user_id }))`. The same filters,
   * `page_size` events per request (1000, the most the API gives),
   * `offset += page.length` until `total`.
   *
   * Without `until` the walk pins it to the newest event of its first page.
   * The trail grows while it is read, newest first, and every new event pushed
   * the rest one place down: the next page began with events already returned.
   * The pin makes the export the trail as it stood when it started.
   *
   * A rate limit rejects mid-way, as any call does, and the SDK does not
   * retry. To resume, call again with the same `until` (pin it yourself:
   * `until: new Date()`) and `offset` the number of events already read.
   */
  async *iterAudit(opts: IterAuditOptions = {}): AsyncGenerator<AuditEvent, void, undefined> {
    checkOptions("iterAudit", opts, ITER_AUDIT_KEYS);
    const filters = auditFilters(opts);
    let offset = opts.offset ?? 0;
    let pin = filters.until === undefined && offset === 0;
    for (;;) {
      const page = await this.audit({
        user_id: opts.user_id,
        action: opts.action,
        since: filters.since as string | undefined,
        until: filters.until as string | undefined,
        limit: opts.page_size ?? 1000,
        offset,
      });
      if (!page.events.length) return;
      yield* page.events;
      if (pin && page.events[0].ts) {
        filters.until = page.events[0].ts;
        pin = false;
      }
      offset += page.events.length;
      if (offset >= page.total) return;
    }
  }

  // ── memories ────────────────────────────────────────────────────────────
  /**
   * POST /v1/memories: store a memory; resolves to it with extracted facts.
   * `content` is a string, or a list of chat messages (role/content), joined
   * into one block before sending. Pass `timestamp` (ISO date/datetime) for
   * backfill: facts extracted inherit it as `valid_from`.
   */
  async add(content: string | Message[], opts: AddOptions = {}): Promise<Memory> {
    checkOptions("add", opts, ADD_KEYS);
    const text = coerceContent(content);
    if (!text.trim()) {
      throw new KorelyError(
        "content is empty: pass a non-blank string or messages with content.",
      );
    }
    return this.request("POST", "/v1/memories", {
      body: {
        content: text,
        agent_id: opts.agent_id,
        user_id: opts.user_id,
        run_id: opts.run_id,
        metadata: opts.metadata,
        timestamp: opts.timestamp,
      },
    });
  }

  /**
   * POST /v1/memories/search: semantic search over raw memories, ranked by
   * score (vector similarity to the query). `run_id` scopes to one session,
   * `metadata` filters on what you stored at write time. `limit` defaults to
   * the server default (15), max 50.
   */
  async search(query: string, opts: SearchOptions = {}): Promise<SearchHit[]> {
    checkOptions("search", opts, SEARCH_KEYS);
    const body = await this.request("POST", "/v1/memories/search", {
      body: {
        query,
        user_id: opts.user_id,
        agent_id: opts.agent_id,
        run_id: opts.run_id,
        metadata: opts.metadata,
        limit: opts.limit,
      },
    });
    return (body.results ?? []) as SearchHit[];
  }

  /**
   * GET /v1/memories: list a scope, newest first. The page is iterable.
   * `limit` goes up to 200 (the server capped it at 100 before 2026-09-28);
   * `total` and `offset` walk the rest.
   */
  async getAll(opts: ListOptions = {}): Promise<MemoryPage> {
    checkOptions("getAll", opts, LIST_KEYS);
    const page: { memories: Memory[]; total: number } = await this.request(
      "GET", "/v1/memories", {
        params: {
          user_id: opts.user_id,
          agent_id: opts.agent_id,
          run_id: opts.run_id,
          limit: opts.limit ?? 50,
          offset: opts.offset ?? 0,
        },
      });
    return iterableOver(page, "memories");
  }

  /** GET /v1/memories/:id: full content, metadata, extracted facts. */
  async get(memoryId: string): Promise<Memory> {
    return this.request("GET", `/v1/memories/${seg(memoryId, "memoryId")}`);
  }

  /**
   * PATCH /v1/memories/:id: re-runs extraction. Pass `expected_updated_at`
   * for optimistic concurrency (throws StaleWriteError instead of clobbering).
   */
  async update(memoryId: string, opts: UpdateOptions): Promise<Memory> {
    checkOptions("update", opts, UPDATE_KEYS);
    return this.request("PATCH", `/v1/memories/${seg(memoryId, "memoryId")}`, {
      body: {
        content: opts.content,
        expected_updated_at: opts.expected_updated_at,
      },
    });
  }

  /**
   * DELETE /v1/memories/:id: forget one memory. It drops out of every default
   * read, and the facts only it asserted are invalidated (kept as history,
   * audited). For erasure use `deleteAll`.
   */
  async delete(memoryId: string): Promise<DeleteReceipt> {
    return this.request("DELETE", `/v1/memories/${seg(memoryId, "memoryId")}`);
  }

  /**
   * DELETE /v1/users/:user_id/memories: ERASE every memory and fact of one end
   * user (GDPR Art. 17). Physical deletion, not a flag: nothing is readable
   * afterwards. The audit row (counts, never content) survives.
   *
   * The receipt counts the rows in `memories_deleted` and `facts_deleted`.
   * `memories_forgotten` and `facts_invalidated` are deprecated aliases with
   * the same numbers; whichever pair the server sends fills both.
   */
  async deleteAll(opts: { user_id: string }): Promise<BulkReceipt> {
    checkOptions("deleteAll", opts, ["user_id"]);
    const r: BulkReceipt = await this.request(
      "DELETE",
      `/v1/users/${seg(opts.user_id, "user_id")}/memories`,
    );
    const fields = r as unknown as Record<string, unknown>;
    fillPair(fields, "memories_deleted", "memories_forgotten");
    fillPair(fields, "facts_deleted", "facts_invalidated");
    return r;
  }

  /**
   * GET /v1/memories/:id/history: the lifecycle timeline of a memory,
   * created / updated / deleted, plus every typed fact it produced.
   */
  async history(memoryId: string): Promise<MemoryHistory> {
    return this.request("GET", `/v1/memories/${seg(memoryId, "memoryId")}/history`);
  }

  /**
   * GET /v1/users: the end users you've stored data for (distinct user_id
   * namespaces), each with active memory + fact counts and last-active time.
   */
  async users(opts: UsersOptions = {}): Promise<UsersPage> {
    checkOptions("users", opts, USERS_KEYS);
    const page: { users: UserScope[]; total: number } = await this.request(
      "GET", "/v1/users", {
        params: {
          agent_id: opts.agent_id,
          limit: opts.limit ?? 50,
          offset: opts.offset ?? 0,
        },
      });
    return iterableOver(page, "users");
  }

  // ── agents ────────────────────────────────────────────────────────────────
  /**
   * GET /v1/agents: the agent namespaces written under in this key's project
   * (the distinct non-null agent_id values), each with active memory + fact
   * counts and last-active time. The antidote to the agent-cap trap: when a
   * write is rejected with `agent_cap_exceeded`, call this to see which
   * namespaces already exist and reuse one instead of minting a new id.
   *
   * `total` counts this project's namespaces (exactly the ones this key can
   * delete); `used` counts the cap slots taken across the whole account, by
   * name, which is what the 403 compares with `cap`. `used` is above `total`
   * when other projects use names this one does not. A self-hosted install
   * sets no cap and answers `cap: 0`.
   */
  async listAgents(opts: ListAgentsOptions = {}): Promise<AgentsPage> {
    checkOptions("listAgents", opts, AGENTS_KEYS);
    const page: { agents: AgentScope[]; total: number; cap: number; used: number } =
      await this.request("GET", "/v1/agents", {
        params: {
          limit: opts.limit ?? 50,
          offset: opts.offset ?? 0,
        },
      });
    return iterableOver(page, "agents");
  }

  /**
   * DELETE /v1/agents/:agent_id: hard-delete an agent namespace, purging every
   * memory + fact written under this agent_id in this key's project
   * (soft-forgetting its data does NOT free its cap slot; this does). Resolves
   * to the purge counts, an audit id and `slot_freed`.
   *
   * The namespace must be one `listAgents()` shows for this key: anything else
   * rejects with NotFoundError, including a name only another project of the
   * account uses (the server answered 200 with zero counts for it before
   * 2026-09-28, and freed nothing). The cap counts a name across the account,
   * so while another project still uses the same agent_id its rows stay and
   * `slot_freed` is false.
   */
  async deleteAgent(agentId: string): Promise<AgentDeleteReceipt> {
    return this.request("DELETE", `/v1/agents/${seg(agentId, "agentId")}`);
  }

  // ── facts ─────────────────────────────────────────────────────────────────
  /**
   * GET /v1/facts: typed (subject, predicate, object) triples with bi-temporal
   * validity. Pass `as_of` (ISO date) for a point-in-time query. Resolves to an
   * array that also carries `total`, the matches across all pages.
   */
  async getFacts(opts: GetFactsOptions = {}): Promise<FactList> {
    checkOptions("getFacts", opts, FACTS_KEYS);
    const params: Params = {
      subject: opts.subject,
      entity: opts.entity,
      predicate: opts.predicate,
      predicate_family: opts.predicate_family,
      as_of: opts.as_of,
      user_id: opts.user_id,
      agent_id: opts.agent_id,
      limit: opts.limit ?? 50,
      offset: opts.offset ?? 0,
    };
    if (opts.include_invalidated) params.include_invalidated = "true";
    const body = await this.request("GET", "/v1/facts", { params });
    const facts = [...((body.facts ?? []) as Fact[])] as FactList;
    Object.defineProperty(facts, "total", {
      value: typeof body.total === "number" ? body.total : facts.length,
      enumerable: false,
    });
    return facts;
  }

  /**
   * POST /v1/facts: write a typed (subject, predicate, object) triple directly,
   * skipping extraction. The server runs the contradiction check; the fact is
   * bi-temporal (pass `valid_from` for a historical fact, `tense: "past"` for
   * one that is over). Resolves to the written Fact, with `invalidated`
   * listing any fact ids it superseded.
   */
  async addFactTriple(
    subject: string,
    predicate: string,
    object: string,
    opts: AddFactTripleOptions = {},
  ): Promise<Fact> {
    checkOptions("addFactTriple", opts, TRIPLE_KEYS);
    const fact: Fact = await this.request("POST", "/v1/facts", {
      body: {
        subject,
        predicate,
        object,
        user_id: opts.user_id,
        agent_id: opts.agent_id,
        run_id: opts.run_id,
        subject_type: opts.subject_type ?? "unknown",
        object_is_literal: opts.object_is_literal ?? false,
        confidence: opts.confidence ?? 0.9,
        valid_from: opts.valid_from,
        tense: opts.tense,
      },
    });
    // Whose fact it is: servers since 2026-10-07 answer it; for an older one,
    // what was sent is what it was stored under.
    return {
      ...fact,
      user_id: fact.user_id !== undefined ? fact.user_id : opts.user_id,
      agent_id: fact.agent_id !== undefined ? fact.agent_id : opts.agent_id,
    };
  }

  /**
   * POST /v1/facts/:id/forget: close a fact; it stops being current and stays
   * in history.
   *
   * `at` is the date it STOPPED being true, not the date you noticed. Reading
   * `as_of` a date before it still returns the fact, which is the reason
   * history is kept rather than rows deleted.
   *
   * Idempotent: closing an already-closed fact changes nothing and comes back
   * with `status === "already_forgotten"`.
   *
   * This is the half that makes a no-model write path possible: an agent that
   * knows a fact is finished says so, and nothing has to infer it.
   */
  async forgetFact(factId: string, opts: { at?: string } = {}): Promise<ForgetReceipt> {
    checkOptions("forgetFact", opts, ["at"]);
    return this.request("POST", `/v1/facts/${seg(factId, "factId")}/forget`, {
      body: { at: opts.at },
    });
  }

  /**
   * PATCH /v1/facts/:id: supersede a fact with a corrected one.
   *
   * Not an edit: the old row keeps its dates and gains a pointer to the new
   * one, so `as_of` before the correction still returns what you believed then.
   * At least one of the three fields is required.
   *
   * Resolves to the new Fact in the write shape. Its `invalidated` lists every
   * fact the correction superseded: the corrected one first, then any other
   * that the contradiction check on the new fact closed. It is not always one
   * id. A correction that names the fact as it already stands (same subject,
   * predicate and object, still open) supersedes nothing: the server
   * reconfirms the fact and returns it, same `id`, with `invalidated: []`.
   */
  async correctFact(
    factId: string,
    changes: { subject?: string; predicate?: string; object?: string },
  ): Promise<Fact> {
    return this.request("PATCH", `/v1/facts/${seg(factId, "factId")}`, {
      body: changes,
    });
  }

  /**
   * GET /v1/profile: the assembled profile of one end user, the active typed
   * facts known about them, the end user's own facts first, grouped by family.
   * Pass `as_of` (ISO date) for the point-in-time profile.
   */
  async getProfile(opts: GetProfileOptions): Promise<Profile> {
    checkOptions("getProfile", opts, PROFILE_KEYS);
    return this.request("GET", "/v1/profile", {
      params: {
        user_id: opts.user_id,
        agent_id: opts.agent_id,
        as_of: opts.as_of,
      },
    });
  }

  // ── context ─────────────────────────────────────────────────────────────
  /**
   * GET /v1/context: one call that assembles a prompt-ready context block
   * (profile + relevant facts + memories) within a token budget.
   */
  async getContext(
    opts: GetContextOptions | string,
    extra: Omit<GetContextOptions, "query"> = {},
  ): Promise<Context> {
    // A bare string is the query (2026-09-29): the Vercel AI SDK example in
    // the docs calls `korely.getContext(query)` and got 422 "query required".
    if (typeof opts === "string") checkOptions("getContext", extra, CONTEXT_EXTRA_KEYS);
    else checkOptions("getContext", opts, CONTEXT_KEYS);
    const o: GetContextOptions = typeof opts === "string" ? { ...extra, query: opts } : opts;
    if (!o || typeof o.query !== "string" || !o.query.trim()) {
      throw new KorelyError("getContext needs a query.");
    }
    return this.request("GET", "/v1/context", {
      params: {
        query: o.query,
        user_id: o.user_id,
        agent_id: o.agent_id,
        token_budget: o.token_budget ?? 800,
      },
    });
  }

  // ── processing state ───────────────────────────────────────────────────────
  /**
   * GET /v1/events: which writes have finished being processed.
   *
   * `add()` resolves as soon as the memory is stored; fact extraction runs
   * behind it. Each event carries a memory's `status` ("processing", "ready"
   * or "error"), newest first. `status` filters before `limit` (up to 200) is
   * applied, so `{ status: "error" }` returns the latest errors however many
   * ready writes came after them. `processing` counts every write of this
   * key's project still in flight (of `user_id`, when given), whatever
   * `status` and `limit` say. No webhook fires when extraction finishes, so
   * this is how to know.
   */
  async events(opts: EventsOptions = {}): Promise<EventsResponse> {
    checkOptions("events", opts, EVENTS_KEYS);
    return this.request("GET", "/v1/events", {
      params: {
        user_id: opts.user_id,
        status: opts.status,
        limit: opts.limit ?? 50,
      },
    });
  }

  // ── batch ─────────────────────────────────────────────────────────────────
  /**
   * POST /v1/batch: bulk import, up to 500 memory objects, processed
   * asynchronously. Each object is the body of one `add()` (see
   * `BatchMemory`): `content` and optionally `user_id`, `agent_id`, `run_id`,
   * `metadata` and `timestamp`.
   *
   * `timestamp` means what it means on `add()`: when the events happened.
   * The facts extracted from the item inherit it as `valid_from`, so a
   * migration keeps its real dates; without it an item is dated when it is
   * imported. A value that is not an ISO 8601 date or datetime refuses the
   * whole batch before anything is queued: a 422 (APIError,
   * `code === "invalid_request"`) whose message names the item, e.g.
   * `memories[3].timestamp`. Any key not listed above is refused the same
   * way. Servers older than 2026-09-28 refuse `timestamp` itself.
   *
   * On the Cloud a batch is also refused, before anything is stored, with a
   * 429: `quota_exceeded` (QuotaExceededError) when its memories do not fit in
   * what is left of the month, batches already queued included, and
   * `too_many_batches` (TooManyBatchesError) while three batches are still
   * being imported. The Self-hosted meters nothing and refuses neither.
   */
  async batch(memories: Array<BatchMemory | Record<string, unknown>>): Promise<BatchJob> {
    return this.request("POST", "/v1/batch", { body: { memories } });
  }

  /** GET /v1/batch/:id: poll an import job. */
  async batchStatus(jobId: string): Promise<BatchJob> {
    return this.request("GET", `/v1/batch/${seg(jobId, "jobId")}`);
  }
}
