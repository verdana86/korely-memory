"""`korely init --name`: a person's full name, declared for --user-id
(POST /v1/users/{id}/names, 18 Oct 2026).

The store never merges two names of a person by itself. The name belongs to a
person's memory: in a repository's (repo:<name>) it would read your own
sentences as the repository's, the defect the option exists to get away from.
"""
import os, sys, tempfile, unittest
from io import StringIO
from korely_memory import cli
from korely_memory.exceptions import ConflictError


class _Fake:
    calls = []
    fail = None

    def __init__(self, api_key=None, base_url=None):
        self.api_key = api_key

    def declare_name(self, name, *, user_id, agent_id=None):
        _Fake.calls.append((self.api_key, name, user_id))
        if _Fake.fail:
            raise _Fake.fail
        return type("D", (), {"name": name, "user_id": user_id})()


def _args(**kw):
    base = {"api_key": None, "base_url": None, "json": False, "agent_caller": None,
            "profile": None, "tool": None, "name": None, "user_id": None, "force": False}
    base.update(kw)
    return type("A", (), base)()


class DeclareTheName(unittest.TestCase):
    def setUp(self):
        self.old_env = {k: os.environ.get(k) for k in ("HOME", "KORELY_CONFIG_HOME", "KORELY_API_KEY")}
        os.environ["HOME"] = tempfile.mkdtemp()
        os.environ.pop("KORELY_CONFIG_HOME", None)
        os.environ["KORELY_API_KEY"] = "kor_live_saved"
        self.real_korely, cli.Korely = cli.Korely, _Fake
        self.real_init_key = cli._init_key
        cli._init_key = lambda a: (_ for _ in ()).throw(AssertionError("no signup with --name"))
        _Fake.calls, _Fake.fail = [], None
        self.err, sys.stderr = sys.stderr, StringIO()

    def tearDown(self):
        cli.Korely, cli._init_key = self.real_korely, self.real_init_key
        sys.stderr = self.err
        for k, v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_without_a_user_id_nothing_is_declared(self):
        self.assertEqual(cli.cmd_init(_args(name="Maria Rossi")), 2)
        self.assertIn("--user-id", sys.stderr.getvalue())
        self.assertEqual(_Fake.calls, [])

    def test_with_a_key_already_there_only_the_name_is_declared(self):
        self.assertEqual(cli.cmd_init(_args(name="Maria Rossi", user_id="maria")), 0)
        self.assertEqual(_Fake.calls, [("kor_live_saved", "Maria Rossi", "maria")])

    def test_a_name_declared_for_somebody_else_is_an_error(self):
        _Fake.fail = ConflictError("this name is already declared for somebody else", status=409)
        self.assertEqual(cli.cmd_init(_args(name="Maria Rossi", user_id="maria")), 1)
        self.assertIn("not declared", sys.stderr.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
