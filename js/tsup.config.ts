import { defineConfig } from "tsup";

// Dual ESM + CJS build with type declarations. Zero runtime dependencies: the
// SDK is a thin client over the native `fetch`.
//
// `ai-sdk` is the Vercel AI SDK integration (korely-memory/ai-sdk). It imports
// `ai`, an optional peer dependency, and the client from "korely-memory"
// itself: both stay external, so the core entry never loads `ai`, and the
// integration uses the same Korely and KorelyError classes as the core entry
// instead of a bundled copy. tsconfig `paths` points "korely-memory" at the
// sources for the type check.
//
// `index.node` is the entry the package's `node` export condition names: the
// universal entry plus the `node:` imports that read the saved config (see
// src/node.ts). It has the universal entry's types.
export default defineConfig({
  entry: { index: "src/index.ts", "index.node": "src/node.ts", "ai-sdk": "src/ai-sdk.ts" },
  format: ["esm", "cjs"],
  dts: { entry: { index: "src/index.ts", "ai-sdk": "src/ai-sdk.ts" } },
  clean: true,
  sourcemap: false,
  minify: false,
  target: "node18",
  external: ["ai", "korely-memory"],
  outExtension({ format }) {
    return { js: format === "cjs" ? ".cjs" : ".js" };
  },
});
