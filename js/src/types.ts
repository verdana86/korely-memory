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
  /** Write-shape only: ids this fact superseded (from add()/addFactTriple()). */
  invalidated?: string[];
  source_memory_id?: string;
  created_at?: string;
}

export interface Memory {
  id?: string;
  content?: string;
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

export interface MemoryPage {
  memories: Memory[];
  total: number;
}

export interface DeleteReceipt {
  id?: string;
  status?: string;
  facts_invalidated?: number;
  audit_id?: string;
}

export interface BulkReceipt {
  user_id?: string;
  memories_forgotten?: number;
  facts_invalidated?: number;
  audit_id?: string;
}

export interface Context {
  context: string;
  tokens: number;
  sources: string[];
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
  /** "subject predicate object" — set on fact events. */
  fact?: string;
  fact_id?: string;
}

export interface MemoryHistory {
  id?: string;
  events: HistoryEvent[];
}

export interface UserScope {
  user_id?: string;
  memories: number;
  facts: number;
  last_active?: string;
}

export interface UsersPage {
  users: UserScope[];
  total: number;
}

export interface AgentScope {
  agent_id?: string;
  memories: number;
  facts: number;
  last_active?: string;
}

export interface AgentsPage {
  agents: AgentScope[];
  /** Distinct agent namespaces == `used` slots. */
  total: number;
  /** The tier agent cap. */
  cap: number;
  /** Agent slots consumed — reconciles with the `agent_cap_exceeded` 403. */
  used: number;
}

export interface AgentDeleteReceipt {
  agent_id?: string;
  memories_deleted?: number;
  facts_deleted?: number;
  audit_id?: string;
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
  limit?: number;
}

export interface ListOptions {
  user_id?: string;
  agent_id?: string;
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

export interface GetFactsOptions {
  subject?: string;
  /** Matches the subject OR object side. */
  entity?: string;
  predicate?: string;
  predicate_family?: string;
  include_invalidated?: boolean;
  /** ISO date — point-in-time validity ("what was true on 2026-06-01"). */
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
  /** ISO date — for a historical fact. */
  valid_from?: string;
}

export interface GetProfileOptions {
  user_id: string;
  agent_id?: string;
  /** ISO date — the profile as it stood on that date. */
  as_of?: string;
}

export interface GetContextOptions {
  query: string;
  user_id?: string;
  agent_id?: string;
  token_budget?: number;
}
