"""CLI tests: drive the `korely` command surface without network. We reuse the
same transport seam (Korely._send) the SDK tests use, and assert the argparse
wiring + per-command guards. Run with:

    cd python && python3 -m unittest discover -s tests -v
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

    def test_the_line_reads_the_new_names(self):
        """A server that sends only `memories_deleted` / `facts_deleted` (the
        deprecated names may go one day) still prints the counts, not 0."""
        rec = _Recorder().queue(200, {
            "user_id": "alex", "memories_deleted": 7, "facts_deleted": 3,
            "erasure": "permanent", "audit_id": "aud_9",
        })
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli.cmd_delete_all(_client(rec), _args(["delete-all", "--user-id", "alex", "--yes"]))
        self.assertEqual(rc, 0)
        self.assertIn("7 memory(ies), 3 fact(s) erased", out.getvalue())

    def test_json_output_carries_both_names(self):
        rec = _Recorder().queue(200, {
            "user_id": "alex", "memories_forgotten": 1,
            "facts_invalidated": 0, "audit_id": "aud_1",
        })
        out = io.StringIO()
        with redirect_stdout(out):
            cli.cmd_delete_all(_client(rec), _args(["delete-all", "--user-id", "alex",
                                                    "--yes", "--json"]))
        import json as _json
        data = _json.loads(out.getvalue())
        self.assertEqual((data["memories_deleted"], data["memories_forgotten"]), (1, 1))
        self.assertEqual((data["facts_deleted"], data["facts_invalidated"]), (0, 0))

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
        with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as fh:
            toml = fh.read()
        declared = re.search(r'^version\s*=\s*"([^"]+)"', toml, re.M).group(1)
        self.assertEqual(__version__, declared,
                         f"__version__={__version__} but pyproject says {declared}")



class TheTenseOfAFactThatEndsLater(unittest.TestCase):
    """`superseded 3026-01-01` reads as a thing that has happened, for a date a
    thousand years away. A tester read that line and it is what convinced them
    the underlying behaviour was a defect rather than a choice."""

    def _fact(self, invalid_at):
        from types import SimpleNamespace

        return SimpleNamespace(subject="u1", predicate="is_on", object="Gold",
                               valid_from="2026-03-15T00:00:00+00:00",
                               invalid_at=invalid_at)

    def test_a_past_end_is_superseded(self):
        from korely_memory.cli import _fact_line

        line = _fact_line(self._fact("2026-06-20T00:00:00+00:00"))
        self.assertIn("superseded 2026-06-20", line)

    def test_a_future_end_is_until(self):
        from korely_memory.cli import _fact_line

        line = _fact_line(self._fact("3026-01-01T00:00:00+00:00"))
        self.assertIn("until 3026-01-01", line)
        self.assertNotIn("superseded", line)

    def test_an_unreadable_date_does_not_crash_the_listing(self):
        """A listing that dies on one odd row shows nothing at all. It falls
        back to the past tense, which is the safe half of the guess: it is
        already closed for every date we can read."""
        from korely_memory.cli import _fact_line

        line = _fact_line(self._fact("non-una-data"))
        self.assertIn("u1 · is_on · Gold", line)
        self.assertIn("superseded", line)

    def test_an_open_fact_says_nothing_about_an_end(self):
        from korely_memory.cli import _fact_line

        line = _fact_line(self._fact(None))
        self.assertNotIn("superseded", line)
        self.assertNotIn("until", line)

    def test_dates_are_utc_days(self):
        """`2026-06-01T10:00:00+14:00` is 2026-05-31 20:00 UTC. Slicing the
        first ten characters printed the day of the offset, not the UTC day
        the hosted MCP prints since 2026-09-28."""
        from types import SimpleNamespace

        from korely_memory.cli import _fact_line, _utc_day

        self.assertEqual(_utc_day("2026-06-01T10:00:00+14:00"), "2026-05-31")
        self.assertEqual(_utc_day("2026-06-01T10:00:00Z"), "2026-06-01")
        self.assertEqual(_utc_day("2026-06-01"), "2026-06-01")
        self.assertEqual(_utc_day("not-a-date-at-all"), "not-a-date")
        self.assertEqual(_utc_day(None), "")
        f = SimpleNamespace(subject="u1", predicate="is_on", object="Gold",
                            valid_from="2026-03-15T02:00:00+05:00",
                            invalid_at="2026-06-01T10:00:00+14:00")
        line = _fact_line(f)
        self.assertIn("[from 2026-03-14]", line)
        self.assertIn("superseded 2026-05-31", line)


class InitReadsEitherErrorShape(unittest.TestCase):
    """A failed signup printed the raw JSON when the server answered with
    FastAPI's `{"detail": ...}` instead of `{"code", "message"}`."""

    def _init_against(self, status, body):
        import tempfile
        import urllib.request
        from email.message import Message
        from urllib.error import HTTPError

        def refuse(req, timeout=None):
            raise HTTPError(req.full_url, status, "err", Message(),
                            io.BytesIO(body.encode("utf-8")))

        saved = os.environ.get("KORELY_CONFIG_HOME")
        os.environ["KORELY_CONFIG_HOME"] = tempfile.mkdtemp()
        real, urllib.request.urlopen = urllib.request.urlopen, refuse
        err = io.StringIO()
        try:
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                rc = cli.main(["init", "--agent", "--base-url", "https://install.example"])
        finally:
            urllib.request.urlopen = real
            if saved is None:
                os.environ.pop("KORELY_CONFIG_HOME", None)
            else:
                os.environ["KORELY_CONFIG_HOME"] = saved
        return rc, err.getvalue()

    def test_the_hosted_envelope(self):
        rc, err = self._init_against(429, '{"code": "rate_limit_exceeded", "message": "slow down"}')
        self.assertEqual(rc, 1)
        self.assertIn("signup failed (429): slow down", err)

    def test_a_detail_only_answer(self):
        rc, err = self._init_against(404, '{"detail": "Not Found"}')
        self.assertEqual(rc, 1)
        self.assertIn("signup failed (404): Not Found", err)
        self.assertNotIn("{", err)

    def test_a_body_that_is_not_json(self):
        rc, err = self._init_against(502, "<html>Bad Gateway</html>")
        self.assertEqual(rc, 1)
        self.assertIn("Bad Gateway", err)


class _CleanEnv(unittest.TestCase):
    """No key, no address and an empty config directory, whatever the machine
    running the tests has set."""

    def setUp(self):
        import tempfile

        self._saved = {k: os.environ.get(k) for k in
                       ("KORELY_API_KEY", "KORELY_BASE_URL", "KORELY_CONFIG_HOME")}
        for k in ("KORELY_API_KEY", "KORELY_BASE_URL"):
            os.environ.pop(k, None)
        self.cfg_dir = tempfile.mkdtemp()
        os.environ["KORELY_CONFIG_HOME"] = self.cfg_dir

    def tearDown(self):
        import shutil

        shutil.rmtree(self.cfg_dir, ignore_errors=True)
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TheCliSaysWhatIsWrongWithTheKey(_CleanEnv):
    """Every constructor refusal printed "no API key", including the one the
    constructor exists for: a self-hosted key with no address. The key was
    there, and the explanation of what was wrong with it was thrown away."""

    def _run(self, argv):
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            rc = cli.main(argv)
        return rc, err.getvalue()

    def test_a_self_hosted_key_without_an_address_is_named(self):
        rc, err = self._run(["users", "--api-key", "kor_self_x"])
        self.assertEqual(rc, 2)
        self.assertIn("kor_self_", err)
        self.assertNotIn("no API key", err)

    def test_a_missing_key_still_says_so(self):
        rc, err = self._run(["users"])
        self.assertEqual(rc, 2)
        self.assertIn("no API key", err)


class InitRefusesAPairThatCannotWork(_CleanEnv):
    """`korely init --api-key kor_self_...` without --base-url wrote the hosted
    address next to a self-hosted key, printed "Saved", and every command after
    it failed."""

    def _init(self, argv):
        import urllib.request

        def explode(*a, **k):
            raise AssertionError("--api-key must not touch the network")

        real, urllib.request.urlopen = urllib.request.urlopen, explode
        err = io.StringIO()
        try:
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                rc = cli.main(argv)
        finally:
            urllib.request.urlopen = real
        return rc, err.getvalue()

    def test_a_self_hosted_key_with_no_address_is_not_saved(self):
        rc, err = self._init(["init", "--api-key", "kor_self_mine"])
        self.assertEqual(rc, 2)
        self.assertIn("kor_self_", err)
        self.assertFalse(os.path.exists(os.path.join(self.cfg_dir, "config.json")))

    def test_a_hosted_key_for_somebody_elses_server_is_not_saved(self):
        rc, _ = self._init(["init", "--api-key", "kor_live_mine",
                            "--base-url", "https://mine.example"])
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(os.path.join(self.cfg_dir, "config.json")))

    def test_a_matching_pair_is_saved(self):
        rc, _ = self._init(["init", "--api-key", "kor_self_mine",
                            "--base-url", "https://mine.example"])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(self.cfg_dir, "config.json")))


if __name__ == "__main__":
    unittest.main()
