/**
 * The entry Node loads (the `node` condition of the package's exports): the
 * universal entry, plus the built-in modules that read what `korely init`
 * saved, imported here because an ES module of Node 18 and of Node 20 before
 * 20.16 has no other synchronous way to load them. A browser or an edge
 * runtime never loads this file, so the universal entry stays free of
 * `node:` imports.
 */
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { useNodeFiles } from "./client.js";

useNodeFiles({ readFileSync, homedir, join });

export * from "./index.js";
