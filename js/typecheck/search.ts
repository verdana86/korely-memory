// Compiled by test/types.test.mjs under --strict, never run: a search of the
// facts with the whole text (Korely 1.0.13) must be in SearchOptions' and
// SearchHit's types, or tsc refuses the program.
import type { Korely, SearchHit, SearchKind } from "../dist/index.js";

export async function wholeFacts(k: Korely, kinds: SearchKind[]): Promise<string[]> {
  const hits: SearchHit[] = await k.search("where does Maria live", {
    user_id: "maria", types: kinds, full_text: true,
  });
  return hits.map((h) => (h.type === "fact" ? h.fact?.object ?? "" : h.content ?? h.snippet ?? ""));
}
