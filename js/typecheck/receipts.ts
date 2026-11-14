// Compiled by test/types.test.mjs under --strict, never run: what a program
// reads from a receipt must be in its type, or tsc refuses the program.
import type { AgentDeleteReceipt, DeleteReceipt } from "../dist/index.js";

export function reopened(memory: DeleteReceipt, agent: AgentDeleteReceipt): number {
  return (memory.facts_reopened ?? 0) + (agent.facts_reopened ?? 0);
}
