"""`korely projects` and `korely keys`: the dashboard's administration from
the terminal (the founder, 17 Oct 2026: what the dashboard does, the API and
the terminal do too). An admin key; the server's own answers."""
import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

from korely_memory import cli
from tests.test_cli import _CleanEnv, _Recorder, _args, _client

PROJECTS = {"projects": [
    {"id": "p-default", "name": "Default", "is_default": True, "region": "eu", "archived_at": None},
    {"id": "p-tools", "name": "Founder tools", "is_default": False, "region": "eu", "archived_at": None},
]}


def _run(fn, rec, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(_client(rec), _args(argv))
    return rc, out.getvalue(), err.getvalue()


class TestProjects(unittest.TestCase):
    def test_list(self):
        rec = _Recorder().queue(200, PROJECTS)
        rc, out, _ = _run(cli.cmd_projects, rec, ["projects"])
        self.assertEqual(rc, 0)
        self.assertIn("Founder tools", out)
        self.assertIn("default", out)
        self.assertEqual(rec.last["path"], "/admin/projects")

    def test_rename_finds_the_project_by_name(self):
        rec = _Recorder().queue(200, PROJECTS).queue(200, {"id": "p-default", "name": "Hermes"})
        rc, out, _ = _run(cli.cmd_projects, rec, ["projects", "rename", "default", "Hermes"])
        self.assertEqual(rc, 0)
        self.assertEqual((rec.last["method"], rec.last["path"], rec.last["json"]),
                         ("PATCH", "/admin/projects/p-default", {"name": "Hermes"}))

    def test_create_with_a_region(self):
        rec = _Recorder().queue(201, {"id": "p-new", "name": "Lumen"})
        _run(cli.cmd_projects, rec, ["projects", "create", "Lumen", "--region", "global"])
        self.assertEqual(rec.last["json"], {"name": "Lumen", "region": "global"})

    def test_delete_needs_yes(self):
        rec = _Recorder().queue(200, PROJECTS)
        rc, _, err = _run(cli.cmd_projects, rec, ["projects", "delete", "Founder tools"])
        self.assertEqual(rc, 2)
        self.assertIn("--yes", err)
        self.assertEqual([c["method"] for c in rec.calls], ["GET"])   # nothing deleted

    def test_an_unknown_name_is_an_error(self):
        from korely_memory.exceptions import KorelyError

        rec = _Recorder().queue(200, PROJECTS)
        with self.assertRaises(KorelyError):
            _run(cli.cmd_projects, rec, ["projects", "archive", "Nope"])


class TestKeys(_CleanEnv):
    def test_create_a_confined_key(self):
        rec = _Recorder().queue(200, PROJECTS).queue(201, {
            "id": "k1", "name": "hermes", "masked": "kor_live_••••abcd", "key": "kor_live_SECRET",
            "users": ["founder", "repo:*"]})
        rc, out, _ = _run(cli.cmd_keys, rec, ["keys", "create", "--name", "hermes",
                                              "--project", "Founder tools", "--scopes", "read,write",
                                              "--users", "founder, repo:*", "--expires", "never"])
        self.assertEqual(rc, 0)
        self.assertEqual(rec.last["json"], {"name": "hermes", "project_id": "p-tools",
                                            "scopes": ["memories:read", "memories:write"],
                                            "users": ["founder", "repo:*"], "expires_in_days": None})
        self.assertIn("kor_live_SECRET", out)

    def test_save_as_keeps_the_secret_off_the_screen(self):
        rec = _Recorder().queue(201, {"id": "k2", "name": "terminale", "masked": "kor_live_••••wxyz",
                                      "key": "kor_live_SECRET2"})
        rc, out, _ = _run(cli.cmd_keys, rec, ["keys", "create", "--name", "terminale",
                                              "--scopes", "admin", "--save-as", "admin"])
        self.assertEqual(rc, 0)
        self.assertNotIn("SECRET2", out)
        cfg = json.loads(open(os.path.join(self.cfg_dir, "config.json")).read())
        self.assertEqual(cfg["profiles"]["admin"]["api_key"], "kor_live_SECRET2")

    def test_revoke_needs_yes(self):
        rec = _Recorder()
        rc, _, err = _run(cli.cmd_keys, rec, ["keys", "revoke", "k1"])
        self.assertEqual(rc, 2)
        self.assertEqual(rec.calls, [])

    def test_list_shows_the_confinement(self):
        rec = _Recorder().queue(200, [{"id": "k1", "masked": "kor_live_••••abcd", "name": "hermes",
                                       "state": "live", "scopes": ["memories:read"],
                                       "project_name": "Founder tools", "users": ["founder", "repo:*"]}])
        rc, out, _ = _run(cli.cmd_keys, rec, ["keys"])
        self.assertIn("only founder,repo:*", out)


class TestAnEmptyKeyIsNotASignup(_CleanEnv):
    """17 Oct 2026, the founder's test: `korely init --api-key "$K"` with K
    empty minted a second account with no login instead of refusing."""

    def test_an_empty_key_refuses_and_signs_nobody_up(self):
        from unittest import mock

        for argv in (["init", "--api-key", ""], ["init", "--api-key", "   "],
                     ["init", "--tool", "claude-code", "--api-key", ""]):
            with mock.patch.object(cli.Korely, "init_agent") as signup:
                err = io.StringIO()
                with redirect_stderr(err), redirect_stdout(io.StringIO()):
                    rc = cli.main(argv)
            self.assertEqual(rc, 2)
            self.assertIn("the key is empty", err.getvalue())
            signup.assert_not_called()
            self.assertFalse(os.path.exists(os.path.join(self.cfg_dir, "config.json")))
