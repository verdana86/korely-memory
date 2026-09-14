// Il nodo, eseguito contro un server vero, con una finta n8n intorno.
//
// Un nodo che compila non e' un nodo che funziona: quello che si rompe sono i
// percorsi, i nomi dei campi e il verbo HTTP, e nessuna di quelle tre cose la
// vede il compilatore. Qui c'e' il minimo indispensabile di n8n per far girare
// `execute`, e le richieste vanno davvero in rete.
import assert from "node:assert/strict";
import test from "node:test";
import { Korely } from "../dist/nodes/Korely/Korely.node.js";

const BASE = process.env.KORELY_BASE_URL;
const KEY = process.env.KORELY_API_KEY;
if (!BASE || !KEY) {
  console.log("servono KORELY_BASE_URL e KORELY_API_KEY: saltato");
  process.exit(0);
}

function fakeN8n(params) {
  const inviate = [];
  return {
    inviate,
    ctx: {
      getInputData: () => [{ json: {} }],
      getNode: () => ({ name: "Korely", type: "korely" }),
      continueOnFail: () => false,
      getNodeParameter(name, _i, fallback) {
        return name in params ? params[name] : fallback;
      },
      helpers: {
        httpRequestWithAuthentication: {
          async call(_self, _cred, options) {
            inviate.push(options);
            const url = new URL(BASE + options.url);
            for (const [k, v] of Object.entries(options.qs ?? {})) {
              if (v !== undefined && v !== "") url.searchParams.set(k, String(v));
            }
            const r = await fetch(url, {
              method: options.method,
              headers: {
                Authorization: "Bearer " + KEY,
                "Content-Type": "application/json",
              },
              body: options.body === undefined ? undefined : JSON.stringify(options.body),
            });
            const text = await r.text();
            if (!r.ok) throw new Error(`${r.status} ${text.slice(0, 200)}`);
            return text ? JSON.parse(text) : {};
          },
        },
      },
    },
  };
}

async function run(params) {
  const { ctx, inviate } = fakeN8n(params);
  const [out] = await Korely.prototype.execute.call(ctx);
  return { out, inviate };
}

const utente = "n8n-" + Math.random().toString(36).slice(2, 8);

test("scrive una memoria", async () => {
  const { out, inviate } = await run({
    resource: "memory", operation: "add", userId: utente,
    content: "Il cliente e passato al piano Gold.",
    extra: { timestamp: "2026-03-15" },
  });
  assert.equal(inviate[0].method, "POST");
  assert.equal(inviate[0].url, "/v1/memories");
  assert.ok(out[0].json.id, "nessun id nella risposta");
});

test("scrive un fatto senza modello, e lo ritratta alla data giusta", async () => {
  // Con una data di nascita: un fatto nato oggi non puo' chiudersi a giugno, e
  // il prodotto adesso lo rifiuta. Era il difetto che questo banco ha trovato.
  const scritto = await run({
    resource: "fact", operation: "write", userId: utente,
    subject: utente, predicate: "is_on", object: "Priority Support plan",
    validFrom: "2026-01-10", extra: {},
  });
  const id = scritto.out[0].json.id;
  assert.ok(id, "il fatto non ha un id");

  const chiuso = await run({
    resource: "fact", operation: "forget", userId: utente,
    factId: id, at: "2026-06-20", extra: {},
  });
  assert.equal(chiuso.inviate[0].method, "POST");
  assert.equal(chiuso.out[0].json.status, "forgotten");
  assert.ok(String(chiuso.out[0].json.invalid_at).startsWith("2026-06-20"));

  const prima = await run({
    resource: "fact", operation: "list", userId: utente,
    asOf: "2026-04-01", extra: {},
  });
  const oggetti = prima.out[0].json.facts.map((f) => f.object);
  assert.ok(oggetti.includes("Priority Support plan"), `ad aprile: ${oggetti}`);

  const adesso = await run({
    resource: "fact", operation: "list", userId: utente, asOf: "", extra: {},
  });
  const ora = adesso.out[0].json.facts.map((f) => f.object);
  assert.ok(!ora.includes("Priority Support plan"), `adesso: ${ora}`);
});

test("corregge un fatto, e una correzione vuota viene rifiutata", async () => {
  const scritto = await run({
    resource: "fact", operation: "write", userId: utente,
    subject: utente, predicate: "lives_in", object: "Roma", extra: {},
  });
  const id = scritto.out[0].json.id;

  const corretto = await run({
    resource: "fact", operation: "correct", userId: utente,
    factId: id, subject: "", predicate: "", object: "Milano", extra: {},
  });
  assert.equal(corretto.inviate[0].method, "PATCH");
  assert.equal(corretto.out[0].json.object, "Milano");

  await assert.rejects(
    run({
      resource: "fact", operation: "correct", userId: utente,
      factId: id, subject: "", predicate: "", object: "", extra: {},
    }),
    /at least one of subject/,
  );
});

test("il contesto pronto per un prompt", async () => {
  const { out, inviate } = await run({
    resource: "context", operation: "get", userId: utente,
    query: "che piano ha", extra: {},
  });
  assert.equal(inviate[0].method, "GET");
  assert.equal(inviate[0].url, "/v1/context");
  assert.ok(typeof out[0].json.context === "string");
});

test("un fatto non puo' chiudersi prima di essere nato", async () => {
  const scritto = await run({
    resource: "fact", operation: "write", userId: utente,
    subject: utente, predicate: "drives", object: "Fiat", extra: {},
  });
  const id = scritto.out[0].json.id;
  await assert.rejects(
    run({
      resource: "fact", operation: "forget", userId: utente,
      factId: id, at: "2020-01-01", extra: {},
    }),
    /true on no date at all|cannot stop being true/,
  );
});
