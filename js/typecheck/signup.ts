// Compiled by test/types.test.mjs under --strict, never run: what the 201 of
// POST /v1/agents/init says of the key's end and of the trial (Korely Agent
// 1.0.10 and later) must be in the type, or tsc refuses the program.
import type { AgentInitResult } from "../dist/index.js";

export function whenItEnds(made: AgentInitResult): string {
  return `${made.expires_at ?? "no end given"}: ${made.note ?? ""}`;
}
