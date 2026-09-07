/**
 * The Korely client. A thin, dependency-free HTTP wrapper over the Korely REST
 * API: every method maps 1:1 onto an endpoint. All the intelligence —
 * embeddings, entity + typed-fact extraction, contradiction checking — runs
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
  AgentsPage,
  BatchJob,
  BulkReceipt,
  Context,
  DeleteReceipt,
  Fact,
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
  UsersPage,
} from "./types.js";

export const VERSION = "0.1.1";

const REGIONS: Record<string, string> = { eu: "https://api.korely.ai" };

export interface KorelyOptions {
  /** Your `kor_live_...` key. Falls back to the KORELY_API_KEY env var. */
  apiKey?: string;
  /** EU only for now (data stored and processed in the EU). */
  region?: "eu";
  /** Override the base URL (mainly for testing). */
  baseUrl?: string;
  /** Per-request timeout in milliseconds. Default 30000. */
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
        // role-only / empty message → dropped
      } else {
        const s = String(m).trim();
        if (s) parts.push(s);
      }
    }
    return parts.join("\n");
  }
  return String(content);
}

type Params = Record<string, string | number | boolean | undefined | null>;

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
    this.baseUrl = (
      opts.baseUrl ??
      REGIONS[opts.region ?? "eu"] ??
      REGIONS.eu
    ).replace(/\/+$/, "");
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

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    let resp: Response;
    try {
      resp = await this.fetchImpl(url, {
        method,
        headers,
        body,
        signal: controller.signal,
      });
    } catch (e: any) {
      throw new KorelyError(`Connection error: ${e?.message ?? String(e)}`);
    } finally {
      clearTimeout(timer);
    }

    const text = await resp.text();
    let parsed: any = {};
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = { message: text };
      }
    }
    if (!resp.ok) {
      this.raise(resp.status, parsed, resp.headers.get("retry-after"));
    }
    return parsed && typeof parsed === "object" ? parsed : {};
  }

  private raise(status: number, body: any, retryAfter: string | null): never {
    const code: string | undefined = body?.code;
    const msg: string = body?.message || code || `HTTP ${status}`;
    if (status === 401) throw new AuthenticationError(msg, { status, code });
    if (status === 403) throw new NamespaceForbiddenError(msg, { status, code });
    if (status === 404) throw new NotFoundError(msg, { status, code });
    if (status === 409) throw new StaleWriteError(msg, { status, code });
    if (status === 429) {
      const raw = retryAfter ?? body?.retry_after ?? body?._retry_after;
      let ra: number | undefined;
      if (raw != null) {
        const n = parseInt(String(raw), 10);
        ra = Number.isNaN(n) ? undefined : n;
      }
      throw new QuotaExceededError(msg, { status, code, retryAfter: ra });
    }
    throw new APIError(msg, { status, code });
  }

  // ── memories ────────────────────────────────────────────────────────────
  /**
   * POST /v1/memories — store a memory; resolves to it with extracted facts.
   * `content` is a string, or a list of chat messages (role/content), joined
   * into one block before sending. Pass `timestamp` (ISO date/datetime) for
   * backfill: facts extracted inherit it as `valid_from`.
   */
  async add(content: string | Message[], opts: AddOptions = {}): Promise<Memory> {
    const text = coerceContent(content);
    if (!text.trim()) {
      throw new KorelyError(
        "content is empty — pass a non-blank string or messages with content.",
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
   * POST /v1/memories/search — hybrid retrieval, ranked by score. `limit`
   * defaults to the server default (15) when not passed.
   */
  async search(query: string, opts: SearchOptions = {}): Promise<SearchHit[]> {
    const body = await this.request("POST", "/v1/memories/search", {
      body: {
        query,
        user_id: opts.user_id,
        agent_id: opts.agent_id,
        limit: opts.limit,
      },
    });
    return (body.results ?? []) as SearchHit[];
  }

  /** GET /v1/memories — list a scope, newest first. */
  async getAll(opts: ListOptions = {}): Promise<MemoryPage> {
    return this.request("GET", "/v1/memories", {
      params: {
        user_id: opts.user_id,
        agent_id: opts.agent_id,
        limit: opts.limit ?? 50,
        offset: opts.offset ?? 0,
      },
    });
  }

  /** GET /v1/memories/:id — full content, metadata, extracted facts. */
  async get(memoryId: string): Promise<Memory> {
    return this.request("GET", `/v1/memories/${memoryId}`);
  }

  /**
   * PATCH /v1/memories/:id — re-runs extraction. Pass `expected_updated_at`
   * for optimistic concurrency (throws StaleWriteError instead of clobbering).
   */
  async update(memoryId: string, opts: UpdateOptions): Promise<Memory> {
    return this.request("PATCH", `/v1/memories/${memoryId}`, {
      body: {
        content: opts.content,
        expected_updated_at: opts.expected_updated_at,
      },
    });
  }

  /** DELETE /v1/memories/:id — forget one memory (audited invalidation). */
  async delete(memoryId: string): Promise<DeleteReceipt> {
    return this.request("DELETE", `/v1/memories/${memoryId}`);
  }

  /**
   * DELETE /v1/users/:user_id/memories — forget every memory + fact for one
   * end user in a single call.
   */
  async deleteAll(opts: { user_id: string }): Promise<BulkReceipt> {
    return this.request(
      "DELETE",
      `/v1/users/${opts.user_id}/memories`,
    );
  }

  /**
   * GET /v1/memories/:id/history — the lifecycle timeline of a memory:
   * created / updated / deleted, plus every typed fact it produced.
   */
  async history(memoryId: string): Promise<MemoryHistory> {
    return this.request("GET", `/v1/memories/${memoryId}/history`);
  }

  /**
   * GET /v1/users — the end users you've stored data for (distinct user_id
   * namespaces), each with active memory + fact counts and last-active time.
   */
  async users(opts: UsersOptions = {}): Promise<UsersPage> {
    return this.request("GET", "/v1/users", {
      params: {
        agent_id: opts.agent_id,
        limit: opts.limit ?? 50,
        offset: opts.offset ?? 0,
      },
    });
  }

  // ── agents ────────────────────────────────────────────────────────────────
  /**
   * GET /v1/agents — the agent namespaces you've written under (the distinct
   * non-null agent_id values), each with active memory + fact counts and
   * last-active time, plus the tier agent `cap` and how many slots are `used`.
   * The antidote to the agent-cap trap: when a write is rejected with
   * `agent_cap_exceeded`, call this to see which namespaces already exist and
   * reuse one instead of minting a new id.
   */
  async listAgents(opts: ListAgentsOptions = {}): Promise<AgentsPage> {
    return this.request("GET", "/v1/agents", {
      params: {
        limit: opts.limit ?? 50,
        offset: opts.offset ?? 0,
      },
    });
  }

  /**
   * DELETE /v1/agents/:agent_id — hard-delete an agent namespace: purge every
   * memory + fact written under this agent_id and FREE its cap slot
   * (soft-forgetting its data does NOT free the slot). Resolves to the purge
   * counts + an audit id.
   */
  async deleteAgent(agentId: string): Promise<AgentDeleteReceipt> {
    return this.request("DELETE", `/v1/agents/${agentId}`);
  }

  // ── facts ─────────────────────────────────────────────────────────────────
  /**
   * GET /v1/facts — typed (subject, predicate, object) triples with bi-temporal
   * validity. Pass `as_of` (ISO date) for a point-in-time query.
   */
  async getFacts(opts: GetFactsOptions = {}): Promise<Fact[]> {
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
    return (body.facts ?? []) as Fact[];
  }

  /**
   * POST /v1/facts — write a typed (subject, predicate, object) triple directly,
   * skipping extraction. The server runs the contradiction check; the fact is
   * bi-temporal (pass `valid_from` for a historical fact). Resolves to the
   * written Fact, with `invalidated` listing any fact ids it superseded.
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
      },
    });
  }

  /**
   * GET /v1/profile — the assembled profile of one end user: the active typed
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
   * GET /v1/context — one call that assembles a prompt-ready context block
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

  // ── batch ─────────────────────────────────────────────────────────────────
  /** POST /v1/batch — bulk import (up to 500 memory objects), async. */
  async batch(memories: Array<Record<string, unknown>>): Promise<BatchJob> {
    return this.request("POST", "/v1/batch", { body: { memories } });
  }

  /** GET /v1/batch/:id — poll an import job. */
  async batchStatus(jobId: string): Promise<BatchJob> {
    return this.request("GET", `/v1/batch/${jobId}`);
  }
}
