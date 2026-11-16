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
  /** Korely Agent 0.1.26: when the store learned the fact had ended
   *  (`created_at` is when it learned the fact); null while it is open. */
  closed_known_at?: string | null;
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
  /** "note" or "session" (Korely Agent 0.1.42); absent from an older server. */
  kind?: "note" | "session";
}

/** What `search()` may search, each once in `types`: the memories, or the
 *  facts true now (Korely 1.0.13 and later). */
export type SearchKind = "memories" | "facts";

export interface SearchHit {
  id?: string;
  score?: number;
  /** The first 280 characters of the text. */
  snippet?: string;
  user_id?: string;
  agent_id?: string;
  metadata?: Record<string, unknown>;
  /** "memory" or "fact": in every result when the search names `types`
   *  (Korely 1.0.13 and later), absent otherwise. */
  type?: "memory" | "fact" | string;
  /** The whole text (a memory's as stored, a fact's line): only with
   *  `full_text: true` (Korely 1.0.13 and later). */
  content?: string;
  /** A fact's result: the fact as `getFacts()` shows it. Absent for a memory. */
  fact?: Fact;
}

/** Iterable like the Python SDK's MemoryPage: `for (const m of page)` works,
 *  and `page.memories` / `page.total` still do. */
export interface MemoryPage extends Iterable<Memory> {
  memories: Memory[];
  total: number;
}

/**
 * What `delete()` did to one memory. `facts_invalidated` counts the facts it
 * ended; `facts_reopened` the facts that are true again: facts of other
 * memories that this one's facts had closed, given back as new rows
 * (undefined from a server older than Korely 1.0.2, which does not send it).
 */
export interface DeleteReceipt {
  id?: string;
  status?: string;
  facts_invalidated?: number;
  audit_id?: string;
  facts_reopened?: number;
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
  /**
   * True when part of the block could not be retrieved the way a healthy
   * request retrieves it; the block then closes with a NOTE saying so to the
   * model. Servers before 2026-09-28 do not send it.
   */
  degraded?: boolean;
  /** Which part, when `degraded`: "facts" and/or "memories". */
  degraded_parts?: string[];
  /**
   * The head of `context` that does not depend on the question (the reader
   * note; with the profile on, the profile too): the same text from one call
   * to the next. Put it in the system prompt, where the provider's prompt
   * cache can reuse it. `context` is `stable` and `volatile` joined by a blank
   * line, either side possibly empty. Servers before 2026-10-01 do not send it.
   */
  stable?: string;
  /** The rest of `context`: the facts and memories for this question, and the NOTE when `degraded`. */
  volatile?: string;
  /** SHA-256 (hex) of `stable`: the same value means the same prefix, so a cached system prompt is still valid. */
  stable_hash?: string;
}

/**
 * One item of `batch()`: the body of a single `add()`. `content` is required,
 * every other key optional, and a key not listed here refuses the whole batch
 * with a 422.
 */
export interface BatchMemory {
  content: string;
  /**
   * Whose memory the item is: `user_id`, the end user it belongs to, or
   * `agent_id`, the agent writing it. Korely 1.0.19 and later need one of the
   * two in every item: an item with neither refuses the whole batch with a
   * 422 (`code === "identity_required"`) naming `memories[i]`.
   */
  user_id?: string;
  /** The agent writing the item; see `user_id`. */
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
  /** "note" (default) or "session", as on `add()` (Korely Agent 0.1.42). */
  kind?: "note" | "session";
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
  /** Echoes a request for the profile as the store knew it then. */
  known_as_of?: string | null;
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
   * @deprecated Always undefined: no Korely server sends this field, and none
   * ever has. It stays in the type so that code reading it keeps compiling. To see
   * whether a cap slot is free, read `cap` and `used` from `listAgents()`; the
   * cap counts a name across the account, so the slot stays taken while
   * another project of the account still uses the same agent_id.
   */
  slot_freed?: boolean;
  /**
   * The facts of other agents that the namespace's facts had closed, true
   * again as new rows (undefined from a server older than Korely 1.0.2).
   */
  facts_reopened?: number;
}

/** What `ping()` answers: the key works. */
export interface PingResponse {
  /** Always true: a key that does not authenticate is an AuthenticationError instead. */
  ok: boolean;
  /**
   * The plan of the key: hobby, developer, team or scale. On the Self-hosted
   * every key says "hobby", and it limits nothing there.
   */
  tier: string;
  /**
   * Where the key's data is stored, as the server declares it ("eu-hel1" on
   * the Cloud; on the Self-hosted, what its operator set). Not where a memory
   * is read by a model: on the Cloud that is the project's region, Europe or
   * Global (Gemini, by Google).
   */
  region: string;
  /**
   * Where a memory is read by a model, "eu" or "global": the region of the
   * key's project on the Cloud, of the writer the install uses on the
   * Self-hosted. Absent from a server older than 2026-10-07; null with no writer.
   */
  processing_region?: "eu" | "global" | null;
  /** What the key may do, e.g. "memories:read". */
  scopes: string[];
}

/** What `Korely.initAgent()` answers: a new key, when it ends, and what the account may do. */
export interface AgentInitResult {
  /** The key, shown this once and never again: save it. */
  api_key: string;
  tier: string;
  region: string;
  /** Where the new project's memories are read by a model. */
  processing_region?: "eu" | "global";
  scopes: string[];
  /** When the key stops working (ISO 8601, with its time zone). Absent from a
   *  server older than Korely Agent 1.0.10. */
  expires_at?: string;
  /** The same in the server's words: how long the key lasts, the trial's
   *  limits until the account is adopted, and how to adopt it. Absent from a
   *  server older than Korely Agent 1.0.10. */
  note?: string;
  /** The free plan's monthly allowance: writes_per_month, queries_per_month,
   *  agents, the monthly AI budget. With a `note`, it applies once the account
   *  is adopted; until then the trial's limits do. */
  quotas: Record<string, unknown>;
  /** The Terms of Service that creating the account accepts. Absent from a
   *  server older than Korely Agent 0.1.27. */
  terms_url?: string;
  /** The Privacy Policy, likewise. */
  privacy_url?: string;
}

/**
 * What `deleteAccount()` removed. `deleted` is true: the account and every key
 * of it are gone, the one this client holds included.
 */
export interface AccountDeleteReceipt {
  deleted: boolean;
  /** Rows removed per kind (memories, facts, keys...), only the kinds that had any. */
  removed: Record<string, number>;
}

/**
 * Who acted, in an audit event. An open string, not a closed set: one client
 * reads two products whose lists differ, and a server may add a value.
 * "dashboard" is the Cloud's only, "manage" the Self-hosted's only.
 */
export type AuditActor = "rest" | "mcp" | "dashboard" | "manage" | "worker" | string;

/**
 * What happened, in an audit event. An open string for the same reason as
 * AuditActor; "tenant_create" is the Self-hosted's only.
 */
export type AuditAction =
  | "read"
  | "write"
  | "fact_write"
  | "fact_invalidate"
  /** A memory forgotten: out of every read, its text kept (Korely 1.0.23; filed as "erase" before). */
  | "forget"
  | "erase"
  | "key_create"
  | "key_revoke"
  | "tenant_create"
  | string;

/** For a read: what the call returned, by public id, at most 100 of each kind. Ids only, never content. */
export interface AuditRead {
  memories: string[];
  facts: string[];
}

/** One event of the trail `audit()` reads (the API's `AuditRow`). */
export interface AuditEvent {
  /** When it happened, ISO 8601. */
  ts: string;
  actor: AuditActor;
  action: AuditAction;
  result: "ok" | "denied" | "error";
  /** The end user the event touched. */
  user_id?: string | null;
  /** The memory or fact acted on, by public id. */
  target_id?: string | null;
  /** Counts and labels, never content. */
  meta?: Record<string, unknown> | null;
  ip?: string | null;
  /** On a read, what it returned; with `known_as_of` on getFacts() or getContext() (Korely Agent 0.1.26) it rebuilds what an agent knew when it decided. */
  read?: AuditRead | null;
  /**
   * The key that made the call, by the id the dashboard's Keys page lists it
   * with, never the key itself. Null for an act with no key (a person signed
   * in to the dashboard, the command line, the worker); undefined from a
   * server older than Korely 1.0.23.
   */
  api_key_id?: string | null;
}

/** Iterable page of the audit trail, newest first. `total` counts the matches across every page. */
export interface AuditPage extends Iterable<AuditEvent> {
  events: AuditEvent[];
  total: number;
}

// ── method option types (snake_case keys mirror the REST params) ────────────

export interface AddOptions {
  /** The agent writing the memory; see `user_id`. */
  agent_id?: string;
  /**
   * Whose memory it is: `user_id`, the end user it belongs to, or `agent_id`,
   * the agent writing it (or both: that agent's memory of that user). Korely
   * 1.0.19 and later refuse a write with neither, and store nothing: a 422
   * APIError with `code === "identity_required"` and the server's message.
   * An older server accepts it. `run_id` alone is not an identity. Optional in
   * the type so that a program talking to an older server still compiles.
   */
  user_id?: string;
  run_id?: string;
  metadata?: Record<string, unknown>;
  /**
   * ISO date/datetime the events happened (for backfill / migration). Facts
   * extracted inherit it as `valid_from`, so `as_of` point-in-time queries
   * reflect when things were true, not when they were ingested. Defaults to now.
   */
  timestamp?: string;
  /**
   * What the text is: "note" (the default) or "session", the log of a work
   * session (Korely Agent 0.1.42). A read with `exclude_sessions` leaves the
   * sessions and the facts only they stated out. An older server refuses the
   * field with a 422.
   */
  kind?: "note" | "session";
}

/** What `declareName` declared: in `user_id`'s memory (`agent_id` the
 *  namespace, none for the end user's own) the facts about `name` read as the
 *  end user's, since `since`. */
export interface DeclaredName {
  user_id: string;
  name: string;
  agent_id?: string | null;
  since?: string | null;
}

export interface DeclareNameOptions {
  user_id: string;
  agent_id?: string;
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
  /**
   * Leave out the memories whose `score` is below this, 0 to 1 (a cosine
   * similarity, compared as the response shows it). Absent: no floor. Fewer
   * than `limit` results can come back. 0.20 is the value measured on
   * LongMemEval with EmbeddingGemma; the right one depends on your texts.
   */
  min_score?: number;
  /** Leave out the work sessions (`kind: "session"`) and what only they stated (Korely Agent 0.1.42). */
  exclude_sessions?: boolean;
  /**
   * What to search, each kind once: `["memories"]` (what the server searches
   * when it is not sent), `["facts"]`, the facts true now, or both, each kind
   * ranked by its own score and the rankings merged by rank (a fact's score
   * and a memory's are not to be compared). Every result then says its
   * `type`, and a fact's carries `fact`. `run_id`, `metadata` and `min_score`
   * narrow the memories only: with `"facts"` the server answers 422. Sent
   * only when given: Korely 1.0.13 and later; an older server answers 422.
   */
  types?: SearchKind[];
  /**
   * Also return each result's whole text as `content`; `snippet` stays its
   * first 280 characters. A memory can hold 16,000 characters. Sent only
   * when true: Korely 1.0.13 and later; an older server answers 422.
   */
  full_text?: boolean;
}

export interface ListOptions {
  user_id?: string;
  agent_id?: string;
  /** Narrow to one run/session. */
  run_id?: string;
  /** Default 50, up to 200. */
  limit?: number;
  offset?: number;
  /** Leave out the work sessions (`kind: "session"`) and what only they stated (Korely Agent 0.1.42). */
  exclude_sessions?: boolean;
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

export interface AuditOptions {
  /**
   * Only the events that touched this end user. An empty string is refused
   * before sending: the server reads it as no filter.
   */
  user_id?: string;
  /** Only this kind of event. An unknown action answers an empty page; an empty string is refused. */
  action?: AuditAction;
  /** Events at or after it: ISO 8601 text, or a Date (sent as UTC). */
  since?: string | Date;
  /** Events at or before it: ISO 8601 text, or a Date (sent as UTC). */
  until?: string | Date;
  /** Default 100, 1 to 1000. */
  limit?: number;
  offset?: number;
}

export interface IterAuditOptions extends Omit<AuditOptions, "limit"> {
  /** Events per request. Default 1000, the most the API gives. */
  page_size?: number;
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
  /**
   * ISO date: what the store knew on that date (Korely Agent 0.1.26). A move
   * made in September and told in October: `as_of` September gives the new
   * address, `known_as_of` September the old one.
   */
  known_as_of?: string;
  user_id?: string;
  agent_id?: string;
  limit?: number;
  offset?: number;
  /** Leave out the work sessions (`kind: "session"`) and what only they stated (Korely Agent 0.1.42). */
  exclude_sessions?: boolean;
}

export interface AddFactTripleOptions {
  /**
   * Whose fact it is, as on `add()`: `user_id` or `agent_id`. Korely 1.0.19
   * and later refuse a fact with neither (422, `code === "identity_required"`).
   */
  user_id?: string;
  /** The agent writing the fact; see `user_id`. */
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
  /** ISO date: the profile as the store knew it on that date. */
  known_as_of?: string;
  /** Leave out the work sessions (`kind: "session"`) and what only they stated (Korely Agent 0.1.42). */
  exclude_sessions?: boolean;
}

export interface GetContextOptions {
  query: string;
  user_id?: string;
  agent_id?: string;
  token_budget?: number;
  /**
   * ISO date: the block as the store could have built it then, the facts it
   * knew and the memories it had received (Korely Agent 0.1.26).
   */
  known_as_of?: string;
  /** Leave out the work sessions (`kind: "session"`) and what only they stated (Korely Agent 0.1.42). */
  exclude_sessions?: boolean;
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
