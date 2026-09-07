# Releasing

Due pacchetti, stesso nome, registri diversi: `korely-memory` su PyPI e su npm.
Si versionano in modo indipendente.

## Prima di ogni rilascio

```bash
# Python
cd python && PYTHONPATH=. python3 -m unittest tests.test_client tests.test_cli tests.test_mcp_server

# Node — `npm ci`, non `npm install`
cd js && npm ci && npm run build && npm test
```

**Usare `npm ci` e non `npm install`.** `ci` installa esattamente quello che dice
il lockfile e fallisce se lockfile e `package.json` divergono, che è proprio il
controllo che serve. `install` invece riscrive il lockfile in silenzio, quindi
una divergenza passa inosservata.

Storico: la 0.1.1 su npm è stata pubblicata con un lockfile che portava ancora
il nome e la versione precedenti alla rinomina (`korely` 0.1.0). Non ha rotto
nulla perché il pacchetto non ha dipendenze runtime, ma con dipendenze vere
sarebbe stata una build non riproducibile.

## Alzare la versione

Modificare **entrambi** i file quando si tocca il pacchetto Node:

- `js/package.json` campo `version`
- `js/package-lock.json` si aggiorna da solo con `npm install`, e va committato

Per Python basta `python/pyproject.toml` campo `version`.

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
