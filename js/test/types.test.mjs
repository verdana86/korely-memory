// The types a program compiles against, checked the way the program would be:
// the files in typecheck/ (outside test/, so node --test does not run them)
// are compiled with the TypeScript the package builds
// with, --strict, against the built declarations (dist). Build first:
//   npm run build && npm test
import test from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import ts from "typescript";

const dir = new URL("../typecheck/", import.meta.url);
const files = readdirSync(dir).filter((f) => f.endsWith(".ts")).map((f) => new URL(f, dir).pathname);
const options = {
  strict: true,
  noEmit: true,
  target: ts.ScriptTarget.ES2021,
  module: ts.ModuleKind.ESNext,
  moduleResolution: ts.ModuleResolutionKind.Bundler,
  types: [],
  skipLibCheck: true,
};

test("the programs in typecheck/ compile against the published types", () => {
  assert.ok(files.length > 0);
  const program = ts.createProgram(files, options);
  const errors = ts.getPreEmitDiagnostics(program).map((d) =>
    ts.flattenDiagnosticMessageText(d.messageText, "\n"));
  assert.deepEqual(errors, []);
});

test("what the types mark deprecated is reported as deprecated, line by line", () => {
  // What an editor shows: the language service's suggestions (code 6385).
  const service = ts.createLanguageService({
    getScriptFileNames: () => files,
    getScriptVersion: () => "1",
    getScriptSnapshot: (f) => ts.sys.fileExists(f)
      ? ts.ScriptSnapshot.fromString(ts.sys.readFile(f)) : undefined,
    getCurrentDirectory: () => dir.pathname,
    getCompilationSettings: () => options,
    getDefaultLibFileName: ts.getDefaultLibFilePath,
    fileExists: ts.sys.fileExists,
    readFile: ts.sys.readFile,
  });
  for (const file of files) {
    const lines = readFileSync(file, "utf8").split("\n");
    const marked = lines.flatMap((l, i) => (/\/\/ deprecated$/.test(l) ? [i] : []));
    const reported = service.getSuggestionDiagnostics(file)
      .filter((d) => d.reportsDeprecated)
      .map((d) => ts.getLineAndCharacterOfPosition(d.file, d.start).line);
    assert.deepEqual([...new Set(reported)].sort(), marked, file);
  }
});
