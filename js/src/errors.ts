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
export class APIError extends KorelyError {
  constructor(message = "", opts: KorelyErrorOptions = {}) {
    super(message, opts);
    this.name = "APIError";
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/** 401: missing, malformed, or revoked API key. */
export class AuthenticationError extends APIError {
  constructor(message = "", opts: KorelyErrorOptions = {}) {
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
  constructor(message = "", opts: KorelyErrorOptions = {}) {
    super(message, opts);
    this.name = "NamespaceForbiddenError";
    Object.setPrototypeOf(this, NamespaceForbiddenError.prototype);
  }
}

/** 404: memory or fact id does not exist, or was forgotten. */
export class NotFoundError extends APIError {
  constructor(message = "", opts: KorelyErrorOptions = {}) {
    super(message, opts);
    this.name = "NotFoundError";
    Object.setPrototypeOf(this, NotFoundError.prototype);
  }
}

/** 409: update() with an expected_updated_at older than the record. */
export class StaleWriteError extends APIError {
  constructor(message = "", opts: KorelyErrorOptions = {}) {
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
 */
export class QuotaExceededError extends APIError {
  readonly retryAfter?: number;

  constructor(
    message = "",
    opts: KorelyErrorOptions & { retryAfter?: number } = {},
  ) {
    super(message, opts);
    this.name = "QuotaExceededError";
    this.retryAfter = opts.retryAfter;
    Object.setPrototypeOf(this, QuotaExceededError.prototype);
  }
}
