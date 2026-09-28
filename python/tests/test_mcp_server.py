"""korely-mcp stdio server: tool registration, key resolution, the extra's pin.

Tests that need the optional `mcp` extra skip when it is not installed (the
base SDK is zero-dep, so the default test env has no `mcp`). The full agentic
path (a real MCP client spawning the server and calling tools) is exercised
separately against a live backend.
"""
import importlib
import os
import re
import sys
import types
import unittest
from unittest import mock

try:
    import mcp  # noqa: F401
    HAS_MCP = True
except Exception:
    HAS_MCP = False

_PYPROJECT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "pyproject.toml")


@unittest.skipUnless(HAS_MCP, "mcp extra not installed (pip install korely-memory[mcp])")
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


@unittest.skipUnless(HAS_MCP, "mcp extra not installed (pip install korely-memory[mcp])")
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


class TheExtraInstallsAWorkingServer(unittest.TestCase):
    """mcp 2.0 removed `mcp.server.fastmcp`. The extra said `mcp>=1.2.0` with
    no upper bound, so `pip install 'korely-memory[mcp]'` resolved mcp 2.x and
    korely-mcp exited telling the user to install the extra they had just
    installed. Measured 2026-09-28 in a clean python:3.11 container with
    mcp 2.2.0."""

    def test_the_extra_stays_on_mcp_1(self):
        with open(_PYPROJECT, encoding="utf-8") as fh:
            text = fh.read()
        m = re.search(r'^mcp\s*=\s*\[\s*"([^"]+)"', text, re.M)
        self.assertIsNotNone(m, "no mcp extra in pyproject")
        self.assertIn("<2", m.group(1).replace(" ", ""))

    def _import_with(self, modules):
        # patch.dict restores the whole of sys.modules on exit, so the real mcp
        # (when installed) and its submodules come back untouched.
        with mock.patch.dict(sys.modules, modules):
            for k in [k for k in sys.modules if k.startswith("mcp.") and k not in modules]:
                del sys.modules[k]
            sys.modules.pop("korely_memory.mcp_server", None)
            with self.assertRaises(SystemExit) as caught:
                importlib.import_module("korely_memory.mcp_server")
        return str(caught.exception.code)

    def test_mcp_2_is_named_as_the_problem(self):
        fake_mcp = types.ModuleType("mcp")
        fake_mcp.__path__ = []
        fake_server = types.ModuleType("mcp.server")
        fake_server.__path__ = []
        msg = self._import_with({"mcp": fake_mcp, "mcp.server": fake_server,
                                 "mcp.server.fastmcp": None})
        self.assertIn("mcp>=1.2.0,<2", msg)
        self.assertNotIn("needs the MCP extra", msg)

    def test_a_missing_mcp_asks_for_the_extra(self):
        msg = self._import_with({"mcp": None})
        self.assertIn("korely-memory[mcp]", msg)


if __name__ == "__main__":
    unittest.main()
