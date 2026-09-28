"""korely-mcp stdio server: tool registration, key resolution, the protocol.

Since 0.1.16 the server needs no dependency (the MCP stdio protocol is in
korely_memory/_mcp_stdio.py), so every test runs, on Python 3.9 too. The full
agentic path against a live backend is exercised separately.
"""
import importlib
import os
import re
import sys
import types
import unittest
from unittest import mock

_PYPROJECT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "pyproject.toml")


class TestKorelyMcpServer(unittest.TestCase):
    def test_four_tools_registered(self):
        from korely_memory import mcp_server as m
        tools = {n for n in dir(m) if n.startswith("korely_")}
        self.assertEqual(
            tools,
            {"korely_add", "korely_search", "korely_get_context", "korely_get_facts"},
        )

    def test_client_requires_a_key(self):
        from korely_memory import mcp_server as m
        from korely_memory.exceptions import KorelyError

        os.environ.pop("KORELY_API_KEY", None)
        os.environ["KORELY_CONFIG_HOME"] = "/nonexistent-korely-config-dir"
        try:
            with self.assertRaises(KorelyError):
                m._client()
        finally:
            os.environ.pop("KORELY_CONFIG_HOME", None)


class TheToolsSayTheRightTense(unittest.TestCase):
    """The CLI stopped printing `superseded 3026-01-01` for a fact that ends in
    the future (52e29b7); the MCP server kept its own copy of the line and the
    defect with it."""

    def _fact(self, invalid_at=None, tense=None):
        from korely_memory import Fact

        return Fact(subject="u1", predicate="is_on", object="Gold",
                    valid_from="2026-03-15T00:00:00+00:00",
                    invalid_at=invalid_at, tense=tense)

    def test_a_future_end_is_until(self):
        from korely_memory.mcp_server import _fact_line

        line = _fact_line(self._fact("3026-01-01T00:00:00+00:00"))
        self.assertIn("until 3026-01-01", line)
        self.assertNotIn("superseded", line)

    def test_a_past_end_is_superseded(self):
        from korely_memory.mcp_server import _fact_line

        self.assertIn("superseded 2026-06-20",
                      _fact_line(self._fact("2026-06-20T00:00:00+00:00")))

    def test_a_past_tense_fact_reads_as_history(self):
        from korely_memory.mcp_server import _fact_line

        line = _fact_line(self._fact("2026-06-20T00:00:00+00:00", tense="past"))
        self.assertIn("past, ended by 2026-06-20", line)

    def test_a_planned_fact_is_not_presented_as_done(self):
        from korely_memory.mcp_server import _fact_line

        self.assertIn("planned, not done", _fact_line(self._fact(tense="planned")))


class TheSameToolsAsTheHostedServer(unittest.TestCase):
    """The stdio server promises the four tools of the hosted /agent/mcp. On
    2026-09-28 the hosted `korely_add` gained `timestamp`, and its fact line
    (memoria_core `riga_mcp`) gained `[until DATE]`, UTC days, the current
    name of an entity and how often a fact was confirmed."""

    def _fact(self, **kw):
        from korely_memory import Fact

        base = dict(subject="u1", predicate="is_on", object="Gold",
                    valid_from="2026-03-15T00:00:00+00:00")
        base.update(kw)
        return Fact(**base)

    def test_korely_add_declares_timestamp(self):
        from korely_memory import mcp_server as m

        listed = m.mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        tools = {t["name"]: t for t in listed}
        props = tools["korely_add"]["inputSchema"]["properties"]
        self.assertIn("timestamp", props)
        self.assertNotIn("timestamp", tools["korely_add"]["inputSchema"].get("required", []))

    def test_korely_add_sends_the_timestamp(self):
        from korely_memory import Korely
        from korely_memory import mcp_server as m

        sent = []

        def send(method, path, *, params=None, json_body=None):
            sent.append(json_body)
            return 201, {"id": "mem_1", "content": "x", "facts": []}

        k = Korely(api_key="kor_live_mcp_test")
        k._send = send
        with mock.patch.object(m, "_client", lambda: k):
            out = m.korely_add("Franco signed up on Pro.", user_id="franco",
                               timestamp="2026-01-15")
            m.korely_add("no date")
        self.assertIn("Stored mem_1", out)
        self.assertEqual(sent[0]["timestamp"], "2026-01-15")
        self.assertNotIn("timestamp", sent[1])

    def test_a_planned_fact_with_an_end_to_come_says_both(self):
        from korely_memory.mcp_server import _fact_line

        line = _fact_line(self._fact(invalid_at="3026-01-01T00:00:00+00:00", tense="planned"))
        self.assertIn("[until 3026-01-01]", line)
        self.assertIn("[planned, not done]", line)
        self.assertNotIn("superseded", line)

    def test_dates_are_utc_days(self):
        from korely_memory.mcp_server import _fact_line

        line = _fact_line(self._fact(valid_from="2026-03-15T02:00:00+05:00",
                                     invalid_at="2026-06-01T10:00:00+14:00"))
        self.assertIn("(since 2026-03-14)", line)
        self.assertIn("[superseded 2026-05-31]", line)

    def test_the_current_name_and_the_confirmations(self):
        from korely_memory.mcp_server import _fact_line

        line = _fact_line(self._fact(subject="Acme", subject_canonical="Globex",
                                     object="Gold", object_canonical="gold",
                                     last_confirmed_at="2026-09-01T08:00:00+00:00",
                                     observation_count=3))
        self.assertTrue(line.startswith("Globex (then: Acme) · is_on · Gold"), line)
        self.assertIn("(since 2026-03-15, confirmed 2026-09-01, seen 3x)", line)

    def test_the_line_matches_the_hosted_one(self):
        """The same fact, the same words: a fixture of what memoria_core
        `riga_mcp` prints for it on the hosted server."""
        from korely_memory.mcp_server import _fact_line

        cases = [
            (dict(), "u1 · is_on · Gold  (since 2026-03-15)"),
            (dict(invalid_at="2026-06-20T00:00:00+00:00"),
             "u1 · is_on · Gold  (since 2026-03-15)  [superseded 2026-06-20]"),
            (dict(invalid_at="2026-06-20T00:00:00+00:00", tense="past"),
             "u1 · is_on · Gold  (since 2026-03-15)  [past, ended by 2026-06-20]"),
            (dict(invalid_at="3026-01-01T00:00:00+00:00", tense="past"),
             "u1 · is_on · Gold  (since 2026-03-15)  [until 3026-01-01]"),
            (dict(tense="planned"),
             "u1 · is_on · Gold  (since 2026-03-15)  [planned, not done]"),
            (dict(last_confirmed_at="2026-03-15T18:00:00+00:00", observation_count=1),
             "u1 · is_on · Gold  (since 2026-03-15)"),
        ]
        for kw, expected in cases:
            with self.subTest(**{k: str(v) for k, v in kw.items()}):
                self.assertEqual(_fact_line(self._fact(**kw)), expected)


class TheServerNeedsNothing(unittest.TestCase):
    """Until 0.1.15 korely-mcp imported `mcp.server.fastmcp`: the `mcp` package
    needs Python 3.10, so it could not start on the Python 3.9 macOS ships
    (blind installs of korely-agent 0.1.13, 2026-09-28), and mcp 2.0 removed
    the module. Now the protocol is ours, standard library only."""

    def test_the_extra_is_empty(self):
        with open(_PYPROJECT, encoding="utf-8") as fh:
            text = fh.read()
        self.assertRegex(text, r"(?m)^mcp\s*=\s*\[\s*\]", "the mcp extra must stay, empty")

    def test_it_imports_without_the_mcp_package(self):
        with mock.patch.dict(sys.modules, {"mcp": None}):
            sys.modules.pop("korely_memory.mcp_server", None)
            m = importlib.import_module("korely_memory.mcp_server")
        self.assertTrue(hasattr(m, "korely_add"))


class TheProtocol(unittest.TestCase):
    """MCP 2025-06-18: lifecycle, stdio transport, tools."""

    def setUp(self):
        from korely_memory import mcp_server
        self.m = mcp_server

    def _call(self, method, params=None, mid=1):
        msg = {"jsonrpc": "2.0", "id": mid, "method": method}
        if params is not None:
            msg["params"] = params
        return self.m.mcp.handle(msg)

    def test_initialize_answers_the_version_the_client_asked_for(self):
        r = self._call("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                      "clientInfo": {"name": "t", "version": "1"}})["result"]
        self.assertEqual(r["protocolVersion"], "2025-03-26")
        self.assertIn("tools", r["capabilities"])
        self.assertEqual(r["serverInfo"]["name"], "korely")

    def test_an_unknown_version_gets_the_latest(self):
        r = self._call("initialize", {"protocolVersion": "1999-01-01"})["result"]
        self.assertEqual(r["protocolVersion"], "2025-06-18")

    def test_notifications_get_no_answer_and_ping_does(self):
        self.assertIsNone(self.m.mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(self._call("ping")["result"], {})

    def test_the_four_tools_with_their_schemas(self):
        tools = {t["name"]: t for t in self._call("tools/list")["result"]["tools"]}
        self.assertEqual(set(tools), {"korely_add", "korely_search", "korely_get_context", "korely_get_facts"})
        facts = tools["korely_get_facts"]["inputSchema"]
        self.assertEqual(facts["properties"]["include_invalidated"]["type"], "boolean")
        self.assertEqual(facts["properties"]["limit"], {"type": "integer", "default": 50})
        self.assertEqual(facts["properties"]["as_of"]["type"], ["string", "null"])
        self.assertEqual(tools["korely_search"]["inputSchema"]["required"], ["query"])
        self.assertTrue(tools["korely_get_context"]["description"])

    def test_a_call_answers_text(self):
        with mock.patch.object(self.m, "korely_search", lambda **kw: "hits"), \
                mock.patch.dict(self.m.mcp._tools, {"korely_search": lambda query, user_id=None, limit=15: "hits"}):
            r = self._call("tools/call", {"name": "korely_search", "arguments": {"query": "tea"}})["result"]
        self.assertEqual(r, {"content": [{"type": "text", "text": "hits"}], "isError": False})

    def test_a_tool_that_raises_is_a_tool_error_not_a_protocol_error(self):
        def boom(query):
            raise RuntimeError("down")
        with mock.patch.dict(self.m.mcp._tools, {"korely_search": boom}):
            r = self._call("tools/call", {"name": "korely_search", "arguments": {"query": "x"}})["result"]
        self.assertTrue(r["isError"])
        self.assertIn("down", r["content"][0]["text"])

    def test_unknown_tool_bad_arguments_unknown_method(self):
        self.assertEqual(self._call("tools/call", {"name": "nope"})["error"]["code"], -32602)
        bad = self._call("tools/call", {"name": "korely_search", "arguments": {"nope": 1}})
        self.assertEqual(bad["error"]["code"], -32602)
        self.assertEqual(self._call("resources/list")["error"]["code"], -32601)

    def test_a_real_process_over_stdio(self):
        """The server as a client starts it: one JSON message per line, with
        this interpreter (the Python 3.9 of macOS when the tests run on it)."""
        import json
        import subprocess

        lines = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        out = subprocess.run(
            [sys.executable, "-m", "korely_memory.mcp_server"],
            input="\n".join(json.dumps(x) for x in lines) + "\n",
            capture_output=True, text=True, timeout=30, env=env,
        )
        answers = [json.loads(line) for line in out.stdout.splitlines() if line.strip()]
        self.assertEqual([a["id"] for a in answers], [1, 2], out.stderr)
        self.assertEqual(len(answers[1]["result"]["tools"]), 4)


if __name__ == "__main__":
    unittest.main()
