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
  KorelyError,
  NamespaceForbiddenError,
  NotFoundError,
  QuotaExceededError,
  StaleWriteError,
} from "./errors.js";
import type {
  AddFactTripleOptions,
  AddOptions,
  AgentDeleteReceipt,
  AgentScope,
  AgentsPage,
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
  ListAgentsOptions,
  ListOptions,
  Memory,
  MemoryHistory,
  MemoryPage,
  Message,
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
export const VERSION = "0.1.6";

const REGIONS: Record<string, string> = { eu: "https://api.korely.ai" };

export interface KorelyOptions {
  /** Your `kor_live_...` key. Falls back to the KORELY_API_KEY env var. */
  apiKey?: string;
  /** EU only for now (data stored and processed in the EU). */
  region?: "eu";
  /** Override the base URL (a self-hosted install, or testing). */
  baseUrl?: string;
  /** Per-request timeout in milliseconds, response body included. Default 30000. */
  timeoutMs?: number;
  /** Inject a fetch implementation (mainly for testing / older runtimes). */
  fetch?: typeof fetch;
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
  const s = value == null ? "" : String(value);
  if (!s) throw new KorelyError(`${what} is empty.`);
  return encodeURIComponent(s);
}

type Params = Record<string, string | number | boolean | undefined | null>;

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

export class Korely {
  readonly apiKey: string;
  readonly baseUrl: string;
  readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(opts: KorelyOptions = {}) {
    const envKey =
      typeof process !== "undefined" ? process.env?.KORELY_API_KEY : undefined;
    const key = opts.apiKey ?? envKey;
    if (!key) {
      throw new KorelyError(
        "No API key. Pass { apiKey: 'kor_live_...' } or set KORELY_API_KEY.",
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
    const envBase =
      typeof process !== "undefined" ? process.env?.KORELY_BASE_URL : undefined;
    this.baseUrl = (
      opts.baseUrl ??
      envBase ??
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

  // ── transport ─────────────────────────────────────────────────────────────
  private async request(
    method: string,
    path: string,
    opts: { params?: Params; body?: unknown } = {},
  ): Promise<any> {
    let url = this.baseUrl + path;
    if (opts.params) {
      const qs = new URLSearchParams();
      for (const [k, v] of Object.entries(opts.params)) {
        if (v !== undefined && v !== null) qs.append(k, String(v));
      }
      const s = qs.toString();
      if (s) url += "?" + s;
    }

    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.apiKey}`,
      Accept: "application/json",
      "X-Korely-Client": `korely-js/${VERSION}`,
    };
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
    }, this.timeoutMs);
    const failed = (e: any): KorelyError =>
      new KorelyError(
        timedOut
          ? `Request timed out after ${this.timeoutMs} ms (${method} ${path}).`
          : `Connection error: ${e?.message ?? String(e)}`,
      );
    let resp: Response;
    let text: string;
    try {
      try {
        resp = await this.fetchImpl(url, {
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
      this.raise(resp.status, parsed, resp.headers.get("retry-after"));
    }
    return parsed && typeof parsed === "object" ? parsed : {};
  }

  private raise(status: number, body: any, retryAfter: string | null): never {
    const { code, message: msg } = errorFields(status, body);
    if (status === 401) throw new AuthenticationError(msg, { status, code });
    if (status === 403) throw new NamespaceForbiddenError(msg, { status, code });
    if (status === 404) throw new NotFoundError(msg, { status, code });
    if (status === 409) throw new StaleWriteError(msg, { status, code });
    if (status === 429) {
      const raw = retryAfter ?? body?.retry_after ?? body?._retry_after;
      let ra: number | undefined;
      if (raw != null) {
        const n = Math.ceil(Number(raw));
        ra = Number.isFinite(n) && n >= 0 ? n : undefined;
      }
      throw new QuotaExceededError(msg, { status, code, retryAfter: ra });
    }
    throw new APIError(msg, { status, code });
  }

  // ── memories ────────────────────────────────────────────────────────────
  /**
   * POST /v1/memories: store a memory; resolves to it with extracted facts.
   * `content` is a string, or a list of chat messages (role/content), joined
   * into one block before sending. Pass `timestamp` (ISO date/datetime) for
   * backfill: facts extracted inherit it as `valid_from`.
   */
  async add(content: string | Message[], opts: AddOptions = {}): Promise<Memory> {
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
    return this.request("POST", "/v1/facts", {
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
  async getContext(opts: GetContextOptions): Promise<Context> {
    return this.request("GET", "/v1/context", {
      params: {
        query: opts.query,
        user_id: opts.user_id,
        agent_id: opts.agent_id,
        token_budget: opts.token_budget ?? 800,
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
   */
  async batch(memories: Array<BatchMemory | Record<string, unknown>>): Promise<BatchJob> {
    return this.request("POST", "/v1/batch", { body: { memories } });
  }

  /** GET /v1/batch/:id: poll an import job. */
  async batchStatus(jobId: string): Promise<BatchJob> {
    return this.request("GET", `/v1/batch/${seg(jobId, "jobId")}`);
  }
}
