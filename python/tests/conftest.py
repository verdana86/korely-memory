"""Every test reads its own empty config directory, never the machine's
~/.korely/config.json: a developer with a saved key turned "no key raises"
red (17 Oct 2026). A test that wants a config writes it there."""
import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _own_config_home(monkeypatch):
    home = tempfile.mkdtemp()
    monkeypatch.setenv("KORELY_CONFIG_HOME", home)
    monkeypatch.delenv("KORELY_API_KEY", raising=False)
    monkeypatch.delenv("KORELY_PROFILE", raising=False)
    yield home
