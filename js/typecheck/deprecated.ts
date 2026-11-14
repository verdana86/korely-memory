// Read by test/types.test.mjs: each line marked `deprecated` must be reported
// as deprecated by TypeScript (an editor strikes it through), and must still
// compile, so that a program written against an older type keeps building.
import type { AgentDeleteReceipt } from "../dist/index.js";

export function slot(agent: AgentDeleteReceipt): boolean | undefined {
  return agent.slot_freed; // deprecated
}
