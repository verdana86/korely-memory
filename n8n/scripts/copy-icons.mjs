// The icons are not TypeScript, so tsc leaves them behind. n8n loads them from
// the paths written in the node's `icon` field, which point inside dist.
import { cpSync, mkdirSync } from "node:fs";
mkdirSync("dist/nodes/Korely", { recursive: true });
cpSync("nodes/Korely/korely.svg", "dist/nodes/Korely/korely.svg");
console.log("icons copied");
