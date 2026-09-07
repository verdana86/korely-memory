import { defineConfig } from "tsup";

// Dual ESM + CJS build with type declarations. Zero runtime dependencies — the
// SDK is a thin client over the native `fetch`.
export default defineConfig({
  entry: ["src/index.ts"],
  format: ["esm", "cjs"],
  dts: true,
  clean: true,
  sourcemap: false,
  minify: false,
  target: "node18",
  outExtension({ format }) {
    return { js: format === "cjs" ? ".cjs" : ".js" };
  },
});
