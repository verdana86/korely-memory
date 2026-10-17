"""Under unittest, which RELEASING.md runs (`unittest discover -s tests`),
conftest.py is not read: this module, imported first because discovery
imports the test files in name order, gives the whole run an empty config
directory instead of the machine's ~/.korely. The release of 0.1.25 found
"no key raises" red on a Mac with a saved key (18 Oct 2026); conftest.py
does the same per test under pytest."""
import os
import tempfile
import unittest

os.environ["KORELY_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ.pop("KORELY_API_KEY", None)
os.environ.pop("KORELY_PROFILE", None)


class TestIsolation(unittest.TestCase):
    def test_the_machines_config_is_never_read(self):
        home = os.environ["KORELY_CONFIG_HOME"]
        self.assertTrue(home.startswith(tempfile.gettempdir()))
        self.assertNotIn("KORELY_API_KEY", os.environ)
