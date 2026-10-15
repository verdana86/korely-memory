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

    def test_names_hashes_and_ids_stay(self):
        kept = ["getContextForUser2024Request", "MAX_CONTENT_CHARS_2026_LIMIT",
                "11f6ad8ec52a2984abaafd7c3b516503785c2072",
                "0b55c4d1-5bbb-4fbd-b844-6f2bbb2bf5d1", "claude-opus-5-5-20261015",
                "github-actions-runner-ubuntu-22-04", "/Users/me/Code/shop-api/src/App2.tsx"]
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
        self.assertEqual(hooks.flush("s1", lambda: client), 1)
        content, kw = client.added[0]
        self.assertIn("A coding session in the repository shop-api, with Claude Code", content)
        self.assertIn("Developer: step 4 use [redacted]", content)
        self.assertIn("Assistant: done 4", content)
        self.assertNotIn(key, content)
        self.assertEqual(kw["user_id"], "repo:shop-api")
        self.assertEqual(kw["run_id"], "s1")
        self.assertEqual(kw["metadata"], {"source": "claude-code", "repo": "shop-api"})
        self.assertTrue(kw["timestamp"])
        # Sent once: a second flush has nothing to send.
        self.assertEqual(hooks.flush("s1", lambda: client), 0)

    def test_session_end_sends_the_rest_and_leaves_no_file(self):
        client = FakeClient()
        self.hook("prompt", "claude-code", {"session_id": "s2", "prompt": "rename it"}, client)
        self.hook("stop", "claude-code", {"session_id": "s2", "last_assistant_message": "ok"},
                  client)
        self.hook("session-end", "claude-code", {"session_id": "s2"}, client)
        self.assertEqual(self.background, ["s2"])
        hooks.flush("s2", lambda: client)
        self.assertEqual(len(client.added), 1)
        self.assertEqual(list(hooks._sessions().glob("s2*")), [])

    def test_a_failed_send_waits_for_the_next_start(self):
        down = FakeClient(fail=True)
        self.hook("prompt", "claude-code", {"session_id": "s3", "prompt": "x"}, down)
        self.hook("pre-compact", "claude-code", {"session_id": "s3"}, down)
        self.assertEqual(hooks.flush("s3", lambda: down), 0)
        boxes = list(hooks._sessions().glob("s3.out*.json"))
        self.assertEqual(len(boxes), 1)
        log = (Path(os.environ["KORELY_CONFIG_HOME"]) / "hook.log").read_text()
        self.assertIn("network down", log)
        self.assertNotIn('"x"', log)
        up = FakeClient()
        self.assertEqual(hooks.flush(None, lambda: up), 1)  # what session-start runs
        self.assertIn("Developer: x", up.added[0][0])

    def test_two_senders_never_send_the_same_turns(self):
        client = FakeClient()
        self.hook("prompt", "claude-code", {"session_id": "d1", "prompt": "once"}, client)
        box = hooks._claim(hooks._session_file("d1"))
        os.rename(box, box.with_name(box.name[:-5] + ".sending"))  # the other sender
        self.assertFalse(hooks.send_outbox(box, lambda: client))
        self.assertEqual(client.added, [])

    def test_a_hook_never_raises_and_never_blocks(self):
        out = self.hook("session-start", "claude-code", {"session_id": "s4"},
                        FakeClient(fail=True))
        self.assertEqual(out, "")


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
        hooks.flush("k1", lambda: client)
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
        hooks.flush("t1", lambda: client)
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

    def test_codex_everywhere(self):
        with mock.patch.object(Path, "home", return_value=self.tmp / "home"):
            hooks.install("codex", self.repo, everywhere=True, base="/opt/bin/korely")
            home = self.tmp / "home" / ".codex"
            data = json.loads((home / "hooks.json").read_text())
            self.assertIn("SessionStart", data["hooks"])
            self.assertIn("[mcp_servers.korely]", (home / "config.toml").read_text())


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
