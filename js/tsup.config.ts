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
export default defineConfig({
  entry: { index: "src/index.ts", "ai-sdk": "src/ai-sdk.ts" },
  format: ["esm", "cjs"],
  dts: true,
  clean: true,
  sourcemap: false,
  minify: false,
  target: "node18",
  external: ["ai", "korely-memory"],
  outExtension({ format }) {
    return { js: format === "cjs" ? ".cjs" : ".js" };
  },
});
