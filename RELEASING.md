# Releasing

Due pacchetti, stesso nome, registri diversi: `korely-memory` su PyPI e su npm.
Si versionano in modo indipendente.

## Prima di ogni rilascio

```bash
# Python: `discover`, cosi' nessun file di test resta fuori (prima l'elenco a
# mano saltava test_cli_init_saves_a_key.py). I test dell'MCP girano solo con
# l'extra installato: `pip install 'mcp>=1.2.0,<2'` in un ambiente usa e getta.
cd python && PYTHONPATH=. python3 -m unittest discover -s tests

# Node: `npm ci`, non `npm install`
cd js && npm ci && npm run build && npm test

# korely-memory/ai-sdk dichiara `ai` ^5 || ^6 || ^7 e `npm test` gira sulla 7:
# gli stessi test sulle altre due, poi `npm ci` rimette il lockfile
cd js && for m in 5 6; do npm install --no-save "ai@^$m" && node --test test/ai-sdk.test.mjs; done; npm ci

# n8n (senza rete; test/run.mjs invece vuole un server vero)
cd n8n && npm ci && npm run build && npm test
```

Su Alpine (`node:20-alpine`) `npm ci` in `n8n/` fallisce: `isolated-vm`, che
arriva con `n8n-workflow` tramite `@n8n/expression-runtime`, compila con
node-gyp e cerca Python. I test offline non lo caricano, quindi basta
`npm ci --ignore-scripts`.

**Usare `npm ci` e non `npm install`.** `ci` installa esattamente quello che dice
il lockfile e fallisce se lockfile e `package.json` divergono, che è proprio il
controllo che serve. `install` invece riscrive il lockfile in silenzio, quindi
una divergenza passa inosservata.

Storico: la 0.1.1 su npm è stata pubblicata con un lockfile che portava ancora
il nome e la versione precedenti alla rinomina (`korely` 0.1.0). Non ha rotto
nulla perché il pacchetto non ha dipendenze runtime, ma con dipendenze vere
sarebbe stata una build non riproducibile.

## Alzare la versione

Node, **tre** posti insieme (un test li lega):

- `js/package.json` campo `version`
- `js/package-lock.json`, con `npm install --package-lock-only`, e va committato
- `VERSION` in `js/src/client.ts`, che va sul filo nell'header `X-Korely-Client`

Python, **due** posti insieme (un test li lega):

- `python/pyproject.toml` campo `version`
- `__version__` in `python/korely_memory/client.py`, che va nello User-Agent e
  in `korely --version`

E una voce in `CHANGELOG.md`.

## Pubblicare

```bash
# Python
cd python
python3 -m pip install --upgrade build twine
python3 -m build
python3 -m twine upload dist/*

# Node
cd js
npm publish
```

`prepublishOnly` esegue la build, quindi `npm publish` non pubblica mai sorgenti
non compilati.

## Dopo il rilascio

Controllare che le schede mostrino il link al sorgente:

```bash
curl -s https://pypi.org/pypi/korely-memory/json | python3 -c "import sys,json; print(json.load(sys.stdin)['info']['project_urls'])"
curl -s https://registry.npmjs.org/korely-memory | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['dist-tags'], d['versions'][d['dist-tags']['latest']].get('repository'))"
```

Se `Repository` manca, la pubblicazione ha perso i metadati e va rifatta con un
numero di versione nuovo: i registri non permettono di sovrascrivere una
versione già pubblicata.
