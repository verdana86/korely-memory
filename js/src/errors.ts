/**
 * Typed errors. Every error subclasses KorelyError, so a caller can catch
 * everything with one `catch`.
 *
 * Every error the server answers with is an `APIError` carrying the stable
 * `code` and the `message` of the REST error envelope; the common statuses
 * also have their own subclass, so both styles work:
 *
 *   if (e instanceof NotFoundError) ...                         // by class
 *   if (e instanceof APIError && e.code === "not_found") ...    // by code
 *
 * A KorelyError that is not an APIError never reached the server, or got no
 * usable answer: no API key, a key and address that do not belong together,
 * a connection error or a timeout.
 */

export interface KorelyErrorOptions {
  status?: number;
  code?: string;
}

export class KorelyError extends Error {
  readonly status?: number;
  readonly code?: string;

  constructor(message = "", opts: KorelyErrorOptions = {}) {
    super(message || opts.code || "Korely error");
    this.name = "KorelyError";
    this.status = opts.status;
    this.code = opts.code;
    // Restore the prototype chain so `instanceof` works after transpilation.
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/**
 * The server answered with a non-2xx status. `status` is the HTTP status,
 * `code` the stable error code (`invalid_key`, `not_found`, `quota_exceeded`,
 * `search_unavailable` and so on). Anything without a subclass below (422
 * validation, 503 unavailable, other 5xx) is a plain APIError.
 *
 * The subclasses were siblings of this class in 0.1.6 and earlier, so the pattern the
 * public docs teach, `err instanceof APIError` and branch on `err.code`, let a
 * 401, a 404 or a 429 through.
 */
export interface APIErrorOptions extends KorelyErrorOptions {
  retryAfter?: number;
}

export class APIError extends KorelyError {
  /**
   * The server's Retry-After header in whole seconds, rounded up; undefined
   * when it sent none. Two answers carry it: a rate limit (429
   * `rate_limit_exceeded`) and the pause of the writes that need a model (503
   * `writes_paused`, Cloud only), which lasts until 00:00 UTC.
   *
   * On every status since 2026-10-06. Only QuotaExceededError kept it, so the
   * 503 `writes_paused` lost the one number that says when to come back.
   */
  readonly retryAfter?: number;

  constructor(message = "", opts: APIErrorOptions = {}) {
    super(message, opts);
    this.name = "APIError";
    this.retryAfter = opts.retryAfter;
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/** 401: missing, malformed, or revoked API key. */
export class AuthenticationError extends APIError {
  constructor(message = "", opts: APIErrorOptions = {}) {
    super(message, opts);
    this.name = "AuthenticationError";
    Object.setPrototypeOf(this, AuthenticationError.prototype);
  }
}

/**
 * 403: the key lacks a required scope, or the write would open an agent
 * namespace past the plan's cap (`code === "agent_cap_exceeded"`).
 */
export class NamespaceForbiddenError extends APIError {
  constructor(message = "", opts: APIErrorOptions = {}) {
    super(message, opts);
    this.name = "NamespaceForbiddenError";
    Object.setPrototypeOf(this, NamespaceForbiddenError.prototype);
  }
}

/** 404: memory or fact id does not exist, or was forgotten. */
export class NotFoundError extends APIError {
  constructor(message = "", opts: APIErrorOptions = {}) {
    super(message, opts);
    this.name = "NotFoundError";
    Object.setPrototypeOf(this, NotFoundError.prototype);
  }
}

export interface ConflictErrorOptions extends APIErrorOptions {
  currentFactId?: string;
}

/**
 * 409: the request conflicts with what the server holds. `code` names the
 * conflict: `stale_write` (update() with an expected_updated_at older than the
 * record, thrown as the subclass StaleWriteError), `account_has_login`
 * (`deleteAccount()` with the key of an account somebody signs in to, Cloud
 * only), `fact_not_current` (`correctFact()` of a fact that is history:
 * superseded, forgotten or ended; `currentFactId` is the fact to correct
 * instead) or `conflict` (the Self-hosted's code for a 409 that names no
 * other).
 *
 * Until 2026-10-06 every 409 was a StaleWriteError, so a refusal that has
 * nothing to do with an update read as a lost write, and code written to
 * re-read the record and retry a stale write retried a refusal that never
 * changes.
 */
export class ConflictError extends APIError {
  /**
   * On `fact_not_current` (2026-10-07): the id of the fact that holds now, the
   * one to correct instead. Undefined when nothing superseded the fact asked
   * (it was forgotten or ended: write the value of now with `addFactTriple()`),
   * on any other conflict, and from a server that does not send it. Read
   * this, never the message.
   */
  readonly currentFactId?: string;

  constructor(message = "", opts: ConflictErrorOptions = {}) {
    super(message, opts);
    this.name = "ConflictError";
    this.currentFactId = opts.currentFactId;
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/**
 * 409 `stale_write`: update() with an expected_updated_at older than the
 * record. A ConflictError, so `instanceof ConflictError` is true for it too.
 */
export class StaleWriteError extends ConflictError {
  constructor(message = "", opts: APIErrorOptions = {}) {
    super(message, opts);
    this.name = "StaleWriteError";
    Object.setPrototypeOf(this, StaleWriteError.prototype);
  }
}

/**
 * 429. `code === "rate_limit_exceeded"`: too many requests in a minute, hour
 * or day, and `retryAfter` holds the seconds the server asked to wait.
 * `code === "quota_exceeded"`: the monthly quota is used up, there is nothing
 * to wait for this month, and `retryAfter` is undefined.
 * `code === "too_many_batches"`: thrown as the subclass TooManyBatchesError.
 * `retryAfter` comes from APIError, which every error the server answers with
 * carries.
 */
export interface QuotaErrorOptions extends APIErrorOptions {
  limit?: number;
  used?: number;
  resetsAt?: string;
}

export class QuotaExceededError extends APIError {
  /**
   * On the Cloud's `quota_exceeded` (2026-10-07): the month's allowance (the
   * plan plus the month's top-ups), what was used, and when it starts again
   * (ISO date). Undefined from a server that does not send them. Read these,
   * never the message, which says the same in words that change.
   */
  readonly limit?: number;
  readonly used?: number;
  readonly resetsAt?: string;

  constructor(message = "", opts: QuotaErrorOptions = {}) {
    super(message, opts);
    this.name = "QuotaExceededError";
    this.limit = opts.limit;
    this.used = opts.used;
    this.resetsAt = opts.resetsAt;
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/**
 * 429 `too_many_batches` (Cloud only): three `batch()` imports of the account
 * are still running, and the Cloud takes no fourth until one of them
 * finishes. Nothing was stored. Wait for a job to finish (`batchStatus()`) and
 * send the batch again. There is no Retry-After, because the wait is a job and
 * not a clock, so `retryAfter` is undefined.
 *
 * Its own class since 2026-10-06. It was a plain QuotaExceededError with no
 * `retryAfter`, exactly what a monthly `quota_exceeded` looks like, and the
 * docs tell you to stop on that one, while this one clears in minutes.
 */
export class TooManyBatchesError extends QuotaExceededError {
  constructor(message = "", opts: QuotaErrorOptions = {}) {
    super(message, opts);
    this.name = "TooManyBatchesError";
    Object.setPrototypeOf(this, TooManyBatchesError.prototype);
  }
}
