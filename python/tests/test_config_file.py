"""~/.korely/config.json holds the keys. It is written so that nobody but its
owner reads it, not even for an instant (0600 from the first byte, a folder
made 0700), whole or not at all (a temporary file renamed over it), never
through a link at its place; and a file korely cannot read is never replaced
by a new one that has lost its keys and profiles: the command stops first,
before anything is done on the server.
"""
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from korely_memory import Korely, _files, cli  # noqa: E402

_POSIX = os.name == "posix"


def _mode(path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


class _Home(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.home = self.root / "korely"  # not there yet: the first save makes it
        env = mock.patch.dict(os.environ, {"KORELY_CONFIG_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        for k in ("KORELY_API_KEY", "KORELY_PROFILE", "KORELY_BASE_URL"):
            os.environ.pop(k, None)
        old = os.umask(0o022)  # the usual one, under which the file was born 0644
        self.addCleanup(os.umask, old)
        self.path = self.home / "config.json"

    def cli(self, argv, rec=None):
        out, err = io.StringIO(), io.StringIO()
        send = rec or (lambda *a, **k: (_ for _ in ()).throw(AssertionError("nothing may be sent")))
        with mock.patch.object(Korely, "_send", send), redirect_stdout(out), \
                redirect_stderr(err):
            rc = cli.main(argv)
        return rc, out.getvalue(), err.getvalue()


@unittest.skipUnless(_POSIX, "file modes are POSIX")
class TheFileIsTheOwnersAlone(_Home):
    def test_a_new_file_is_0600_before_it_holds_the_key_under_its_name(self):
        seen = []
        real = os.replace

        def replace(src, dst):
            seen.append((_mode(src), Path(src).read_text(encoding="utf-8")))
            return real(src, dst)

        with mock.patch.object(_files.os, "replace", replace):
            rc, _, _ = self.cli(["init", "--api-key", "kor_self_mine", "--base-url",
                                 "https://mine.example"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], 0o600)
        self.assertIn("kor_self_mine", seen[0][1])
        self.assertEqual(_mode(self.path), 0o600)
        self.assertEqual(_mode(self.home), 0o700)
        self.assertEqual(sorted(os.listdir(self.home)), ["config.json"])  # no temporary left

    def test_an_existing_file_becomes_0600(self):
        self.home.mkdir()
        self.path.write_text(json.dumps({"api_key": "kor_live_saved"}), encoding="utf-8")
        os.chmod(self.path, 0o644)
        with mock.patch.object(Korely, "init_agent", side_effect=AssertionError("no signup")):
            self.assertEqual(self.cli(["init", "--user-id", "maria"])[0], 0)
        self.assertEqual(_mode(self.path), 0o600)
        self.assertEqual(json.loads(self.path.read_text())["user_id"], "maria")

    def test_a_link_at_its_place_is_replaced_not_followed(self):
        self.home.mkdir()
        elsewhere = self.root / "elsewhere.json"
        elsewhere.write_text(json.dumps({"api_key": "kor_live_saved"}), encoding="utf-8")
        os.chmod(elsewhere, 0o644)
        os.symlink(elsewhere, self.path)
        with mock.patch.object(Korely, "init_agent", side_effect=AssertionError("no signup")):
            self.assertEqual(self.cli(["init", "--user-id", "maria"])[0], 0)
        self.assertFalse(self.path.is_symlink())
        self.assertEqual(_mode(self.path), 0o600)
        self.assertEqual(json.loads(elsewhere.read_text()), {"api_key": "kor_live_saved"})
        self.assertEqual(_mode(elsewhere), 0o644)

    def test_a_failed_write_leaves_the_old_file_whole(self):
        self.home.mkdir()
        before = json.dumps({"api_key": "kor_live_saved"})
        self.path.write_text(before, encoding="utf-8")
        with mock.patch.object(_files.os, "replace", side_effect=OSError("disk full")), \
                self.assertRaises(OSError):
            _files.write_private(self.path, "{}")
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual(sorted(os.listdir(self.home)), ["config.json"])


class AFileKorelyCannotRead(_Home):
    BROKEN = '{"api_key": "kor_live_0123456789abcdef", "profiles": {"work": {"api_k'

    def setUp(self):
        super().setUp()
        self.home.mkdir()
        self.path.write_text(self.BROKEN, encoding="utf-8")

    def assertRefusedAndKept(self, rc, err):
        self.assertNotEqual(rc, 0)
        self.assertIn("cannot be read", err)
        self.assertIn(str(self.path), err)
        self.assertNotIn("kor_live_0123456789abcdef", err)  # the reason never quotes the file
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.BROKEN)

    def test_init_refuses_and_leaves_it(self):
        with mock.patch.object(Korely, "init_agent", side_effect=AssertionError("no signup")):
            with mock.patch.dict(os.environ, {"KORELY_API_KEY": "kor_live_env"}):
                rc, _, err = self.cli(["init", "--user-id", "maria"])
            self.assertRefusedAndKept(rc, err)
            rc, _, err = self.cli(["init", "--api-key", "kor_live_new"])
            self.assertRefusedAndKept(rc, err)
            rc, _, err = self.cli(["init"])
            self.assertRefusedAndKept(rc, err)

    def test_a_json_that_is_not_an_object_is_refused_too(self):
        self.path.write_text("[1, 2]", encoding="utf-8")
        self.BROKEN = "[1, 2]"
        with mock.patch.object(Korely, "init_agent", side_effect=AssertionError("no signup")):
            rc, _, err = self.cli(["init", "--api-key", "kor_live_new"])
        self.assertRefusedAndKept(rc, err)

    def test_nothing_is_done_on_the_server_first(self):
        """A key made and then not saved would be shown nowhere; an account
        deleted and then not forgotten would leave a dead key behind."""
        for argv in (["keys", "create", "--name", "ci", "--save-as", "ci"],
                     ["delete-account", "--yes"]):
            with self.subTest(cmd=argv[0]):
                rc, _, err = self.cli(argv + ["--api-key", "kor_live_admin"])
                self.assertRefusedAndKept(rc, err)

    def test_an_empty_file_is_an_empty_config(self):
        self.path.write_text("", encoding="utf-8")
        rc, _, _ = self.cli(["init", "--api-key", "kor_self_mine", "--base-url",
                             "https://mine.example"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(self.path.read_text())["api_key"], "kor_self_mine")


if __name__ == "__main__":
    unittest.main(verbosity=2)
