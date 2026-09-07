/**
 * Typed errors mapping 1:1 onto the REST error codes. Every error subclasses
 * KorelyError, so a caller can catch everything with one `catch`.
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

/** 401 — missing, malformed, or revoked API key. */
export class AuthenticationError extends KorelyError {
  constructor(message = "", opts: KorelyErrorOptions = {}) {
    super(message, opts);
    this.name = "AuthenticationError";
    Object.setPrototypeOf(this, AuthenticationError.prototype);
  }
}

/** 403 — the key lacks the scope required for this call. */
export class NamespaceForbiddenError extends KorelyError {
  constructor(message = "", opts: KorelyErrorOptions = {}) {
    super(message, opts);
    this.name = "NamespaceForbiddenError";
    Object.setPrototypeOf(this, NamespaceForbiddenError.prototype);
  }
}

/** 404 — memory or fact id does not exist, or was forgotten. */
export class NotFoundError extends KorelyError {
  constructor(message = "", opts: KorelyErrorOptions = {}) {
    super(message, opts);
    this.name = "NotFoundError";
    Object.setPrototypeOf(this, NotFoundError.prototype);
  }
}

/** 409 — update() with an expected_updated_at older than the record. */
export class StaleWriteError extends KorelyError {
  constructor(message = "", opts: KorelyErrorOptions = {}) {
    super(message, opts);
    this.name = "StaleWriteError";
    Object.setPrototypeOf(this, StaleWriteError.prototype);
  }
}

/** 429 — past the soft cap. `retryAfter` is in seconds when the server sent it. */
export class QuotaExceededError extends KorelyError {
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

/** Any other non-2xx response (validation 422, server 5xx, …). */
export class APIError extends KorelyError {
  constructor(message = "", opts: KorelyErrorOptions = {}) {
    super(message, opts);
    this.name = "APIError";
    Object.setPrototypeOf(this, APIError.prototype);
  }
}
