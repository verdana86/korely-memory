#!/usr/bin/env python3
"""Quello che hai riparato e' arrivato a chi installa, o e' solo sul tuo disco?

    python scripts/check_published.py            # dice dove sei
    python scripts/check_published.py --strict   # rosso se il locale e' avanti

Esiste per un difetto trovato da un collaudatore che verificava riparazioni: tre
su sei risultavano non fatte, perche' stavano nei client e i client li installa
da PyPI e da npm. Le avevo scritte e lasciate nel working tree.

Il server si installa clonando il repository, quindi una riparazione lato server
arriva appena la committi. Un client no: arriva quando lo pubblichi. Le due cose
hanno tempi diversi, e nessuno se ne accorge finche' qualcuno non prova il
prodotto dall'esterno.

`--strict` e' pensato per girare prima di dire "riparato" a qualcuno.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _locale() -> tuple[str, str]:
    py = re.search(r'^version = "([^"]+)"',
                   (ROOT / "python" / "pyproject.toml").read_text(), re.M).group(1)
    js = json.loads((ROOT / "js" / "package.json").read_text())["version"]
    return py, js


def _pubblicata(url: str, dove) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return dove(json.loads(r.read().decode()))
    except (urllib.error.URLError, KeyError, ValueError) as e:
        print(f"  ! non ho potuto chiedere a {url}: {e}", file=sys.stderr)
        return None


def _tupla(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="esce rosso se il locale e' avanti al pubblicato")
    args = ap.parse_args()

    py_loc, js_loc = _locale()
    py_pub = _pubblicata("https://pypi.org/pypi/korely-memory/json",
                         lambda d: d["info"]["version"])
    js_pub = _pubblicata("https://registry.npmjs.org/korely-memory",
                         lambda d: d["dist-tags"]["latest"])

    avanti = []
    for nome, loc, pub in (("PyPI", py_loc, py_pub), ("npm", js_loc, js_pub)):
        if pub is None:
            print(f"{nome:<6} locale {loc}  pubblicata ?")
            continue
        stato = "allineato"
        if _tupla(loc) > _tupla(pub):
            stato = "LOCALE AVANTI: chi installa non ha le tue riparazioni"
            avanti.append(nome)
        elif _tupla(loc) < _tupla(pub):
            stato = "locale indietro: il repository e' vecchio"
        print(f"{nome:<6} locale {loc:<9} pubblicata {pub:<9} {stato}")

    if avanti and args.strict:
        print()
        print("Le riparazioni in " + " e ".join(avanti) + " esistono solo qui.")
        print("Vedi RELEASING.md. Finche' non sono pubblicate, per chi installa")
        print("il prodotto non sono state fatte.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
