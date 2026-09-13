import json, os, pathlib, sys, tempfile, unittest
from korely_memory import cli

class SaveAnExistingKey(unittest.TestCase):
    """`korely init --api-key` writes the key and never calls the network.

    The self-hosted README documented this as one of the three ways to point the
    client at your own server. It did not exist: the command was a signup, the
    flag was unrecognized, and somebody following the README got an argparse
    error on a page that explains why the step matters.
    """
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.old = os.environ.get("HOME")
        os.environ["HOME"] = self.home

    def tearDown(self):
        if self.old: os.environ["HOME"] = self.old

    def test_it_saves_the_key_and_the_address(self):
        import urllib.request
        def explode(*a, **k):
            raise AssertionError("signup was called; --api-key must not touch the network")
        real, urllib.request.urlopen = urllib.request.urlopen, explode
        try:
            args = type("A", (), {"api_key": "kor_live_mine", "base_url": "https://mine.example",
                                  "json": False, "agent_caller": None})()
            self.assertEqual(cli.cmd_init(args), 0)
        finally:
            urllib.request.urlopen = real
        cfg = json.loads((pathlib.Path(self.home) / ".korely" / "config.json").read_text())
        self.assertEqual(cfg["api_key"], "kor_live_mine")
        self.assertEqual(cfg["base_url"], "https://mine.example")

if __name__ == "__main__":
    unittest.main(verbosity=2)
