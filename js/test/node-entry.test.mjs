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
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const pkgDir = fileURLToPath(new URL("..", import.meta.url));
const pkg = JSON.parse(readFileSync(join(pkgDir, "package.json"), "utf8"));
// Node 18 and Node 20 before 20.16, for the process that runs the case.
const OLD_NODE = "data:text/javascript,delete process.getBuiltinModule";

function savedKeyIn(kind, { load, older = true } = {}) {
  const home = mkdtempSync(join(tmpdir(), "korely-config-"));
  try {
    writeFileSync(join(home, "config.json"), JSON.stringify({
      api_key: "kor_self_fromconfig", base_url: "https://memory.example.com" }));
    load ??= kind === "module"
      ? 'const { Korely } = await import("korely-memory");'
      : 'const { Korely } = require("korely-memory");';
    const env = { ...process.env, KORELY_CONFIG_HOME: home };
    delete env.KORELY_API_KEY;
    delete env.KORELY_BASE_URL;
    return execFileSync(process.execPath, [
      ...(older ? ["--import", OLD_NODE] : []), `--input-type=${kind}`, "-e",
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

test(
  "the universal entry, which a bundler may hand Node, reads it with process.getBuiltinModule",
  // Node 20.16 and 22.3 and later; an older Node is served by the Node entry.
  { skip: typeof process.getBuiltinModule !== "function" },
  () => {
    const universal = JSON.stringify(new URL("../dist/index.js", import.meta.url).href);
    const expected = "function kor_self_fromconfig https://memory.example.com";
    assert.equal(savedKeyIn("module", {
      older: false, load: `const { Korely } = await import(${universal});` }), expected);
    const cjs = JSON.stringify(join(pkgDir, "dist", "index.cjs"));
    assert.equal(savedKeyIn("commonjs", {
      older: false, load: `const { Korely } = require(${cjs});` }), expected);
  },
);

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

test("package.json declares the Node entry's one statement as a side effect", () => {
  // It was "sideEffects": false for the whole package; the Node entry's call
  // that hands over the built-ins is a side effect.
  const node = pkg.exports["."].node;
  assert.deepEqual([...pkg.sideEffects].sort(), [node.import, node.require].sort());
});

test("esbuild keeps the call for Node and leaves the built-ins out for a browser", async () => {
  const { build } = await import("esbuild");
  const proj = mkdtempSync(join(tmpdir(), "korely-bundle-"));
  try {
    mkdirSync(join(proj, "node_modules"));
    symlinkSync(pkgDir, join(proj, "node_modules", "korely-memory"), "dir");
    writeFileSync(join(proj, "entry.mjs"),
      'import { Korely } from "korely-memory";\nexport const make = () => new Korely();\n');
    for (const platform of ["node", "browser"]) {
      const r = await build({
        entryPoints: [join(proj, "entry.mjs")], absWorkingDir: proj, bundle: true,
        format: "esm", platform, write: false, metafile: true, logLevel: "silent",
      });
      const code = r.outputFiles[0].text;
      const inputs = Object.keys(r.metafile.inputs).map((p) => p.split("/").pop());
      const node = platform === "node";
      assert.equal(inputs.includes("index.node.js"), node, `${platform}: ${inputs}`);
      assert.equal(/useNodeFiles\(\{/.test(code), node, `${platform}: the call`);
      assert.equal(/from\s*["'](node:)?fs["']/.test(code), node, `${platform}: fs`);
    }
  } finally {
    rmSync(proj, { recursive: true, force: true });
  }
});
