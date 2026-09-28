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
        self.old_cfg = os.environ.pop("KORELY_CONFIG_HOME", None)
        os.environ["HOME"] = self.home

    def tearDown(self):
        if self.old is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.old
        if self.old_cfg is not None:
            os.environ["KORELY_CONFIG_HOME"] = self.old_cfg

    def test_it_saves_the_key_and_the_address(self):
        # A self-hosted key with its own address: the case the flag exists for.
        # (This test used a kor_live_ key with a foreign address in 0.1.14 and earlier,
        # a pair the client refuses on every call; init now refuses it too.)
        import urllib.request
        def explode(*a, **k):
            raise AssertionError("signup was called; --api-key must not touch the network")
        real, urllib.request.urlopen = urllib.request.urlopen, explode
        try:
            args = type("A", (), {"api_key": "kor_self_mine", "base_url": "https://mine.example",
                                  "json": False, "agent_caller": None})()
            self.assertEqual(cli.cmd_init(args), 0)
        finally:
            urllib.request.urlopen = real
        cfg = json.loads((pathlib.Path(self.home) / ".korely" / "config.json").read_text())
        self.assertEqual(cfg["api_key"], "kor_self_mine")
        self.assertEqual(cfg["base_url"], "https://mine.example")

if __name__ == "__main__":
    unittest.main(verbosity=2)
