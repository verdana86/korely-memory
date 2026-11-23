"""A write says whose memory it is (Korely 1.0.19).

The server refuses a write that names neither `user_id` nor `agent_id` (422
`identity_required`): without one, every writer that sent none was read as one
person. Here: the identity `korely init` saves for a profile, which the `korely`
command line and `korely-mcp` send when a write names none; the refusal made
before anything is sent when there is none; and the server's own refusal,
which reaches the caller with its code and its message.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from korely_memory import Korely, cli  # noqa: E402
from korely_memory.exceptions import APIError  # noqa: E402

# What a 1.0.19 server answers to a write with no identity (its CHANGELOG).
_MESSAGE = ("user_id or agent_id is required: say whose memory this is, with user_id (the end "
            "user it belongs to) or agent_id (the agent writing it); an id of spaces or "
            "invisible characters names nobody")
_REFUSAL = {"detail": {"code": "identity_required", "message": _MESSAGE},
            "code": "identity_required", "message": _MESSAGE}


class _Recorder:
    def __init__(self, *answers):
        self.calls, self.answers = [], list(answers)

    def __call__(self, method, path, *, params=None, json_body=None, headers=None):
        self.calls.append({"method": method, "path": path, "json": json_body})
        return self.answers.pop(0) if self.answers else (201, {"id": "x", "facts": []})


class _OwnConfig(unittest.TestCase):
    """An empty config directory of its own, and a key that is never saved."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"KORELY_CONFIG_HOME": self.home})
        self.env.start()
        for k in ("KORELY_API_KEY", "KORELY_PROFILE", "KORELY_BASE_URL"):
            os.environ.pop(k, None)
        self.addCleanup(self.env.stop)

    def save(self, cfg):
        with open(os.path.join(self.home, "config.json"), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)

    def config(self):
        with open(os.path.join(self.home, "config.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def run_cli(self, argv, rec, stdin=None, key="kor_live_test"):
        """`key` None: the command finds its key itself (the saved one)."""
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(Korely, "_send", rec), redirect_stdout(out), redirect_stderr(err), \
                mock.patch.object(sys, "stdin", io.StringIO(stdin or "")):
            rc = cli.main(argv + (["--api-key", key] if key else []))
        return rc, out.getvalue(), err.getvalue()

    def batch_file(self, items):
        fd, path = tempfile.mkstemp(suffix=".json", dir=self.home)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(items, fh)
        return path


class AWriteWithNoIdentity(_OwnConfig):
    def test_is_refused_before_anything_is_sent(self):
        for argv in (["add", "Maria is on Pro."], ["add-fact", "maria", "subscribes_to", "Pro"],
                     ["batch", None]):
            if argv[-1] is None:
                argv = ["batch", self.batch_file([{"content": "a"}])]
            with self.subTest(cmd=argv[0]):
                rec = _Recorder()
                rc, out, err = self.run_cli(argv, rec)
                self.assertEqual(rec.calls, [])
                self.assertEqual(rc, 1)
                self.assertIn("--user-id <id>", err)
                self.assertIn("--agent-id <id>", err)
                self.assertIn("[identity_required]", err)

    def test_with_the_saved_key_the_hint_is_to_save_an_id(self):
        self.save({"api_key": "kor_live_saved"})
        rec = _Recorder()
        rc, _, err = self.run_cli(["add", "x"], rec, key=None)
        self.assertEqual((rc, rec.calls), (1, []))
        self.assertIn("korely init --user-id <id>", err)

    def test_the_refusal_has_the_server_s_code_in_json(self):
        rc, out, err = self.run_cli(["add", "x", "--json"], _Recorder())
        self.assertEqual((rc, out), (1, ""))
        self.assertEqual(json.loads(err)["code"], "identity_required")

    def test_a_batch_names_the_first_item_with_none(self):
        path = self.batch_file([{"content": "a", "agent_id": "bot"}, {"content": "b"},
                                {"content": "c"}])
        rec = _Recorder()
        rc, _, err = self.run_cli(["batch", path], rec)
        self.assertEqual((rc, rec.calls), (1, []))
        self.assertIn("memories[1].user_id or memories[1].agent_id is required", err)

    def test_a_named_profile_is_named_in_the_hint(self):
        self.save({"api_key": "kor_live_saved", "user_id": "maria",
                   "profiles": {"work": {"api_key": "kor_live_w"}}})
        rec = _Recorder()
        rc, _, err = self.run_cli(["add", "x", "--profile", "work"], rec, key=None)
        self.assertEqual((rc, rec.calls), (1, []))
        self.assertIn("korely init --profile work --user-id <id>", err)
        with mock.patch.dict(os.environ, {"KORELY_PROFILE": "work"}):
            rc, _, err = self.run_cli(["add", "x"], rec, key=None)
        self.assertIn("korely init --profile work --user-id <id>", err)

    def test_reads_need_no_identity(self):
        rec = _Recorder((200, {"results": []}), (200, {"facts": [], "total": 0}))
        self.assertEqual(self.run_cli(["search", "plan"], rec)[0], 0)
        self.assertEqual(self.run_cli(["facts"], rec)[0], 0)
        self.assertEqual(len(rec.calls), 2)
        self.assertNotIn("user_id", rec.calls[0]["json"])


class TheSavedIdentity(_OwnConfig):
    def test_every_write_that_names_none_sends_it(self):
        self.save({"api_key": "kor_live_saved", "user_id": "maria"})
        rec = _Recorder()
        self.assertEqual(self.run_cli(["add", "Maria is on Pro."], rec, key=None)[0], 0)
        self.assertEqual(rec.calls[-1]["json"]["user_id"], "maria")
        self.assertNotIn("agent_id", rec.calls[-1]["json"])
        self.assertEqual(self.run_cli(["add-fact", "maria", "subscribes_to", "Pro"], rec,
                                      key=None)[0], 0)
        self.assertEqual(rec.calls[-1]["json"]["user_id"], "maria")

    def test_the_ids_given_win_and_are_the_whole_identity(self):
        """`--agent-id bot` with `user_id maria` saved writes as the agent
        alone: the saved pair fills a write that names neither, never half of
        one that names an id."""
        self.save({"api_key": "kor_live_saved", "user_id": "maria"})
        rec = _Recorder()
        self.run_cli(["add", "x", "--agent-id", "bot"], rec, key=None)
        self.assertEqual(rec.calls[-1]["json"]["agent_id"], "bot")
        self.assertNotIn("user_id", rec.calls[-1]["json"])
        self.run_cli(["add", "x", "--user-id", "franco"], rec, key=None)
        self.assertEqual(rec.calls[-1]["json"]["user_id"], "franco")

    def test_a_batch_fills_only_the_items_that_name_none(self):
        self.save({"api_key": "kor_live_saved", "agent_id": "importer"})
        path = self.batch_file([{"content": "a"}, {"content": "b", "user_id": "maria"},
                                {"content": "c", "agent_id": "bot"}, "d"])
        rec = _Recorder((202, {"id": "job_1", "status": "processing", "received": 4}))
        self.assertEqual(self.run_cli(["batch", path], rec, key=None)[0], 0)
        self.assertEqual(rec.calls[0]["json"]["memories"], [
            {"content": "a", "agent_id": "importer"}, {"content": "b", "user_id": "maria"},
            {"content": "c", "agent_id": "bot"}, {"content": "d", "agent_id": "importer"}])

    def test_each_profile_has_its_own(self):
        self.save({"api_key": "kor_live_saved", "user_id": "maria",
                   "profiles": {"work": {"api_key": "kor_live_w", "agent_id": "bot"}}})
        rec = _Recorder()
        self.run_cli(["add", "x", "--profile", "work"], rec, key=None)
        self.assertEqual(rec.calls[-1]["json"].get("agent_id"), "bot")
        self.assertNotIn("user_id", rec.calls[-1]["json"])
        with mock.patch.dict(os.environ, {"KORELY_PROFILE": "work"}):
            self.run_cli(["add", "x"], rec, key=None)
        self.assertEqual(rec.calls[-1]["json"].get("agent_id"), "bot")

    def test_it_goes_only_with_the_key_saved_next_to_it(self):
        """A key from KORELY_API_KEY, --api-key or another profile may be
        another project's or account's: the id saved for one's own use would
        name one of its end users. Refused before sending, with the flags."""
        self.save({"api_key": "kor_live_saved", "user_id": "maria",
                   "profiles": {"work": {"api_key": "kor_live_w"}}})
        path = self.batch_file([{"content": "a"}])
        for how, argv, env, hint in (
                ("KORELY_API_KEY", [], {"KORELY_API_KEY": "kor_live_other"},
                 "goes only with the key saved next to it"),
                ("--api-key", ["--api-key", "kor_live_w"], {},
                 "goes only with the key saved next to it"),
                # the profile's own key, and no id saved next to it
                ("another profile", ["--profile", "work"], {},
                 "korely init --profile work --user-id <id>")):
            for cmd in (["add", "x"], ["add-fact", "a", "b", "c"], ["batch", path]):
                with self.subTest(how=how, cmd=cmd[0]), mock.patch.dict(os.environ, env):
                    rec = _Recorder()
                    rc, _, err = self.run_cli(cmd + argv, rec, key=None)
                    self.assertEqual((rc, rec.calls), (1, []))
                    self.assertIn("--user-id <id>", err)
                    self.assertIn(hint, err)
        with mock.patch.dict(os.environ, {"KORELY_API_KEY": "kor_live_saved"}):
            rec = _Recorder()
            self.assertEqual(self.run_cli(["add", "x"], rec, key=None)[0], 0)
        self.assertEqual(rec.calls[-1]["json"]["user_id"], "maria")

    def test_init_does_not_save_an_id_that_no_saved_key_would_carry(self):
        with mock.patch.dict(os.environ, {"KORELY_API_KEY": "kor_live_env"}), \
                mock.patch.object(Korely, "init_agent", side_effect=AssertionError("no signup")):
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                rc = cli.main(["init", "--user-id", "maria"])
        self.assertEqual(rc, 2)
        self.assertIn("--api-key <key> --user-id maria", err.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.home, "config.json")))

    def test_the_sdk_does_not_read_it(self):
        """A program says whose memory each of its writes is: a file on one
        machine never changes where its memories go."""
        self.save({"api_key": "kor_live_saved", "user_id": "maria"})
        rec = _Recorder()
        k = Korely()
        k._send = rec
        k.add("x")
        self.assertNotIn("user_id", rec.calls[-1]["json"])


class InitSavesTheIdentity(_OwnConfig):
    def init(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["init"] + argv)
        return rc, out.getvalue(), err.getvalue()

    def no_signup(self):
        return mock.patch.object(Korely, "init_agent",
                                 side_effect=AssertionError("init must not sign up"))

    def test_with_a_key_saved_only_the_identity_is_saved(self):
        self.save({"api_key": "kor_live_saved", "base_url": "https://api.korely.ai"})
        with self.no_signup():
            rc, out, _ = self.init(["--user-id", "maria"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.config(), {"api_key": "kor_live_saved",
                                         "base_url": "https://api.korely.ai", "user_id": "maria"})
        self.assertIn("as user_id maria", out)
        with self.no_signup():
            self.assertEqual(self.init(["--agent-id", "bot"])[0], 0)
        cfg = self.config()
        self.assertEqual((cfg.get("agent_id"), "user_id" in cfg), ("bot", False))
        with self.no_signup():
            self.init(["--user-id", "maria", "--agent-id", "bot"])
        self.assertEqual((self.config()["user_id"], self.config()["agent_id"]), ("maria", "bot"))

    def test_in_a_named_profile(self):
        self.save({"api_key": "kor_live_saved", "user_id": "maria",
                   "profiles": {"work": {"api_key": "kor_live_w"}}})
        with self.no_signup():
            self.assertEqual(self.init(["--profile", "work", "--agent-id", "bot"])[0], 0)
        cfg = self.config()
        self.assertEqual(cfg["profiles"]["work"], {"api_key": "kor_live_w", "agent_id": "bot"})
        self.assertEqual(cfg["user_id"], "maria")

    def test_with_the_key(self):
        rc, out, _ = self.init(["--api-key", "kor_self_mine", "--base-url", "https://mine.example",
                                "--user-id", "maria"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.config()["user_id"], "maria")
        self.assertIn("as user_id maria", out)
        os.remove(os.path.join(self.home, "config.json"))
        rc, out, _ = self.init(["--api-key", "kor_self_mine", "--base-url", "https://mine.example",
                                "--agent-id", "bot", "--json"])
        self.assertEqual(json.loads(out)["agent_id"], "bot")

    def test_an_empty_id_saves_nothing(self):
        self.save({"api_key": "kor_live_saved", "user_id": "maria"})
        with self.no_signup():
            rc, _, err = self.init(["--user-id", "  "])
        self.assertEqual(rc, 2)
        self.assertIn("--user-id is empty", err)
        self.assertEqual(self.config()["user_id"], "maria")

    def test_a_new_account_is_told_to_say_whose_memory_it_writes(self):
        from korely_memory.models import AgentInitResult

        made = AgentInitResult.from_dict({"api_key": "kor_live_new", "tier": "hobby",
                                          "region": "eu"})
        with mock.patch.object(Korely, "init_agent", return_value=made):
            rc, out, _ = self.init([])
        self.assertEqual(rc, 0)
        self.assertLess(out.index("korely init --user-id <id>"), out.index('korely add "I am'))
        os.remove(os.path.join(self.home, "config.json"))
        with mock.patch.object(Korely, "init_agent", return_value=made):
            rc, out, _ = self.init(["--user-id", "maria"])
        self.assertEqual(self.config()["user_id"], "maria")
        self.assertIn("as user_id maria", out)
        self.assertNotIn("korely init --user-id <id>", out)


    def test_with_a_coding_assistant(self):
        """`init --tool X --user-id u` puts the hooks in `u` and saves it; with
        `--remove` nothing is saved."""
        installed = []
        with mock.patch.object(cli, "_install_tool", lambda a: installed.append(a.user_id) or 0), \
                self.no_signup():
            self.save({"api_key": "kor_live_saved"})
            self.assertEqual(self.init(["--tool", "claude-code", "--user-id", "maria"])[0], 0)
            self.assertEqual(self.config()["user_id"], "maria")
            self.init(["--tool", "claude-code", "--remove", "--agent-id", "bot"])
            self.assertEqual(self.config().get("agent_id"), None)
            os.remove(os.path.join(self.home, "config.json"))
            self.init(["--tool", "codex", "--api-key", "kor_self_mine", "--base-url",
                       "https://mine.example", "--agent-id", "bot"])
            self.assertEqual(self.config()["agent_id"], "bot")
        self.assertEqual(installed, ["maria", None, None])


class TheServersRefusal(_OwnConfig):
    """An id the server reads as nobody (U+200B ZERO WIDTH SPACE alone) is
    sent: what names somebody is the server's rule, and its refusal arrives
    with its code and its message."""

    def test_reaches_the_command_line(self):
        rec = _Recorder((422, dict(_REFUSAL)))
        rc, _, err = self.run_cli(["add", "x", "--user-id", "\u200b"], rec)
        self.assertEqual(rec.calls[0]["json"]["user_id"], "\u200b")
        self.assertEqual(rc, 1)
        self.assertIn(_MESSAGE, err)
        self.assertIn("[identity_required]", err)

    def test_reaches_the_sdk_from_either_shape(self):
        # 1.0.19 sends the pair at the top and inside `detail`; an install
        # that sends `detail` alone reads the same.
        for body in (_REFUSAL, {"detail": _REFUSAL["detail"]}):
            with self.subTest(top="code" in body):
                k = Korely(api_key="kor_live_test")
                k._send = _Recorder((422, dict(body)))
                with self.assertRaises(APIError) as caught:
                    k.add("x")
                e = caught.exception
                self.assertEqual((e.status, e.code, e.message),
                                 (422, "identity_required", _MESSAGE))


class KorelyMcp(_OwnConfig):
    def setUp(self):
        super().setUp()
        from korely_memory import mcp_server as m

        self.m = m
        saved = (m._DEFAULT_USER, m._DEFAULT_AGENT, m._PROFILE, m._ALSO_READ, m.korely_add.__doc__)
        self.addCleanup(self.restore, saved)
        m._DEFAULT_USER = m._DEFAULT_AGENT = m._PROFILE = None
        self.rec = _Recorder()
        k = Korely(api_key="kor_live_test")
        k._send = self.rec
        patcher = mock.patch.object(m, "_client", lambda: k)
        patcher.start()
        self.addCleanup(patcher.stop)

    def restore(self, saved):
        m = self.m
        m._DEFAULT_USER, m._DEFAULT_AGENT, m._PROFILE, m._ALSO_READ, m.korely_add.__doc__ = saved

    def test_a_write_with_no_identity_is_an_error_and_sends_nothing(self):
        out = self.m.korely_add("Maria is on Pro.")
        self.assertTrue(out.startswith("error: user_id or agent_id is required"), out)
        self.assertIn("--user-id <id>", out)
        self.assertEqual(self.rec.calls, [])

    def test_the_ids_in_order(self):
        self.save({"api_key": "kor_live_test", "user_id": "maria"})
        self.m.korely_add("x")
        self.assertEqual(self.rec.calls[-1]["json"].get("user_id"), "maria")
        self.m._DEFAULT_AGENT = "bot"
        self.m.korely_add("x")
        self.assertEqual(self.rec.calls[-1]["json"].get("agent_id"), "bot")
        self.assertNotIn("user_id", self.rec.calls[-1]["json"])
        self.m._DEFAULT_USER = "repo:shop"
        self.m.korely_add("x", agent_id="other")
        self.assertEqual((self.rec.calls[-1]["json"]["user_id"],
                          self.rec.calls[-1]["json"]["agent_id"]), ("repo:shop", "other"))

    def test_the_saved_id_goes_only_with_its_key(self):
        self.save({"api_key": "kor_live_saved", "user_id": "maria"})  # the client's is another
        out = self.m.korely_add("x")
        self.assertTrue(out.startswith("error: user_id or agent_id is required"), out)
        self.assertIn("not with KORELY_API_KEY", out)
        self.assertEqual(self.rec.calls, [])

    def test_the_tool_says_so_when_the_server_has_none(self):
        self.m._note_the_scope()
        listed = self.m.mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = {t["name"]: t["description"] for t in listed["result"]["tools"]}
        self.assertIn("pass user_id or agent_id", tools["korely_add"])

    def test_agent_id_on_the_command_line(self):
        with mock.patch.object(self.m.mcp, "run", lambda: None):
            self.m.main(["--agent-id", "support-bot"])
        self.assertEqual(self.m._DEFAULT_AGENT, "support-bot")
        self.assertNotIn("pass user_id or agent_id", self.m.korely_add.__doc__)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TheExamplesNameTheUser(unittest.TestCase):
    """The package's own examples, run as written: every write they make
    names the end user. `add(..., agent_id=...)` alone was the example, and
    whoever copied it wrote an agent's note in place of the user's memory
    (with no user_id, the store reads "I" as the agent). Mem0, Zep and
    Supermemory name the user in their first write (their quickstarts,
    read 2026-11-14)."""

    @staticmethod
    def _examples():
        import doctest
        import textwrap

        import korely_memory

        # The module's: the block indented under its first paragraph.
        block = [line for line in korely_memory.__doc__.splitlines() if line.startswith("    ")]
        yield "korely_memory", textwrap.dedent("\n".join(block))
        for name in korely_memory.__all__:
            obj = getattr(korely_memory, name)
            if isinstance(obj, type) and obj.__doc__ and ">>>" in obj.__doc__:
                yield name, "".join(e.source for e in doctest.DocTestParser().get_examples(obj.__doc__))

    def test_every_write_of_an_example_names_the_user(self):
        examples = dict(self._examples())
        self.assertIn("Korely", examples)
        for name, code in examples.items():
            with self.subTest(example=name):
                sent = []

                def send(self, method, path, *, params=None, json_body=None, headers=None):
                    sent.append((method, path, json_body))
                    return 201, {"id": "mem_1", "context": "", "sources": []}

                with mock.patch.object(Korely, "_send", send):
                    exec(compile(code, name, "exec"), {"Korely": Korely})
                writes = [body for method, path, body in sent if method == "POST"]
                self.assertTrue(writes, code)
                for body in writes:
                    self.assertTrue(body.get("user_id"), f"{name}: {body}")
