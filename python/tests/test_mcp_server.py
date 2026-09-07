"""korely-mcp stdio server — tool registration + key resolution.

Skips when the optional `mcp` extra isn't installed (the base SDK is zero-dep,
so the default test env has no `mcp`). The full agentic path (a real MCP client
spawning the server and calling tools) is exercised separately against a live
backend.
"""
import os
import unittest

try:
    import mcp  # noqa: F401
    HAS_MCP = True
except Exception:
    HAS_MCP = False


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


if __name__ == "__main__":
    unittest.main()
