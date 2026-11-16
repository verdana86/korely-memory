"""`korely hook` and `korely init --tool`: one memory for several coding assistants.

No network: the client is a fake that records what would have been sent.
"""
import json
import os
import random
import string
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from korely_memory import hooks


def _random_key(n):
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(n))


class FakeContext:
    def __init__(self, text, sources):
        self.context, self.sources = text, sources


class FakeClient:
    def __init__(self, context="- The user decided to ship on Fridays", fail=False):
        self.added, self.reads, self.context, self.fail = [], [], context, fail
        self.tools = []

    def add(self, content, **kw):
        if self.fail:
            raise RuntimeError("network down")
        self.added.append((content, kw))

    def get_context(self, query, user_id=None, token_budget=800):
        if self.fail:
            raise RuntimeError("network down")
        self.reads.append((query, user_id, token_budget))
        return FakeContext(self.context, ["fct_1"] if self.context else [])


def _git_repo(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c",
                    "user.name=t", "commit", "-q", "--allow-empty", "-m", "start"], check=True)
    return repo


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "gitconfig").write_text("")
        # The machine's own git settings stay out: a global ignore file that
        # already lists .claude/settings.local.json changes what install does.
        self.env = mock.patch.dict(os.environ, {"KORELY_CONFIG_HOME": str(self.tmp / "cfg"),
                                                "GIT_CONFIG_GLOBAL": str(self.tmp / "gitconfig"),
                                                "XDG_CONFIG_HOME": str(self.tmp / "xdg")})
        self.env.start()
        os.environ.pop("CURSOR_VERSION", None)
        self.repo = _git_repo(self.tmp, "shop-api")
        self.background = []

    def tearDown(self):
        self.env.stop()

    def hook(self, event, tool, data, client, every=5):
        data = {"cwd": str(self.repo), **data}
        return hooks.run_hook(event, tool, data, every=every, client_factory=lambda: client,
                              background=self.background.append)


class Redaction(unittest.TestCase):
    def test_generated_keys_are_masked_whoever_made_them(self):
        for key in ("sk-proj-" + _random_key(48), "AIza" + _random_key(35),
                    "ghp_" + _random_key(36), "kor_live_" + _random_key(40),
                    "xoxb-" + _random_key(12) + "-" + _random_key(24), _random_key(24)):
            self.assertNotIn(key, hooks.redact(f"the key is {key} ok"), key)

    def test_hex_keys_are_masked_too(self):
        """A key of hex digits alone has no upper case, and went to the
        server whole: 0 of 2,000 were masked. Masked by its shape, wherever
        it sits in a run (`TOKEN=`, a prefix, `0x`)."""
        rng = random.Random(14)

        def hex_key(n, upper=False):
            k = "".join(rng.choice("0123456789abcdef") for _ in range(n))
            return k.upper() if upper else k

        for key in (hex_key(32), hex_key(40, upper=True), "TOKEN=" + hex_key(32),
                    "token=" + hex_key(40), "dd_" + hex_key(32), "0x" + hex_key(64)):
            self.assertNotIn(key, hooks.redact(f"export {key} ok"), key)
        for n, at_least in ((24, 1800), (32, 1980), (40, 2000), (64, 2000)):
            masked = sum(1 for _ in range(2000)
                         if "[redacted]" in hooks.redact(hex_key(n, upper=rng.random() < 0.5)))
            self.assertGreaterEqual(masked, at_least, n)

    def test_a_hex_key_needs_24_digits_and_letters_among_them(self):
        # The same well-spread digits: 24 are a key, 23 are not (the
        # threshold, where a piece of a run is tested on its own).
        key = "0123456789abcdef01a2b3c4"
        self.assertEqual(hooks.redact(f"token={key}"), "[redacted]")
        self.assertEqual(hooks.redact(f"token={key[:23]}"), f"token={key[:23]}")
        # Digits alone are a number, however spread: no letter, no key.
        number = "012345678901234567890123456789"
        self.assertEqual(hooks.redact(f"order {number}"), f"order {number}")
        self.assertEqual(hooks.redact(f"id={number}"), f"id={number}")

    def test_a_full_commit_hash_is_masked_like_a_hex_key(self):
        # By its shape a 40-digit hex key is a commit hash: both are masked.
        # The short hash git prints, under 24 digits, stays.
        self.assertEqual(hooks.redact("fixed in 11f6ad8ec52a2984abaafd7c3b516503785c2072"),
                         "fixed in [redacted]")
        self.assertEqual(hooks.redact("fixed in 11f6ad8, see 11f6ad8ec52a"),
                         "fixed in 11f6ad8, see 11f6ad8ec52a")

    def test_names_short_hashes_and_ids_stay(self):
        kept = ["getContextForUser2024Request", "MAX_CONTENT_CHARS_2026_LIMIT",
                "0b55c4d1-5bbb-4fbd-b844-6f2bbb2bf5d1", "claude-opus-5-5-20261015",
                "github-actions-runner-ubuntu-22-04", "/Users/me/Code/shop-api/src/App2.tsx",
                # hex digits, but not spread like a key, or not mixed
                "deadbeefdeadbeefdeadbeef", "2026111420261114202611142026",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaa1", "feedfacecafebeef00000000"]
        for text in kept:
            self.assertEqual(hooks.redact(text), text)

    def test_private_keys_and_url_passwords(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
        self.assertEqual(hooks.redact(pem), "[redacted private key]")
        self.assertEqual(hooks.redact("postgres://app:s3cret@db:5432/x"),
                         "postgres://[redacted]@db:5432/x")


class Scope(unittest.TestCase):
    def test_a_worktree_belongs_to_its_repository(self):
        tmp = Path(tempfile.mkdtemp())
        repo = _git_repo(tmp, "shop-api")
        wt = tmp / "elsewhere" / "feature-x"
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt)], check=True)
        self.assertEqual(hooks.scope_for(str(repo)), "repo:shop-api")
        self.assertEqual(hooks.scope_for(str(wt)), "repo:shop-api")
        self.assertEqual(hooks.scope_for(str(repo / ".")), "repo:shop-api")

    def test_a_fixed_scope_wins(self):
        self.assertEqual(hooks.scope_for("/nowhere", "me"), "me")


class SessionFlow(Base):
    def test_start_hands_the_repository_memory_to_claude_code(self):
        client = FakeClient()
        out = json.loads(self.hook("session-start", "claude-code", {"session_id": "s1"}, client))
        spec = out["hookSpecificOutput"]
        self.assertEqual(spec["hookEventName"], "SessionStart")
        self.assertIn("ship on Fridays", spec["additionalContext"])
        self.assertEqual(client.reads[0][1], "repo:shop-api")
        self.assertEqual(self.background, [None])  # stale sessions are sent

    def test_an_empty_memory_adds_nothing(self):
        out = self.hook("session-start", "claude-code", {"session_id": "s1"}, FakeClient(""))
        self.assertEqual(out, "")

    def test_the_first_prompt_only_brings_its_own_context(self):
        client = FakeClient()
        first = self.hook("prompt", "claude-code", {"session_id": "s1", "prompt": "fix login"},
                          client)
        self.assertEqual(json.loads(first)["hookSpecificOutput"]["hookEventName"],
                         "UserPromptSubmit")
        self.assertEqual(client.reads[0][0], "fix login")
        again = self.hook("prompt", "claude-code", {"session_id": "s1", "prompt": "and tests"},
                          client)
        self.assertEqual(again, "")
        self.assertEqual(len(client.reads), 1)

    def test_five_answers_make_one_memory_with_secrets_masked(self):
        client = FakeClient()
        key = "sk-" + _random_key(40)
        for i in range(5):
            self.hook("prompt", "claude-code", {"session_id": "s1",
                                                "prompt": f"step {i} use {key}"}, client)
            self.hook("stop", "claude-code", {"session_id": "s1",
                                              "last_assistant_message": f"done {i}"}, client)
        self.assertEqual(self.background, ["s1"])
        self.assertEqual(hooks.flush("s1", lambda tool=None: client.tools.append(tool) or client), 1)
        self.assertEqual(client.tools, ["claude-code"])  # sent as X-Korely-Client
        content, kw = client.added[0]
        self.assertIn("A coding session in the repository shop-api, with Claude Code", content)
        self.assertIn("Developer: step 4 use [redacted]", content)
        self.assertIn("Assistant: done 4", content)
        self.assertNotIn(key, content)
        self.assertEqual(kw["user_id"], "repo:shop-api")
        self.assertEqual(kw["run_id"], "s1")
        self.assertEqual(kw["metadata"], {"source": "claude-code", "repo": "shop-api"})
        self.assertTrue(kw["timestamp"])
        self.assertEqual(kw["kind"], "session")  # a read can leave it out
        # Sent once: a second flush has nothing to send.
        self.assertEqual(hooks.flush("s1", lambda tool=None: client), 0)

    def test_a_server_that_does_not_know_kind_gets_the_session_as_a_note(self):
        from korely_memory.exceptions import APIError

        class Older(FakeClient):
            def add(self, content, **kw):
                if "kind" in kw:
                    raise APIError("body.kind: Extra inputs are not permitted", status=422)
                super().add(content, **kw)

        client = Older()
        self.hook("prompt", "claude-code", {"session_id": "s1", "prompt": "step"}, client)
        self.hook("stop", "claude-code", {"session_id": "s1", "last_assistant_message": "done"},
                  client)
        self.assertEqual(hooks.flush("s1", lambda tool=None: client), 1)
        content, kw = client.added[0]
        self.assertNotIn("kind", kw)
        self.assertIn("Developer: step", content)

    def test_session_end_sends_the_rest_and_leaves_no_file(self):
        client = FakeClient()
        self.hook("prompt", "claude-code", {"session_id": "s2", "prompt": "rename it"}, client)
        self.hook("stop", "claude-code", {"session_id": "s2", "last_assistant_message": "ok"},
                  client)
        self.hook("session-end", "claude-code", {"session_id": "s2"}, client)
        self.assertEqual(self.background, ["s2"])
        hooks.flush("s2", lambda tool=None: client)
        self.assertEqual(len(client.added), 1)
        self.assertEqual(list(hooks._sessions().glob("s2*")), [])

    def test_a_failed_send_waits_for_the_next_start(self):
        down = FakeClient(fail=True)
        self.hook("prompt", "claude-code", {"session_id": "s3", "prompt": "x"}, down)
        self.hook("pre-compact", "claude-code", {"session_id": "s3"}, down)
        self.assertEqual(hooks.flush("s3", lambda tool=None: down), 0)
        boxes = list(hooks._sessions().glob("s3.out*.json"))
        self.assertEqual(len(boxes), 1)
        log = (Path(os.environ["KORELY_CONFIG_HOME"]) / "hook.log").read_text()
        self.assertIn("network down", log)
        self.assertNotIn('"x"', log)
        up = FakeClient()
        self.assertEqual(hooks.flush(None, lambda tool=None: up), 1)  # what session-start runs
        self.assertIn("Developer: x", up.added[0][0])

    def test_every_attempt_sends_the_outbox_s_one_key(self):
        """The 8 s timeout can run out while a proxy holds the send through a
        restart of the server, which then makes it: the next attempt must be
        the same write, so the server answers what the first made."""
        sent = []

        class Timeout(FakeClient):
            def add(self, content, **kw):
                sent.append(kw.get("idempotency_key"))
                raise TimeoutError("timed out")

        self.hook("prompt", "claude-code", {"session_id": "k1", "prompt": "once"}, Timeout())
        self.hook("pre-compact", "claude-code", {"session_id": "k1"}, Timeout())
        self.assertEqual(hooks.flush("k1", lambda tool=None: Timeout()), 0)
        self.assertEqual(hooks.flush(None, lambda tool=None: Timeout()), 0)
        up = FakeClient()
        self.assertEqual(hooks.flush(None, lambda tool=None: up), 1)
        sent.append(up.added[0][1]["idempotency_key"])
        self.assertEqual(len(sent), 3)
        self.assertEqual(len(set(sent)), 1)
        self.assertRegex(sent[0], r"^[0-9a-f]{32}$")

    def test_each_outbox_has_its_own_key(self):
        client = FakeClient()
        for session in ("k2", "k3"):
            self.hook("prompt", "claude-code", {"session_id": session, "prompt": "x"}, client)
            hooks.flush(session, lambda tool=None: client)
        keys = [kw["idempotency_key"] for _, kw in client.added]
        self.assertEqual(len(keys), 2)
        self.assertNotEqual(keys[0], keys[1])

    def test_an_outbox_from_before_keys_gets_one_and_keeps_it(self):
        self.hook("prompt", "claude-code", {"session_id": "k4", "prompt": "x"}, FakeClient())
        box = hooks._claim(hooks._session_file("k4"))
        old = json.loads(box.read_text())
        del old["idempotency_key"]
        box.write_text(json.dumps(old))
        self.assertFalse(hooks.send_outbox(box, lambda tool=None: FakeClient(fail=True)))
        kept = json.loads(box.read_text())["idempotency_key"]
        up = FakeClient()
        self.assertTrue(hooks.send_outbox(box, lambda tool=None: up))
        self.assertEqual(up.added[0][1]["idempotency_key"], kept)

    def test_two_senders_never_send_the_same_turns(self):
        client = FakeClient()
        self.hook("prompt", "claude-code", {"session_id": "d1", "prompt": "once"}, client)
        box = hooks._claim(hooks._session_file("d1"))
        os.rename(box, box.with_name(box.name[:-5] + ".sending"))  # the other sender
        self.assertFalse(hooks.send_outbox(box, lambda tool=None: client))
        self.assertEqual(client.added, [])

    def test_a_hook_never_raises_and_never_blocks(self):
        out = self.hook("session-start", "claude-code", {"session_id": "s4"},
                        FakeClient(fail=True))
        # It answers, and what it answers says the read failed (NotRead below).
        self.assertIn("was not read", json.loads(out)["hookSpecificOutput"]["additionalContext"])


class NotRead(Base):
    """Every read failing handed the assistant nothing, as an empty memory
    does: the session went on without knowing that a memory was there and
    unread (a timeout at startup, a key that ended), and only hook.log
    knew."""

    def test_a_read_that_fails_says_so_where_the_memory_would_be(self):
        # A fixed key of the masked shape: a random one is of letters alone
        # now and then, which the masking declares it does not catch.
        key = "kQ7vR2mX9pL4sT8wN3bY6cF1hJ5dG0aZx4Ve"

        class Down(FakeClient):
            def get_context(self, query, user_id=None, token_budget=800):
                raise RuntimeError(f"read operation timed out (token {key})")

        for tool, event, data in (
                ("claude-code", "session-start", {"session_id": "n1"}),
                ("claude-code", "prompt", {"session_id": "n2", "prompt": "fix login"}),
                ("cursor", "session-start", {"session_id": "n3"})):
            with self.subTest(tool=tool, event=event):
                out = json.loads(self.hook(event, tool, data, Down()))
                text = (out["additional_context"] if tool == "cursor"
                        else out["hookSpecificOutput"]["additionalContext"])
                self.assertIn("Korely memory of user_id repo:shop-api was not read "
                              "(RuntimeError: read operation timed out", text)
                self.assertIn("it is missing from this context", text)
                self.assertNotIn("this session starts", text)  # the prompt's is not the start
                self.assertIn("hook.log has the line", text)
                self.assertNotIn(key, text)  # masked as a session's text is
        log = (Path(os.environ["KORELY_CONFIG_HOME"]) / "hook.log").read_text()
        self.assertIn("user_id repo:shop-api: RuntimeError: read operation timed out", log)

    def test_only_the_block_that_failed_is_replaced(self):
        shared = FakeClient("- shared fact")

        def factory(profile=None):
            if profile:
                raise RuntimeError("no such profile")
            return shared

        out = json.loads(hooks.run_hook(
            "session-start", "claude-code", {"session_id": "n4", "cwd": str(self.repo)},
            client_factory=factory, background=self.background.append, also_read=["gone"]))
        text = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("shared fact", text)
        self.assertNotIn("repo:shop-api was not read", text)
        self.assertIn("Korely memory of profile gone was not read (RuntimeError: no such "
                      "profile)", text)
        self.assertLess(text.index("shared fact"), text.index("profile gone was not read"))

    def _line(self, error):
        class Down(FakeClient):
            def get_context(self, query, user_id=None, token_budget=800):
                raise error

        text = json.loads(self.hook("session-start", "claude-code", {"session_id": "n6"},
                                    Down()))["hookSpecificOutput"]["additionalContext"]
        return text[text.index("Korely memory of"):]

    def test_the_reason_is_one_line(self):
        line = self._line(RuntimeError("HTTP 502\nBad Gateway\r\n\tupstream closed"))
        self.assertIn("(RuntimeError: HTTP 502 Bad Gateway upstream closed)", line)
        self.assertNotIn("\n", line)

    def test_a_long_reason_is_cut_to_its_start_and_end(self):
        line = self._line(RuntimeError("first words " + "x" * 5000 + " last words"))
        reason = line[line.index("(") + 1:line.index("): it is missing")]
        self.assertLessEqual(len(reason), hooks.REASON_CHARS)
        self.assertTrue(reason.startswith("RuntimeError: first words"))
        self.assertTrue(reason.endswith("last words"))
        self.assertIn(" [...] ", reason)

    def test_an_empty_memory_still_adds_nothing(self):
        self.assertEqual(self.hook("session-start", "claude-code", {"session_id": "n5"},
                                   FakeClient("")), "")


class KeyEnds(Base):
    """Korely 1.0.23 says on every answer when the key ends
    (Korely-Key-Expires-At); the hook never read it, so a key's end reached
    the session as a 401 in hook.log. A real server in this process, and the
    hook's own client with its key where `korely init` saves it or in
    KORELY_API_KEY: the header has to travel the whole way, and the command
    the line gives has to replace the key that ends, not another."""

    # Fixed keys of the self-hosted shape (they go to 127.0.0.1).
    DEFAULT = "kor_self_DDDDDDDDdefaultprofileAAAA"
    WORK = "kor_self_WWWWWWWWworkprofileBBBBBBB"
    ENV = "kor_self_EEEEEEEEfromtheenvCCCCCCCC"
    NEW = "kor_self_NNNNNNNNthenewkeyDDDDDDDDD"

    def setUp(self):
        super().setUp()
        from datetime import datetime, timedelta, timezone

        from test_client import _Server

        self.ending = {}  # key -> days left; a key not here never ends

        def answer(method, path):
            key = self.server.seen[-1][2].get("Authorization", "")[len("Bearer "):]
            headers = {}
            if key in self.ending:
                at = datetime.now(timezone.utc) + timedelta(days=self.ending[key])
                headers["Korely-Key-Expires-At"] = at.isoformat(timespec="seconds")
            return 200, headers, {"context": "## Known facts\n- The team ships on Fridays",
                                  "tokens": 9, "sources": ["fct_1"]}

        self.server = _Server(answer)
        self.addCleanup(self.server.close)
        # Base's patch of the environment puts it all back at tearDown. A
        # second patch started here would be stopped after it, and put back
        # this test's KORELY_CONFIG_HOME for every test after.
        os.environ.update({"no_proxy": "*", "NO_PROXY": "*"})
        for name in ("KORELY_API_KEY", "KORELY_BASE_URL", "KORELY_PROFILE"):
            os.environ.pop(name, None)
        self.config = Path(os.environ["KORELY_CONFIG_HOME"]) / "config.json"
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self._save({"api_key": self.DEFAULT, "base_url": self.server.url,
                    "profiles": {"work": {"api_key": self.WORK, "base_url": self.server.url}}})

    def _save(self, cfg):
        self.config.write_text(json.dumps(cfg))

    def _saved(self):
        return json.loads(self.config.read_text())

    def _start(self, also_read=()):
        """The session start's context, read with the hooks' own client."""
        out = hooks.run_hook("session-start", "claude-code",
                             {"session_id": "k1", "cwd": str(self.repo)},
                             background=self.background.append, also_read=also_read)
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]

    def _follow(self, text):
        """Run the line's command as written, the new key in its place."""
        import contextlib
        import io

        from korely_memory import cli

        line = next(x for x in text.split("\n") if "The Korely key" in x)
        command = line.split("`")[1].replace("<new key>", self.NEW)
        self.assertTrue(command.startswith("korely init "), command)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(command.split()[1:]), 0, command)

    def test_a_key_that_ends_within_two_weeks_is_one_line(self):
        self.ending[self.DEFAULT] = 3.5
        text = self._start()
        self.assertIn("ships on Fridays", text)
        self.assertIn("The Korely key these hooks use ends on ", text)
        self.assertIn("UTC (in 3 days): after that Korely refuses its reads and writes (401)",
                      text)
        self.assertIn(f"`korely init --api-key <new key> --force --base-url {self.server.url}`",
                      text)
        self.assertEqual(text.count("The Korely key"), 1)  # one key, read twice or not
        self._follow(text)
        self.assertEqual(self._saved()["api_key"], self.NEW)
        self.assertNotIn("Korely key", self._start())

    def test_the_key_of_korely_profile_is_the_one_replaced(self):
        """With KORELY_PROFILE=work the hooks send the work key, and the line
        said `korely init --api-key <new key> --force`, which writes the
        default profile: the default key was replaced, lost for good, and the
        work key that ends stayed."""
        self.ending[self.WORK] = 2.5
        with mock.patch.dict(os.environ, {"KORELY_PROFILE": "work"}):
            text = self._start()
            self.assertIn("--force --base-url ", text)
            self.assertIn(" --profile work`", text)
            self._follow(text)
            saved = self._saved()
            self.assertEqual(saved["api_key"], self.DEFAULT)  # the key that does not end
            self.assertEqual(saved["profiles"]["work"]["api_key"], self.NEW)
            self.assertNotIn("Korely key", self._start())

    def test_a_key_from_korely_api_key_is_changed_in_the_variable(self):
        self.ending[self.ENV] = 2.5
        with mock.patch.dict(os.environ, {"KORELY_API_KEY": self.ENV,
                                          "KORELY_BASE_URL": self.server.url}):
            text = self._start()
        self.assertEqual(self.server.seen[-1][2]["Authorization"], "Bearer " + self.ENV)
        self.assertIn("Make a new key in the Korely dashboard and put it in KORELY_API_KEY, "
                      "where these hooks read this one", text)
        self.assertNotIn("korely init --api-key", text)  # it would replace a saved key

    def test_a_profile_key_names_its_profile(self):
        self.ending[self.WORK] = 0.2
        text = self._start(also_read=["work"])
        self.assertIn("The Korely key of the profile work ends on ", text)
        self.assertIn("(in less than a day)", text)
        self.assertIn(f"`korely init --api-key <new key> --force --base-url {self.server.url} "
                      f"--profile work`", text)
        self.assertNotIn("The Korely key these hooks use", text)

    def test_a_key_further_off_or_without_an_end_adds_nothing(self):
        for ends in (hooks.KEY_WARN_DAYS + 1, None):
            with self.subTest(ends=ends):
                self.ending.clear()
                if ends is not None:
                    self.ending[self.DEFAULT] = ends
                text = self._start()
                self.assertIn("ships on Fridays", text)
                self.assertNotIn("Korely key", text)

    def test_an_end_that_cannot_be_read_never_costs_the_memory(self):
        """`0001-01-01T00:30:00+01:00` is a year 0 in UTC: its OverflowError,
        raised after the reads, made the hook answer nothing at all."""
        client = FakeClient("- The team ships on Fridays")
        for ends in ("0001-01-01T00:30:00+01:00", "9999-12-31T23:30:00-01:00", "garbage", ""):
            with self.subTest(ends=ends):
                client.key_expires_at = ends
                text = json.loads(self.hook("session-start", "claude-code",
                                            {"session_id": "k2"}, client))
                text = text["hookSpecificOutput"]["additionalContext"]
                self.assertIn("ships on Fridays", text)
                self.assertNotIn("Korely key", text)

    def test_on_the_cloud_the_line_says_an_unadopted_key_needs_none(self):
        from datetime import datetime, timedelta, timezone

        class Cloud:
            base_url = "https://api.korely.ai"
            _key_profile = "default"
            key_expires_at = (datetime.now(timezone.utc) + timedelta(days=6, hours=1)) \
                .isoformat(timespec="seconds")

        line = hooks._key_ends(Cloud(), "these hooks use")
        self.assertIn("(in 6 days)", line)
        self.assertIn("save it with `korely init --api-key <new key> --force`.", line)
        self.assertIn("A key `korely init --agent` made, whose account nobody has signed in "
                      "to yet, needs no new key: signing in to the Korely dashboard with it "
                      "lifts that end.", line)
        Cloud.base_url = self.server.url  # one's own server: no such account
        self.assertNotIn("korely init --agent", hooks._key_ends(Cloud(), "these hooks use"))

    def test_the_client_keeps_the_end_its_server_said(self):
        from test_client import _Server
        from korely_memory import Korely

        ends = "2026-12-01T09:00:00+00:00"
        answers = [(200, {"Korely-Key-Expires-At": ends}, {"ok": True}),
                   (401, {}, {"code": "unauthorized", "message": "Invalid API key"}),
                   (422, {"Korely-Key-Expires-At": "2026-12-02T09:00:00+00:00"},
                    {"code": "validation_error", "message": "bad field"})]
        server = _Server(lambda method, path: answers.pop(0))
        self.addCleanup(server.close)
        k = Korely(api_key="kor_self_hook_probe_0123456789", base_url=server.url)
        self.assertIsNone(k.key_expires_at)
        k.ping()
        self.assertEqual(k.key_expires_at, ends)
        with self.assertRaises(Exception):
            k.ping()
        # a 401 carries no end: the one said stays, it is the same key
        self.assertEqual(k.key_expires_at, ends)
        # a refusal to a key that ends still says when
        with self.assertRaises(Exception):
            k.ping()
        self.assertEqual(k.key_expires_at, "2026-12-02T09:00:00+00:00")

    def test_the_client_says_where_it_found_its_key(self):
        from korely_memory import Korely

        url = self.server.url
        self.assertIsNone(Korely(api_key=self.ENV, base_url=url)._key_profile)
        self.assertEqual(Korely()._key_profile, "default")
        self.assertEqual(Korely(profile="work")._key_profile, "work")
        with mock.patch.dict(os.environ, {"KORELY_PROFILE": "work"}):
            self.assertEqual(Korely()._key_profile, "work")
            with mock.patch.dict(os.environ, {"KORELY_API_KEY": self.ENV,
                                              "KORELY_BASE_URL": url}):
                self.assertIsNone(Korely()._key_profile)
                # a profile named is a decision: the variable does not count
                self.assertEqual(Korely(profile="work")._key_profile, "work")
        self.assertIsNone(Korely.__new__(Korely)._key_profile)


class Tools(Base):
    def test_inside_cursor_the_claude_code_hooks_step_aside(self):
        with mock.patch.dict(os.environ, {"CURSOR_VERSION": "2.4"}):
            out = self.hook("prompt", "claude-code", {"session_id": "c1", "prompt": "hi"},
                            FakeClient())
        self.assertEqual(out, "")
        self.assertEqual(list(hooks._sessions().glob("c1*")), [])

    def test_cursor_reads_the_answer_at_after_agent_response(self):
        client = FakeClient()
        data = {"conversation_id": "k1", "workspace_roots": [str(self.repo)]}
        start = json.loads(hooks.run_hook("session-start", "cursor", data,
                                          client_factory=lambda: client,
                                          background=self.background.append))
        self.assertIn("ship on Fridays", start["additional_context"])
        prompt = hooks.run_hook("prompt", "cursor", {**data, "prompt": "hello"},
                                client_factory=lambda: client, background=lambda s: None)
        self.assertEqual(json.loads(prompt), {"continue": True})
        hooks.run_hook("stop", "cursor", {**data, "text": "hi there"},
                       client_factory=lambda: client, background=lambda s: None)
        hooks.flush("k1", lambda tool=None: client)
        self.assertIn("Assistant: hi there", client.added[0][0])
        self.assertIn("with Cursor", client.added[0][0])

    def test_codex_stop_prints_nothing(self):
        out = self.hook("stop", "codex", {"session_id": "x1", "last_assistant_message": "ok"},
                        FakeClient())
        self.assertEqual(out, "")

    def test_a_stop_without_the_text_reads_the_transcript(self):
        t = self.tmp / "t.jsonl"
        t.write_text("\n".join(json.dumps(r) for r in [
            {"type": "user", "message": {"role": "user", "content": "q"}},
            {"type": "assistant", "message": {"role": "assistant",
                                              "content": [{"type": "text", "text": "the end"}]}},
        ]))
        client = FakeClient()
        self.hook("stop", "claude-code", {"session_id": "t1", "transcript_path": str(t)}, client)
        hooks.flush("t1", lambda tool=None: client)
        self.assertIn("Assistant: the end", client.added[0][0])


class Merging(unittest.TestCase):
    def test_installing_twice_leaves_one_hook_per_event_and_keeps_the_rest(self):
        mine = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]},
                "model": "opus"}
        cmds = {e: f"/bin/korely hook {e} --tool claude-code" for e in hooks.EVENTS}
        once = hooks.merge_hooks(mine, "claude-code", cmds)
        twice = hooks.merge_hooks(once, "claude-code", cmds)
        self.assertEqual(once, twice)
        self.assertEqual(twice["model"], "opus")
        self.assertEqual(len(twice["hooks"]["Stop"]), 2)
        self.assertEqual(twice["hooks"]["SessionStart"][0]["matcher"],
                         "startup|resume|clear|compact")
        self.assertNotIn("matcher", twice["hooks"]["UserPromptSubmit"][0])
        self.assertEqual(twice["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"], 3)
        removed = hooks.merge_hooks(twice, "claude-code", {})
        self.assertEqual(removed, mine)

    def test_cursor_hooks_are_flat_and_versioned(self):
        cmds = {e: f"korely hook {e} --tool cursor" for e in hooks.EVENTS}
        out = hooks.merge_hooks({}, "cursor", cmds)
        self.assertEqual(out["version"], 1)
        self.assertEqual(out["hooks"]["afterAgentResponse"][0]["command"],
                         "korely hook stop --tool cursor")
        self.assertEqual(hooks.merge_hooks(out, "cursor", {}), {"version": 1})

    def test_codex_toml_keeps_every_other_line(self):
        original = 'model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "x"\n'
        added = hooks.merge_mcp_toml(original, ["/bin/korely-mcp", "--repo-scope"])
        self.assertIn('[mcp_servers.korely]\ncommand = "/bin/korely-mcp"\nargs = ["--repo-scope"]',
                      added)
        self.assertEqual(hooks.merge_mcp_toml(added, ["/bin/korely-mcp", "--repo-scope"]), added)
        self.assertEqual(hooks.merge_mcp_toml(added, None), original)


class Installing(Base):
    def test_claude_code_this_repository_only(self):
        calls = []
        run = lambda cmd, **kw: calls.append(cmd) or mock.Mock(returncode=0)  # noqa: E731
        report = hooks.install("claude-code", self.repo, base="/opt/bin/korely", run=run)
        settings = json.loads((self.repo / ".claude" / "settings.local.json").read_text())
        self.assertEqual(settings["hooks"]["Stop"][0]["hooks"][0]["command"],
                         "/opt/bin/korely hook stop --tool claude-code")
        self.assertIn(["claude", "mcp", "add", "--scope", "local", "korely", "--",
                       "/opt/bin/korely-mcp", "--repo-scope"], calls)
        exclude = (self.repo / ".git" / "info" / "exclude").read_text()
        self.assertIn(".claude/settings.local.json", exclude)
        self.assertTrue(any("exclude" in line for line in report))
        again = hooks.install("claude-code", self.repo, base="/opt/bin/korely", run=run)
        self.assertFalse(any("exclude" in line for line in again))  # listed once
        hooks.install("claude-code", self.repo, base="/opt/bin/korely", run=run, remove=True)
        settings = json.loads((self.repo / ".claude" / "settings.local.json").read_text())
        self.assertEqual(settings, {})

    def test_a_committed_file_is_not_touched(self):
        f = self.repo / ".cursor" / "hooks.json"
        f.parent.mkdir()
        f.write_text('{"version": 1, "hooks": {}}\n')
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        with self.assertRaises(hooks.InstallError):
            hooks.install("cursor", self.repo, base="/opt/bin/korely")
        self.assertEqual(f.read_text(), '{"version": 1, "hooks": {}}\n')

    def test_removing_what_was_never_installed_creates_nothing(self):
        hooks.install("cursor", self.repo, remove=True, base="/opt/bin/korely")
        self.assertFalse((self.repo / ".cursor").exists())

    def test_no_mcp_touches_no_mcp_setting(self):
        """`--no-mcp` (hooks only) ran `claude mcp remove ... korely` and
        rewrote .cursor/mcp.json and .codex/config.toml without `korely`: a
        server the developer had added by hand under that name was deleted."""
        calls = []
        run = lambda cmd, **kw: calls.append(cmd) or mock.Mock(returncode=0)  # noqa: E731
        cursor = self.repo / ".cursor" / "mcp.json"
        codex = self.repo / ".codex" / "config.toml"
        cursor.parent.mkdir()
        codex.parent.mkdir()
        mine = '{"mcpServers": {"korely": {"url": "https://api.korely.ai/agent/mcp"}}}\n'
        toml = '[mcp_servers.korely]\nurl = "https://api.korely.ai/agent/mcp"\n'
        cursor.write_text(mine)
        codex.write_text(toml)
        for tool in ("claude-code", "cursor", "codex"):
            for remove in (False, True):
                with self.subTest(tool=tool, remove=remove):
                    report = hooks.install(tool, self.repo, base="/opt/bin/korely", run=run,
                                           mcp=False, remove=remove, also_read=["private"])
                    self.assertEqual(calls, [])  # no `claude mcp` at all
                    self.assertEqual(cursor.read_text(), mine)
                    self.assertEqual(codex.read_text(), toml)
                    self.assertIn("MCP settings not touched (--no-mcp): a korely server "
                                  "already there stays as it is", report)
        # the hooks went in and out all the same
        self.assertNotIn("hooks", json.loads((self.repo / ".cursor" / "hooks.json").read_text()))
        exclude = (self.repo / ".git" / "info" / "exclude").read_text()
        self.assertIn(".cursor/hooks.json", exclude)
        self.assertNotIn(".cursor/mcp.json", exclude)  # not ours to keep out of git

    def test_no_mcp_installs_next_to_a_committed_mcp_file(self):
        """The file `--no-mcp` does not write is not its to judge: a committed
        .cursor/mcp.json stopped a hooks-only install."""
        f = self.repo / ".cursor" / "mcp.json"
        f.parent.mkdir()
        f.write_text('{"mcpServers": {}}\n')
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        with self.assertRaises(hooks.InstallError):
            hooks.install("cursor", self.repo, base="/opt/bin/korely")
        hooks.install("cursor", self.repo, base="/opt/bin/korely", mcp=False)
        self.assertIn("sessionStart",
                      json.loads((self.repo / ".cursor" / "hooks.json").read_text())["hooks"])
        self.assertEqual(f.read_text(), '{"mcpServers": {}}\n')

    def test_codex_everywhere(self):
        with mock.patch.object(Path, "home", return_value=self.tmp / "home"):
            hooks.install("codex", self.repo, everywhere=True, base="/opt/bin/korely")
            home = self.tmp / "home" / ".codex"
            data = json.loads((home / "hooks.json").read_text())
            self.assertIn("SessionStart", data["hooks"])
            self.assertIn("[mcp_servers.korely]", (home / "config.toml").read_text())


class ClientHeader(unittest.TestCase):
    def test_the_tool_is_sent_as_x_korely_client(self):
        from korely_memory.client import Korely

        seen = {}

        def fake_urlopen(req, timeout=None, **kw):
            seen.update({k.lower(): v for k, v in req.header_items()})
            raise OSError("stop here")

        with mock.patch("korely_memory.client._open", fake_urlopen):
            k = Korely(api_key="kor_live_x", client="codex")
            with self.assertRaises(Exception):
                k.add("hello", user_id="repo:x")
        self.assertEqual(seen.get("x-korely-client"), "codex")

    def test_without_one_the_header_names_the_library(self):
        from korely_memory import __version__
        from korely_memory.client import Korely

        seen = {}

        def fake_urlopen(req, timeout=None, **kw):
            seen.update({k.lower(): v for k, v in req.header_items()})
            raise OSError("stop here")

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KORELY_CLIENT", None)
            with mock.patch("korely_memory.client._open", fake_urlopen):
                with self.assertRaises(Exception):
                    Korely(api_key="kor_live_x").add("hello")
        self.assertEqual(seen.get("x-korely-client"), "korely-python/" + __version__)


class McpScope(unittest.TestCase):
    def test_the_server_takes_the_hooks_scope(self):
        from korely_memory import mcp_server

        with mock.patch.object(mcp_server.mcp, "run"):
            mcp_server.main(["--user-id", "repo:shop-api"])
        try:
            self.assertEqual(mcp_server._user(None), "repo:shop-api")
            self.assertEqual(mcp_server._user("alex"), "alex")
        finally:
            mcp_server._DEFAULT_USER = None


if __name__ == "__main__":
    unittest.main(verbosity=2)


class Profiles(unittest.TestCase):
    """A second project next to the default one (a private one): its key is a
    named profile in the same config file, read with `--profile`."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = mock.patch.dict(os.environ, {"KORELY_CONFIG_HOME": str(self.tmp)})
        self.env.start()
        os.environ.pop("KORELY_PROFILE", None)
        os.environ.pop("KORELY_API_KEY", None)

    def tearDown(self):
        self.env.stop()

    def _init(self, *extra):
        from korely_memory import cli

        with mock.patch("sys.stdout"):
            return cli.main(["init", *extra])

    def test_init_saves_a_named_profile_next_to_the_default(self):
        self.assertEqual(self._init("--api-key", "kor_live_shared"), 0)
        self.assertEqual(self._init("--api-key", "kor_live_private", "--profile", "private"), 0)
        cfg = json.loads((self.tmp / "config.json").read_text())
        self.assertEqual(cfg["api_key"], "kor_live_shared")
        self.assertEqual(cfg["profiles"]["private"]["api_key"], "kor_live_private")
        # A key already saved in a profile is not replaced without --force.
        with mock.patch("sys.stderr"):
            self.assertEqual(self._init("--api-key", "kor_live_other", "--profile", "private"), 2)

    def test_a_named_profile_wins_over_the_environment(self):
        from korely_memory.client import Korely, KorelyError

        self._init("--api-key", "kor_live_private", "--profile", "private")
        with mock.patch.dict(os.environ, {"KORELY_API_KEY": "kor_live_env"}):
            self.assertEqual(Korely(profile="private").api_key, "kor_live_private")
            self.assertEqual(Korely().api_key, "kor_live_env")
        with self.assertRaises(KorelyError):
            Korely(profile="nobody")

    def test_the_mcp_server_takes_a_profile(self):
        from korely_memory import mcp_server

        with mock.patch.object(mcp_server.mcp, "run"):
            mcp_server.main(["--profile", "private"])
        try:
            self.assertEqual(mcp_server._PROFILE, "private")
        finally:
            mcp_server._PROFILE = None


class AlsoRead(Base):
    def test_the_start_reads_the_repository_and_the_private_project(self):
        shared, private = FakeClient("- shared fact"), FakeClient("- private fact")
        clients = {None: shared, "private": private}
        out = json.loads(hooks.run_hook(
            "session-start", "claude-code", {"session_id": "p1", "cwd": str(self.repo)},
            client_factory=lambda profile=None: clients[profile],
            background=self.background.append, also_read=["private"]))
        text = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("shared fact", text)
        self.assertIn("Korely memory of the profile private (its whole project):", text)
        self.assertIn("private fact", text)
        self.assertEqual(private.reads[0][1], None)  # the whole project, no repo scope
        self.assertEqual(shared.reads[0][1], "repo:shop-api")

    def test_the_start_reads_a_user_id_across_repositories(self):
        client = FakeClient("- a fact")
        out = json.loads(hooks.run_hook(
            "session-start", "claude-code", {"session_id": "p3", "cwd": str(self.repo)},
            client_factory=lambda profile=None: client,
            background=self.background.append, also_read_user=["founder"]))
        self.assertIn("Korely memory of user_id founder (across repositories):",
                      out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual([r[1] for r in client.reads], ["repo:shop-api", "founder"])
        cmd = hooks.hook_command("/bin/korely", "session-start", "claude-code",
                                 also_read_user=["founder"])
        self.assertTrue(cmd.endswith("--also-read-user founder"))
        self.assertNotIn("--also-read-user",
                         hooks.hook_command("/bin/korely", "stop", "claude-code",
                                            also_read_user=["founder"]))

    def test_the_reads_of_a_start_share_one_time_budget(self):
        """Three reads in turn, each allowed 8 s: a slow server kept the start
        24 s, past the 10 s Claude Code gives it, and all the context was lost."""
        now = [100.0]

        class Slow(FakeClient):
            def __init__(self, text):
                super().__init__(text)
                self.timeout = hooks.NETWORK_TIMEOUT
                self.allowed = []

            def get_context(self, query, user_id=None, token_budget=800):
                self.allowed.append(self.timeout)
                now[0] += min(5.0, self.timeout)  # the server takes 5 s, or the client gives up
                return super().get_context(query, user_id=user_id, token_budget=token_budget)

        shared, private = Slow("- shared fact"), Slow("- private fact")
        clients = {None: shared, "private": private}
        with mock.patch.object(hooks, "_monotonic", lambda: now[0], create=True):
            out = json.loads(hooks.run_hook(
                "session-start", "claude-code", {"session_id": "p9", "cwd": str(self.repo)},
                client_factory=lambda profile=None: clients[profile],
                background=self.background.append,
                also_read_user=["founder"], also_read=["private"]))
        self.assertLessEqual(now[0] - 100.0, hooks.NETWORK_TIMEOUT)
        # the repository's read had all the time; the user_id's read what was left
        self.assertEqual(shared.allowed, [hooks.NETWORK_TIMEOUT, 3.0])
        # nothing left for the profile: not begun, and the others still go
        self.assertEqual(private.allowed, [])
        text = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("shared fact", text)

    def test_a_repository_read_that_fails_leaves_the_rest(self):
        """The repository's read failed and the others were never begun, while
        the docstring said a failed block never takes the rest: the session
        started with no memory although the private project could be read."""
        class Down(FakeClient):
            def get_context(self, query, user_id=None, token_budget=800):
                raise RuntimeError("503 from the shared project")

        shared, private = Down(), FakeClient("- private fact")
        (self.tmp / "cfg").mkdir(exist_ok=True)  # where hook.log goes
        out = json.loads(hooks.run_hook(
            "session-start", "claude-code", {"session_id": "p3", "cwd": str(self.repo)},
            client_factory=lambda profile=None: private if profile else shared,
            background=self.background.append, also_read=["private"]))
        self.assertIn("private fact", out["hookSpecificOutput"]["additionalContext"])
        log = (Path(os.environ["KORELY_CONFIG_HOME"]) / "hook.log").read_text()
        self.assertIn("RuntimeError: 503 from the shared project", log)

    def test_a_profile_that_fails_leaves_the_rest(self):
        def factory(profile=None):
            if profile:
                raise RuntimeError("no such profile")
            return FakeClient("- shared fact")

        out = json.loads(hooks.run_hook(
            "session-start", "claude-code", {"session_id": "p2", "cwd": str(self.repo)},
            client_factory=factory, background=self.background.append, also_read=["gone"]))
        self.assertIn("shared fact", out["hookSpecificOutput"]["additionalContext"])

    def test_install_adds_the_reads_and_a_server_per_profile(self):
        with mock.patch.object(Path, "home", return_value=self.tmp / "home"):
            hooks.install("cursor", self.repo, everywhere=True, base="/opt/bin/korely",
                          also_read=["private"])
            home = self.tmp / "home" / ".cursor"
            h = json.loads((home / "hooks.json").read_text())["hooks"]
            self.assertTrue(h["sessionStart"][0]["command"].endswith("--also-read private"))
            self.assertNotIn("--also-read", h["afterAgentResponse"][0]["command"])
            servers = json.loads((home / "mcp.json").read_text())["mcpServers"]
            self.assertEqual(servers["korely"]["args"], ["--repo-scope"])
            self.assertEqual(servers["korely-private"]["args"], ["--profile", "private"])
