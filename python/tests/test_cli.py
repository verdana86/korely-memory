"""CLI tests — drive the `korely` command surface without network. We reuse the
same transport seam (Korely._send) the SDK tests use, and assert the argparse
wiring + per-command guards. Run with:

    cd sdk/python && python3 -m unittest discover -s tests -v
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from korely_memory import Korely  # noqa: E402
from korely_memory import cli  # noqa: E402


class _Recorder:
    def __init__(self):
        self.calls = []
        self._responses = []

    def queue(self, status, body):
        self._responses.append((status, body))
        return self

    def __call__(self, method, path, *, params=None, json_body=None):
        self.calls.append({"method": method, "path": path, "params": params, "json": json_body})
        return self._responses.pop(0) if self._responses else (200, {})

    @property
    def last(self):
        return self.calls[-1]


def _args(argv):
    return cli.build_parser().parse_args(argv)


def _client(rec):
    k = Korely(api_key="kor_live_test")
    k._send = rec
    return k


class TestDeleteAllGuards(unittest.TestCase):
    def test_requires_user_id(self):
        rec = _Recorder()
        a = _args(["delete-all", "--yes"])
        err = io.StringIO()
        with redirect_stderr(err):
            rc = cli.cmd_delete_all(_client(rec), a)
        self.assertEqual(rc, 2)
        self.assertIn("--user-id", err.getvalue())
        self.assertEqual(rec.calls, [])  # never hit the network

    def test_requires_yes_confirmation(self):
        rec = _Recorder()
        a = _args(["delete-all", "--user-id", "alex"])
        err = io.StringIO()
        with redirect_stderr(err):
            rc = cli.cmd_delete_all(_client(rec), a)
        self.assertEqual(rc, 2)
        self.assertIn("--yes", err.getvalue())
        self.assertEqual(rec.calls, [])  # guarded before any destructive call

    def test_happy_path_calls_delete_all(self):
        rec = _Recorder().queue(200, {
            "user_id": "alex", "memories_forgotten": 12,
            "facts_invalidated": 4, "audit_id": "aud_77",
        })
        a = _args(["delete-all", "--user-id", "alex", "--yes"])
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli.cmd_delete_all(_client(rec), a)
        self.assertEqual(rc, 0)
        self.assertEqual(rec.last["method"], "DELETE")
        self.assertEqual(rec.last["path"], "/v1/users/alex/memories")
        self.assertIn("forgot user alex", out.getvalue())
        self.assertIn("12 memory(ies)", out.getvalue())

    def test_json_output(self):
        rec = _Recorder().queue(200, {
            "user_id": "alex", "memories_forgotten": 1,
            "facts_invalidated": 0, "audit_id": "aud_1",
        })
        a = _args(["delete-all", "--user-id", "alex", "--yes", "--json"])
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli.cmd_delete_all(_client(rec), a)
        self.assertEqual(rc, 0)
        self.assertIn('"user_id": "alex"', out.getvalue())


class TestParserWiring(unittest.TestCase):
    def test_delete_all_subcommand_registered(self):
        a = _args(["delete-all", "--user-id", "x", "--yes"])
        self.assertIs(a.func, cli.cmd_delete_all)
        self.assertTrue(a.yes)
        self.assertEqual(a.user_id, "x")

    def test_facts_exposes_every_sdk_filter(self):
        """The CLI must not lag the SDK: get_facts() takes a `predicate`, so
        `korely facts` needs --predicate too. Regression on the asymmetry found
        while walking the product as an outside user (2026-09-08)."""
        a = _args(["facts", "--predicate", "subscribes_to", "--subject", "maria",
                   "--entity", "Pro plan", "--family", "ownership",
                   "--as-of", "2026-03-01", "--include-invalidated"])
        self.assertEqual(a.predicate, "subscribes_to")
        self.assertEqual(a.subject, "maria")
        self.assertEqual(a.entity, "Pro plan")
        self.assertEqual(a.family, "ownership")
        self.assertEqual(a.as_of, "2026-03-01")
        self.assertTrue(a.include_invalidated)

    def test_facts_forwards_predicate_to_the_api(self):
        rec = _Recorder().queue(200, {"facts": []})
        k = Korely(api_key="kor_live_x")
        k._send = rec
        with redirect_stdout(io.StringIO()):
            cli.cmd_facts(k, _args(["facts", "--predicate", "subscribes_to"]))
        self.assertEqual(rec.last["params"].get("predicate"), "subscribes_to")

    def test_all_subcommands_present(self):
        # every documented command must parse (regression on accidental drops)
        for argv in (
            ["auth"], ["add", "hi"], ["search", "q"], ["context", "q"],
            ["facts"], ["profile"], ["users"], ["get", "mem_1"],
            ["delete", "mem_1"], ["delete-all", "--user-id", "x", "--yes"],
        ):
            with self.subTest(cmd=argv[0]):
                a = _args(argv)
                self.assertTrue(callable(a.func))


class TestVersionConsistency(unittest.TestCase):
    def test_module_version_matches_pyproject(self):
        """__version__ and pyproject must agree. 0.1.4 shipped to PyPI while the
        module still reported 0.1.3, so the User-Agent and `korely --version`
        lied about which build was running. Caught 2026-09-08."""
        import re
        from korely_memory import __version__
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        toml = open(os.path.join(root, "pyproject.toml")).read()
        declared = re.search(r'^version\s*=\s*"([^"]+)"', toml, re.M).group(1)
        self.assertEqual(__version__, declared,
                         f"__version__={__version__} but pyproject says {declared}")


if __name__ == "__main__":
    unittest.main()
