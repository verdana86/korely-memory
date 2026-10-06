/**
 * korely-memory: the JavaScript / TypeScript SDK for Korely Agents.
 *
 * A typed, dependency-free client over the Korely REST API. Every method maps
 * 1:1 onto an endpoint; the moat (typed bi-temporal facts, contradiction
 * checking) runs server-side.
 *
 *   import { Korely } from "korely-memory";
 *
 *   const korely = new Korely({ apiKey: "kor_live_..." });
 *   await korely.add("User prefers TypeScript", { user_id: "dana" });
 *   const ctx = await korely.getContext({ query: "what does the user like?", user_id: "dana" });
 */
export { Korely, VERSION } from "./client.js";
export type { KorelyOptions } from "./client.js";

export {
  KorelyError,
  AuthenticationError,
  NamespaceForbiddenError,
  NotFoundError,
  ConflictError,
  StaleWriteError,
  QuotaExceededError,
  TooManyBatchesError,
  APIError,
} from "./errors.js";

export type {
  Message,
  Fact,
  FactList,
  Memory,
  SearchHit,
  MemoryPage,
  DeleteReceipt,
  BulkReceipt,
  Context,
  BatchJob,
  BatchMemory,
  Profile,
  HistoryEvent,
  HistoryEventType,
  MemoryHistory,
  MemoryEvent,
  EventsResponse,
  UserScope,
  UsersPage,
  AgentScope,
  AgentsPage,
  AgentDeleteReceipt,
  ForgetReceipt,
  PingResponse,
  AddOptions,
  SearchOptions,
  ListOptions,
  UpdateOptions,
  UsersOptions,
  ListAgentsOptions,
  EventsOptions,
  GetFactsOptions,
  AddFactTripleOptions,
  GetProfileOptions,
  GetContextOptions,
} from "./types.js";
