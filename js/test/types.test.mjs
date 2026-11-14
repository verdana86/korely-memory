// The types a program compiles against, checked the way the program would be:
// the files in typecheck/ (outside test/, so node --test does not run them)
// are compiled with the TypeScript the package builds
// with, --strict, against the built declarations (dist). Build first:
//   npm run build && npm test
import test from "node:test";
import assert from "node:assert/strict";
import { readdirSync } from "node:fs";
import ts from "typescript";

const dir = new URL("../typecheck/", import.meta.url);

test("the programs in typecheck/ compile against the published types", () => {
  const files = readdirSync(dir).filter((f) => f.endsWith(".ts")).map((f) => new URL(f, dir).pathname);
  assert.ok(files.length > 0);
  const program = ts.createProgram(files, {
    strict: true,
    noEmit: true,
    target: ts.ScriptTarget.ES2021,
    module: ts.ModuleKind.ESNext,
    moduleResolution: ts.ModuleResolutionKind.Bundler,
    types: [],
    skipLibCheck: true,
  });
  const errors = ts.getPreEmitDiagnostics(program).map((d) =>
    ts.flattenDiagnosticMessageText(d.messageText, "\n"));
  assert.deepEqual(errors, []);
});
