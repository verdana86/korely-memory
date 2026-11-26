#!/usr/bin/env python3
"""Has what you fixed reached the people who install the clients, or is it
only on your disk?

    python scripts/check_published.py            # says where you are
    python scripts/check_published.py --strict   # fails if the local copy is ahead

It exists because of a defect a tester found while checking fixes: three of
six looked undone, because they were in the clients, and the clients are
installed from PyPI and npm. They had been written and left in the working
tree.

The server is installed by cloning its repository, so a fix on the server
side arrives as soon as it is committed. A client does not: it arrives when it
is published. The two run on different clocks, and nobody notices until
someone tries the product from the outside.

`--strict` is meant to run before telling anyone "fixed".
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


def _local() -> tuple[str, str]:
    py = re.search(r'^version = "([^"]+)"',
                   (ROOT / "python" / "pyproject.toml").read_text(), re.M).group(1)
    js = json.loads((ROOT / "js" / "package.json").read_text())["version"]
    return py, js


def _published(url: str, where) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return where(json.loads(r.read().decode()))
    except (urllib.error.URLError, KeyError, ValueError) as e:
        print(f"  ! could not ask {url}: {e}", file=sys.stderr)
        return None


def _parts(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="exit with an error when the local version is ahead of the published one")
    args = ap.parse_args()

    py_local, js_local = _local()
    py_pub = _published("https://pypi.org/pypi/korely-memory/json",
                        lambda d: d["info"]["version"])
    js_pub = _published("https://registry.npmjs.org/korely-memory",
                        lambda d: d["dist-tags"]["latest"])

    ahead = []
    for name, local, pub in (("PyPI", py_local, py_pub), ("npm", js_local, js_pub)):
        if pub is None:
            print(f"{name:<6} local {local}  published ?")
            continue
        state = "in step"
        if _parts(local) > _parts(pub):
            state = "LOCAL AHEAD: whoever installs it does not have your fixes"
            ahead.append(name)
        elif _parts(local) < _parts(pub):
            state = "local behind: this repository is out of date"
        print(f"{name:<6} local {local:<9} published {pub:<9} {state}")

    if ahead and args.strict:
        print()
        print("The fixes in " + " and ".join(ahead) + " exist only here.")
        print("Until they are published, for whoever installs the product")
        print("they have not been made.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
