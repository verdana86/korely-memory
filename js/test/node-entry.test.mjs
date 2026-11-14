// What Node loads: the `node` condition of the package's exports. An ES module
// of Node 18, and of Node 20 before 20.16, has no process.getBuiltinModule and
// no require, so the universal entry could not read what `korely init` saved:
// `new Korely()` threw "No API key" there. Each case runs in a process of its
// own with process.getBuiltinModule taken away, as on those versions, and
// imports the package by its name, as a program does. Build first:
//   npm run build && npm test
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const pkgDir = fileURLToPath(new URL("..", import.meta.url));
const pkg = JSON.parse(readFileSync(join(pkgDir, "package.json"), "utf8"));
// Node 18 and Node 20 before 20.16, for the process that runs the case.
const OLD_NODE = "data:text/javascript,delete process.getBuiltinModule";

function savedKeyIn(kind) {
  const home = mkdtempSync(join(tmpdir(), "korely-config-"));
  try {
    writeFileSync(join(home, "config.json"), JSON.stringify({
      api_key: "kor_self_fromconfig", base_url: "https://memory.example.com" }));
    const load = kind === "module"
      ? 'const { Korely } = await import("korely-memory");'
      : 'const { Korely } = require("korely-memory");';
    const env = { ...process.env, KORELY_CONFIG_HOME: home };
    delete env.KORELY_API_KEY;
    delete env.KORELY_BASE_URL;
    return execFileSync(process.execPath, [
      "--import", OLD_NODE, `--input-type=${kind}`, "-e",
      `${load} const k = new Korely({ fetch: async () => ({}) });` +
        "console.log(typeof process.getBuiltinModule, k.apiKey, k.baseUrl);",
    ], { cwd: pkgDir, env, encoding: "utf8" }).trim();
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
}

test("an ES module of an older Node reads what `korely init` saved", () => {
  assert.equal(savedKeyIn("module"), "undefined kor_self_fromconfig https://memory.example.com");
});

test("so does require() of an older Node", () => {
  assert.equal(savedKeyIn("commonjs"), "undefined kor_self_fromconfig https://memory.example.com");
});

test("the universal entry imports no Node built-in: a browser or an edge runtime loads it", () => {
  // The files dist/index.js reaches by its imports, the chunks included.
  const seen = new Set();
  const walk = (file) => {
    if (seen.has(file)) return;
    seen.add(file);
    const code = readFileSync(join(pkgDir, "dist", file), "utf8");
    // The module's own import statements: esbuild writes them from column 0.
    const statements = /^(?:import\s*["']([^"']+)["']|(?:import|export)\s[^;]*?\sfrom\s*["']([^"']+)["'])/gm;
    for (const m of code.matchAll(statements)) {
      const spec = m[1] ?? m[2];
      if (spec.startsWith("./")) walk(spec.slice(2));
      else assert.fail(`${file} imports ${spec}`);
    }
  };
  walk(pkg.exports["."].import.replace("./dist/", ""));
  assert.ok(seen.size > 1, [...seen].join(" "));
});

test("a bundler keeps the Node entry's one statement: the entry is declared with a side effect", () => {
  // With "sideEffects": false a bundler may take the re-exports and drop the
  // call that hands over the built-ins.
  const node = pkg.exports["."].node;
  assert.deepEqual([...pkg.sideEffects].sort(), [node.import, node.require].sort());
});
