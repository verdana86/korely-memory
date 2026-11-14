// Compiled by test/types.test.mjs under --strict, never run: the key of each
// call in the trail (Korely 1.0.23) must be in AuditEvent's type.
import type { AuditEvent } from "../dist/index.js";

export function byKey(events: AuditEvent[], keyId: string): AuditEvent[] {
  return events.filter((e) => e.api_key_id === keyId && e.action === "forget");
}
