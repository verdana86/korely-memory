/**
 * Response shapes from the Korely REST API. Field names match the JSON the API
 * returns (the same shapes documented in the API reference), so what you see in
 * the docs is what you get on the object. Interfaces are open: a server that
 * returns extra fields never breaks an old client.
 */

/** A chat message, accepted by `add()` as an alternative to a plain string. */
export interface Message {
  role?: string;
  content?: string | null;
}

export interface Fact {
  id?: string;
  subject?: string;
  predicate?: string;
  object?: string;
  predicate_family?: string;
  subject_type?: string;
  predicate_raw?: string;
  object_is_literal?: boolean;
  confidence?: number;
  user_id?: string;
  agent_id?: string;
  valid_from?: string;
  invalid_at?: string;
  invalidated_by?: string;
  /**
   * Write shape only (add(), update(), addFactTriple(), correctFact()): the ids
   * this write superseded. On correctFact() that is the corrected fact plus any
   * other the contradiction check closed; empty when the correction restated
   * the fact as it stands, which reconfirms it.
   */
  invalidated?: string[];
  source_memory_id?: string;
  created_at?: string;
  /** What the text said: "current", "past" (history) or "planned" (an intention). */
  tense?: "current" | "past" | "planned" | string;
  /** How many memories stated this fact; above 1 it was reconfirmed, not duplicated. */
  observation_count?: number;
  /** When a memory last restated the fact. */
  last_confirmed_at?: string;
  /** The subject's current name after aliases; `subject` keeps the words as written. */
  subject_canonical?: string;
  /** The object's current name after aliases; `object` keeps the words as written. */
  object_canonical?: string;
  /** Every memory that stated this fact. */
  source_memory_ids?: string[];
}

/**
 * The facts of one `getFacts()` page: a plain array that also carries
 * `total`, how many facts match the filters across all pages. `GET /v1/facts`
 * has always answered with it; `getFacts()` dropped it in 0.1.6 and earlier.
 */
export type FactList = Fact[] & { total: number };

export interface Memory {
  id?: string;
  content?: string;
  /**
   * "processing" while the worker is still mining facts from this memory,
   * which is a few seconds after the write. It is on the wire and was missing
   * here, so the only way to tell an empty `facts` from a not-yet `facts` was
   * to guess or to poll blindly.
   */
  status?: string;
  user_id?: string;
  agent_id?: string;
  run_id?: string;
  metadata?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
  /** Facts extracted from this memory on the write path. */
  facts?: Fact[];
}

export interface SearchHit {
  id?: string;
  score?: number;
  snippet?: string;
  user_id?: string;
  agent_id?: string;
  metadata?: Record<string, unknown>;
}

/** Iterable like the Python SDK's MemoryPage: `for (const m of page)` works,
 *  and `page.memories` / `page.total` still do. */
export interface MemoryPage extends Iterable<Memory> {
  memories: Memory[];
  total: number;
}

export interface DeleteReceipt {
  id?: string;
  status?: string;
  facts_invalidated?: number;
  audit_id?: string;
}

/**
 * What `deleteAll` erased. Every row is physically deleted, and `erasure` is
 * "permanent". `memories_deleted` and `facts_deleted` count the rows: read
 * these. `deleteAll()` fills both names from whichever pair the server sends,
 * so they work against an install that predates them too.
 */
export interface BulkReceipt {
  user_id?: string;
  /** Memories physically deleted. */
  memories_deleted?: number;
  /** Facts physically deleted. */
  facts_deleted?: number;
  /**
   * @deprecated Use `memories_deleted`, same number. The only name the server
   * sent until 2026-09-28: nothing was forgotten, it was deleted.
   */
  memories_forgotten?: number;
  /**
   * @deprecated Use `facts_deleted`, same number. The only name the server
   * sent until 2026-09-28: nothing was invalidated, it was deleted.
   */
  facts_invalidated?: number;
  erasure?: string;
  audit_id?: string;
}

export interface Context {
  context: string;
  tokens: number;
  sources: string[];
}

/**
 * One item of `batch()`: the body of a single `add()`. `content` is required,
 * every other key optional, and a key not listed here refuses the whole batch
 * with a 422.
 */
export interface BatchMemory {
  content: string;
  user_id?: string;
  agent_id?: string;
  run_id?: string;
  metadata?: Record<string, unknown>;
  /**
   * ISO 8601 date or datetime the item's events happened. Its facts inherit it
   * as `valid_from`, exactly as on `add()`. An unreadable value refuses the
   * whole batch with a 422 naming `memories[i].timestamp`, before anything is
   * queued.
   */
  timestamp?: string;
}

export interface BatchJob {
  id?: string;
  status?: string;
  received?: number;
  imported?: number;
  failed?: number;
  errors?: unknown[];
}

export interface Profile {
  user_id?: string;
  as_of?: string | null;
  /** The end user's own facts first, then the rest. */
  facts: Fact[];
  /** Facts grouped by predicate family. */
  by_family: Record<string, Fact[]>;
  total: number;
  /** True when `total` exceeds the facts returned (the profile is capped at 200). */
  truncated: boolean;
}

export type HistoryEventType =
  | "created"
  | "updated"
  | "fact_extracted"
  | "fact_invalidated"
  | "deleted";

export interface HistoryEvent {
  event?: HistoryEventType;
  at?: string;
  /** "subject predicate object", set on fact events. */
  fact?: string;
  fact_id?: string;
}

export interface MemoryHistory {
  id?: string;
  events: HistoryEvent[];
}

/** One memory's processing state, as `events()` reports it. */
export interface MemoryEvent {
  memory_id: string;
  user_id?: string | null;
  agent_id?: string | null;
  /** "processing" | "ready" | "error". */
  status: string;
  created_at?: string | null;
}

export interface EventsResponse {
  /** Newest first. */
  events: MemoryEvent[];
  /**
   * Every write of this key's project still being extracted (in the `user_id`
   * scope, when given), whatever `status` and `limit` say. A `batch()` job that
   * has not stored its memories yet is not counted: wait for `batchStatus()`
   * first.
   */
  processing: number;
}

export interface UserScope {
  user_id?: string;
  memories: number;
  facts: number;
  last_active?: string;
}

/** Iterable, like the Python SDK's UsersPage, so `for (const u of page)`
 *  works and `page.users` / `page.total` still do. The two SDKs claim to be
 *  "the same idea" and this was the one place a reader could see they were
 *  not: Python handed back something you iterate, Node handed back a wrapper.
 *  Adding the iterator keeps every existing caller working. */
export interface UsersPage extends Iterable<UserScope> {
  users: UserScope[];
  total: number;
}

export interface AgentScope {
  agent_id?: string;
  memories: number;
  facts: number;
  last_active?: string;
}

export interface AgentsPage extends Iterable<AgentScope> {
  agents: AgentScope[];
  /**
   * The agent namespaces in this key's project, before pagination: exactly
   * the ones this key can delete. (Until 2026-09-28 the server counted the
   * whole account here, so it always equalled `used`.)
   */
  total: number;
  /** The plan's agent cap; 0 on a self-hosted install, which sets no ceiling. */
  cap: number;
  /**
   * Agent slots taken across the whole account, by name; reconciles with the
   * `agent_cap_exceeded` 403. Above `total` when other projects use names this
   * project does not.
   */
  used: number;
}

export interface AgentDeleteReceipt {
  agent_id?: string;
  memories_deleted?: number;
  facts_deleted?: number;
  audit_id?: string;
  /**
   * Whether the agent cap slot is free now. The cap counts a name across the
   * account, so it stays taken (false) while another project of the account
   * still uses the same agent_id. Undefined when the server does not say: a
   * self-hosted install has no cap, and servers before 2026-09-28 did not send
   * it.
   */
  slot_freed?: boolean;
}

// ── method option types (snake_case keys mirror the REST params) ────────────

export interface AddOptions {
  agent_id?: string;
  user_id?: string;
  run_id?: string;
  metadata?: Record<string, unknown>;
  /**
   * ISO date/datetime the events happened (for backfill / migration). Facts
   * extracted inherit it as `valid_from`, so `as_of` point-in-time queries
   * reflect when things were true, not when they were ingested. Defaults to now.
   */
  timestamp?: string;
}

export interface SearchOptions {
  user_id?: string;
  agent_id?: string;
  /** Scope to one run/session. */
  run_id?: string;
  /** Filter on the metadata stored at write time: keys ANDed, compared as strings. */
  metadata?: Record<string, unknown>;
  /** Server default 15, max 50. */
  limit?: number;
}

export interface ListOptions {
  user_id?: string;
  agent_id?: string;
  /** Narrow to one run/session. */
  run_id?: string;
  /** Default 50, up to 200. */
  limit?: number;
  offset?: number;
}

export interface UpdateOptions {
  content: string;
  /** Optimistic concurrency: rejects (StaleWriteError) if it doesn't match. */
  expected_updated_at?: string;
}

export interface UsersOptions {
  agent_id?: string;
  limit?: number;
  offset?: number;
}

export interface ListAgentsOptions {
  limit?: number;
  offset?: number;
}

export interface EventsOptions {
  user_id?: string;
  /** Applied before `limit`: the latest events in that state. */
  status?: "processing" | "ready" | "error";
  /** Default 50, up to 200. */
  limit?: number;
}

export interface GetFactsOptions {
  subject?: string;
  /** Matches the subject OR object side. */
  entity?: string;
  predicate?: string;
  predicate_family?: string;
  include_invalidated?: boolean;
  /** ISO date: point-in-time validity ("what was true on 2026-06-01"). */
  as_of?: string;
  user_id?: string;
  agent_id?: string;
  limit?: number;
  offset?: number;
}

export interface AddFactTripleOptions {
  user_id?: string;
  agent_id?: string;
  run_id?: string;
  subject_type?: string;
  object_is_literal?: boolean;
  confidence?: number;
  /** ISO date, for a historical fact. */
  valid_from?: string;
  /**
   * "current" (the server default), "past" (the fact is over, and closes the
   * open fact it restates) or "planned".
   */
  tense?: "current" | "past" | "planned";
}

export interface GetProfileOptions {
  user_id: string;
  agent_id?: string;
  /** ISO date: the profile as it stood on that date. */
  as_of?: string;
}

export interface GetContextOptions {
  query: string;
  user_id?: string;
  agent_id?: string;
  token_budget?: number;
}


/** What `forgetFact` gives back. `status` is "forgotten" the first time and
 *  "already_forgotten" afterwards; `invalid_at` is the date it stopped being
 *  true, which is the one you passed if you passed one. */
export interface ForgetReceipt {
  id: string;
  status: "forgotten" | "already_forgotten";
  invalid_at?: string | null;
  audit_id?: string | null;
}
