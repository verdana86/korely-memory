"""SDK tests — no network. We replace the one transport seam (Korely._send)
with a recorder that captures the request and returns a canned response, then
assert the SDK builds the right request and parses the right model. Run with:

    cd sdk/python && python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import tempfile
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from korely_memory import (  # noqa: E402
    Korely,
    AuthenticationError,
    NotFoundError,
    StaleWriteError,
    QuotaExceededError,
    APIError,
    KorelyError,
    Memory,
    MemoryPage,
    Context,
)


class _Recorder:
    """Stand-in for Korely._send. Records calls, pops queued responses."""

    def __init__(self):
        self.calls = []
        self._responses = []

    def queue(self, status, body):
        self._responses.append((status, body))
        return self

    def __call__(self, method, path, *, params=None, json_body=None):
        self.calls.append({"method": method, "path": path, "params": params, "json": json_body})
        return self._responses.pop(0) if self._responses else (200, {})

    @property
    def last(self):
        return self.calls[-1]


def _client(rec):
    k = Korely(api_key="kor_live_test")
    k._send = rec
    return k


class TestConstruction(unittest.TestCase):
    def test_region_maps_to_eu_base_url(self):
        self.assertEqual(Korely(api_key="x", region="eu").base_url, "https://api.korely.ai")

    def test_base_url_override(self):
        self.assertEqual(Korely(api_key="x", base_url="http://localhost:8000/").base_url,
                         "http://localhost:8000")

    def test_env_key_picked_up(self):
        os.environ["KORELY_API_KEY"] = "kor_live_env"
        try:
            self.assertEqual(Korely().api_key, "kor_live_env")
        finally:
            del os.environ["KORELY_API_KEY"]

    def test_no_key_raises(self):
        os.environ.pop("KORELY_API_KEY", None)
        with self.assertRaises(KorelyError):
            Korely()


class TestMemories(unittest.TestCase):
    def test_add_builds_request_and_parses_facts(self):
        rec = _Recorder().queue(201, {
            "id": "mem_1", "content": "x", "user_id": "u",
            "facts": [{"subject": "A", "predicate": "likes", "object": "B", "invalidated": ["fct_9"]}],
        })
        m = _client(rec).add("x", user_id="u", agent_id="bot", metadata={"k": 1})
        self.assertEqual(rec.last["method"], "POST")
        self.assertEqual(rec.last["path"], "/v1/memories")
        self.assertEqual(rec.last["json"], {"content": "x", "agent_id": "bot", "user_id": "u", "metadata": {"k": 1}})
        self.assertIsInstance(m, Memory)
        self.assertEqual(m.id, "mem_1")
        self.assertEqual(m.facts[0].object, "B")
        self.assertEqual(m.facts[0].invalidated, ["fct_9"])

    def test_search_returns_hits(self):
        rec = _Recorder().queue(200, {"results": [{"id": "m", "score": 0.91, "snippet": "s"}]})
        hits = _client(rec).search("northwind pricing", user_id="u", limit=5)
        self.assertEqual(rec.last["path"], "/v1/memories/search")
        self.assertEqual(rec.last["json"]["limit"], 5)
        self.assertEqual(hits[0].score, 0.91)
        self.assertEqual(hits[0].snippet, "s")

    def test_get_all_page_is_iterable_with_total(self):
        rec = _Recorder().queue(200, {"memories": [{"id": "m1"}, {"id": "m2"}], "total": 218})
        page = _client(rec).get_all(user_id="u")
        self.assertIsInstance(page, MemoryPage)
        self.assertEqual(page.total, 218)
        self.assertEqual(len(page), 2)
        self.assertEqual([m.id for m in page], ["m1", "m2"])

    def test_get_uses_path_id(self):
        rec = _Recorder().queue(200, {"id": "mem_8f2c1a", "content": "c"})
        m = _client(rec).get("mem_8f2c1a")
        self.assertEqual(rec.last["method"], "GET")
        self.assertEqual(rec.last["path"], "/v1/memories/mem_8f2c1a")
        self.assertEqual(m.content, "c")

    def test_update_sends_expected_updated_at(self):
        rec = _Recorder().queue(200, {"id": "mem_1", "content": "new"})
        _client(rec).update("mem_1", content="new", expected_updated_at="2026-06-07T09:14:00Z")
        self.assertEqual(rec.last["method"], "PATCH")
        self.assertEqual(rec.last["json"], {"content": "new", "expected_updated_at": "2026-06-07T09:14:00Z"})

    def test_delete_receipt(self):
        rec = _Recorder().queue(200, {"id": "mem_1", "status": "forgotten", "facts_invalidated": 1, "audit_id": "aud_3d0f"})
        r = _client(rec).delete("mem_1")
        self.assertEqual(rec.last["method"], "DELETE")
        self.assertEqual(r.status, "forgotten")
        self.assertEqual(r.audit_id, "aud_3d0f")

    def test_delete_all_receipt(self):
        rec = _Recorder().queue(200, {"user_id": "c1", "memories_forgotten": 218, "facts_invalidated": 64, "audit_id": "aud_91xb"})
        r = _client(rec).delete_all(user_id="c1")
        self.assertEqual(rec.last["path"], "/v1/users/c1/memories")
        self.assertEqual(r.memories_forgotten, 218)


class TestFactsContextBatch(unittest.TestCase):
    def test_get_facts_as_of_and_parse(self):
        rec = _Recorder().queue(200, {"facts": [{"object": "40 euro", "invalid_at": "2026-06-07"}], "total": 1})
        facts = _client(rec).get_facts(entity="Northwind Hosting", as_of="2026-06-01")
        self.assertEqual(rec.last["path"], "/v1/facts")
        self.assertEqual(rec.last["params"]["entity"], "Northwind Hosting")
        self.assertEqual(rec.last["params"]["as_of"], "2026-06-01")
        self.assertEqual(facts[0].object, "40 euro")
        self.assertEqual(facts[0].invalid_at, "2026-06-07")

    def test_get_facts_include_invalidated_param(self):
        rec = _Recorder().queue(200, {"facts": [], "total": 0})
        _client(rec).get_facts(entity="X", include_invalidated=True)
        self.assertEqual(rec.last["params"]["include_invalidated"], "true")

    def test_get_context(self):
        rec = _Recorder().queue(200, {"context": "## Known facts…", "tokens": 642, "sources": ["fct_b91e", "mem_8f2c1a"]})
        ctx = _client(rec).get_context(query="plan infra budget", user_id="c1", token_budget=800)
        self.assertEqual(rec.last["path"], "/v1/context")
        self.assertEqual(rec.last["params"]["token_budget"], 800)
        self.assertIsInstance(ctx, Context)
        self.assertEqual(ctx.tokens, 642)
        self.assertEqual(ctx.sources, ["fct_b91e", "mem_8f2c1a"])

    def test_batch_and_status(self):
        rec = _Recorder().queue(202, {"id": "job_1", "status": "processing", "received": 2})
        job = _client(rec).batch([{"content": "a", "user_id": "c1"}, {"content": "b"}])
        self.assertEqual(rec.last["path"], "/v1/batch")
        self.assertEqual(rec.last["json"], {"memories": [{"content": "a", "user_id": "c1"}, {"content": "b"}]})
        self.assertEqual(job.received, 2)

        rec.queue(200, {"id": "job_1", "status": "completed", "received": 2, "imported": 2, "failed": 0})
        js = _client(rec).batch_status("job_1")
        self.assertEqual(rec.last["path"], "/v1/batch/job_1")
        self.assertEqual(js.status, "completed")
        self.assertEqual(js.imported, 2)


class TestErrorMapping(unittest.TestCase):
    def test_401_authentication(self):
        rec = _Recorder().queue(401, {"code": "invalid_key", "message": "bad key"})
        with self.assertRaises(AuthenticationError):
            _client(rec).get("mem_1")

    def test_404_not_found(self):
        rec = _Recorder().queue(404, {"code": "not_found", "message": "Memory not found"})
        with self.assertRaises(NotFoundError):
            _client(rec).get("mem_x")

    def test_409_stale_write(self):
        rec = _Recorder().queue(409, {"code": "stale_write", "message": "stale"})
        with self.assertRaises(StaleWriteError):
            _client(rec).update("mem_1", content="x", expected_updated_at="2000-01-01T00:00:00Z")

    def test_429_quota_with_retry_after(self):
        rec = _Recorder().queue(429, {"code": "quota_exceeded", "message": "slow down", "_retry_after": "12"})
        with self.assertRaises(QuotaExceededError) as cm:
            _client(rec).add("x")
        self.assertEqual(cm.exception.retry_after, 12)

    def test_422_is_generic_api_error(self):
        # non-empty content so the request actually reaches the server (empty
        # content now raises KorelyError client-side — see TestMethodParity).
        rec = _Recorder().queue(422, {"detail": "bad"})
        with self.assertRaises(APIError):
            _client(rec).add("x")


class TestMethodParity(unittest.TestCase):
    """Sprint 18 Method Parity — add(messages), add_fact_triple, get_profile,
    history, users."""

    def test_add_accepts_message_list(self):
        rec = _Recorder().queue(201, {"id": "mem_1", "content": "user: hi\nassistant: yo"})
        _client(rec).add(
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}],
            user_id="u",
        )
        self.assertEqual(rec.last["method"], "POST")
        self.assertEqual(rec.last["path"], "/v1/memories")
        self.assertEqual(rec.last["json"]["content"], "user: hi\nassistant: yo")
        self.assertEqual(rec.last["json"]["user_id"], "u")

    def test_add_string_unchanged(self):
        rec = _Recorder().queue(201, {"id": "mem_1", "content": "plain"})
        _client(rec).add("plain")
        self.assertEqual(rec.last["json"]["content"], "plain")

    def test_add_fact_triple(self):
        rec = _Recorder().queue(201, {
            "id": "fct_1", "subject": "Mario", "predicate": "works_at",
            "object": "Acme", "invalidated": ["fct_old"],
        })
        f = _client(rec).add_fact_triple(
            "Mario", "works_at", "Acme", user_id="mario",
            subject_type="person", valid_from="2026-01-01",
        )
        self.assertEqual(rec.last["method"], "POST")
        self.assertEqual(rec.last["path"], "/v1/facts")
        self.assertEqual(rec.last["json"]["subject"], "Mario")
        self.assertEqual(rec.last["json"]["object"], "Acme")
        self.assertEqual(rec.last["json"]["valid_from"], "2026-01-01")
        self.assertEqual(f.subject, "Mario")
        self.assertEqual(f.invalidated, ["fct_old"])

    def test_get_profile(self):
        rec = _Recorder().queue(200, {
            "user_id": "maria", "as_of": "2026-03-01", "total": 1,
            "facts": [{"id": "fct_1", "subject": "maria", "predicate": "status", "object": "single"}],
            "by_family": {"identity": [{"id": "fct_1", "subject": "maria", "object": "single"}]},
        })
        p = _client(rec).get_profile(user_id="maria", as_of="2026-03-01")
        self.assertEqual(rec.last["method"], "GET")
        self.assertEqual(rec.last["path"], "/v1/profile")
        self.assertEqual(rec.last["params"]["user_id"], "maria")
        self.assertEqual(rec.last["params"]["as_of"], "2026-03-01")
        self.assertEqual(p.user_id, "maria")
        self.assertEqual(p.total, 1)
        self.assertEqual(p.facts[0].object, "single")
        self.assertIn("identity", p.by_family)
        self.assertEqual(p.by_family["identity"][0].subject, "maria")

    def test_history(self):
        rec = _Recorder().queue(200, {"id": "mem_1", "events": [
            {"event": "created", "at": "2026-01-01T00:00:00Z"},
            {"event": "fact_extracted", "at": "2026-01-01T00:00:01Z",
             "fact": "maria likes peaches", "fact_id": "fct_1"},
        ]})
        h = _client(rec).history("mem_1")
        self.assertEqual(rec.last["method"], "GET")
        self.assertEqual(rec.last["path"], "/v1/memories/mem_1/history")
        self.assertEqual(h.id, "mem_1")
        self.assertEqual(len(h.events), 2)
        self.assertEqual(h.events[0].event, "created")
        self.assertEqual(h.events[1].fact_id, "fct_1")

    def test_users(self):
        rec = _Recorder().queue(200, {"users": [
            {"user_id": "maria", "memories": 2, "facts": 1, "last_active": "2026-01-01T00:00:00Z"},
            {"user_id": "mario", "memories": 1, "facts": 0, "last_active": None},
        ], "total": 2})
        us = _client(rec).users(agent_id="bot", limit=10)
        self.assertEqual(rec.last["method"], "GET")
        self.assertEqual(rec.last["path"], "/v1/users")
        self.assertEqual(rec.last["params"]["agent_id"], "bot")
        self.assertEqual(rec.last["params"]["limit"], 10)
        self.assertEqual(us.total, 2)          # pagination total preserved
        self.assertEqual(len(us), 2)           # iterable like a list
        self.assertEqual([u.user_id for u in us], ["maria", "mario"])
        self.assertEqual(us[0].memories, 2)

    def test_add_empty_messages_raises_client_side(self):
        rec = _Recorder()  # no response queued — must raise before sending
        with self.assertRaises(KorelyError):
            _client(rec).add([{"role": "user", "content": "   "}])
        self.assertEqual(rec.calls, [])  # never hit the network

    def test_profile_truncated_flag(self):
        rec = _Recorder().queue(200, {"user_id": "u", "as_of": None, "facts": [],
                                      "by_family": {}, "total": 250, "truncated": True})
        p = _client(rec).get_profile(user_id="u")
        self.assertTrue(p.truncated)
        self.assertEqual(p.total, 250)


class TestKeyResolution(unittest.TestCase):
    """`korely init` saves the key to ~/.korely/config.json and the docs tell
    people to run it first. The SDK must find it, otherwise the documented path
    (init, then import the SDK) dies on "No API key" — which is exactly what a
    new user hits first. Found walking the product from outside, 2026-09-08."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        os.environ["KORELY_CONFIG_HOME"] = self._tmp
        self._saved_env = os.environ.pop("KORELY_API_KEY", None)

    def tearDown(self):
        os.environ.pop("KORELY_CONFIG_HOME", None)
        if self._saved_env is not None:
            os.environ["KORELY_API_KEY"] = self._saved_env
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_config(self, key):
        with open(os.path.join(self._tmp, "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"api_key": key}, fh)

    def test_reads_the_key_saved_by_korely_init(self):
        self._write_config("kor_live_from_config")
        self.assertEqual(Korely().api_key, "kor_live_from_config")

    def test_environment_wins_over_the_config_file(self):
        self._write_config("kor_live_from_config")
        os.environ["KORELY_API_KEY"] = "kor_live_from_env"
        try:
            self.assertEqual(Korely().api_key, "kor_live_from_env")
        finally:
            os.environ.pop("KORELY_API_KEY", None)

    def test_explicit_argument_wins_over_everything(self):
        self._write_config("kor_live_from_config")
        self.assertEqual(Korely(api_key="kor_live_explicit").api_key, "kor_live_explicit")

    def test_missing_key_still_raises_with_a_useful_hint(self):
        with self.assertRaises(KorelyError) as ctx:
            Korely()
        self.assertIn("korely init", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


class TestAsyncClient(unittest.TestCase):
    """An agent in production fans out across users. With only a blocking client
    we become the thing the event loop waits on, which is row 3 of the checklist
    a developer runs us through."""

    def setUp(self):
        os.environ["KORELY_API_KEY"] = "kor_live_async_test"

    def tearDown(self):
        os.environ.pop("KORELY_API_KEY", None)

    def test_mirrors_every_sync_method(self):
        """Parity is the promise, so it is worth a test rather than a comment.
        If someone adds a method to Korely and forgets AsyncKorely, this fails."""
        import inspect
        from korely_memory import AsyncKorely, Korely

        def public(cls):
            return {n for n, _ in inspect.getmembers(cls, inspect.isfunction)
                    if not n.startswith("_")}

        missing = public(Korely) - public(AsyncKorely)
        self.assertEqual(missing, set(), f"AsyncKorely is missing: {sorted(missing)}")

    def test_every_mirrored_method_is_awaitable(self):
        import inspect
        from korely_memory import AsyncKorely

        for name, fn in inspect.getmembers(AsyncKorely, inspect.isfunction):
            if name.startswith("_"):
                continue
            with self.subTest(method=name):
                self.assertTrue(inspect.iscoroutinefunction(fn),
                                f"{name} should be async")

    def test_calls_reach_the_transport_and_run_concurrently(self):
        import asyncio
        from korely_memory import AsyncKorely

        rec = _Recorder()
        for _ in range(3):
            rec.queue(200, {"context": "ctx", "tokens": 3, "sources": []})
        korely = AsyncKorely()
        korely._sync._send = rec

        async def go():
            return await asyncio.gather(*[
                korely.get_context(query="q", user_id=u) for u in ("a", "b", "c")
            ])

        out = asyncio.run(go())
        self.assertEqual(len(out), 3)
        self.assertEqual(len(rec.calls), 3)
        self.assertEqual({c["params"]["user_id"] for c in rec.calls}, {"a", "b", "c"})

    def test_shares_the_key_resolution_of_the_sync_client(self):
        from korely_memory import AsyncKorely
        self.assertEqual(AsyncKorely().api_key, "kor_live_async_test")
        self.assertEqual(AsyncKorely(api_key="kor_live_explicit").api_key, "kor_live_explicit")


class TheSniTrap(unittest.TestCase):
    """The one TLS failure whose own message explains nothing.

    Found by a tester following the install instructions, which suggest
    `<ip>.nip.io` for a machine without a DNS name. The handshake fails with
    TLSV1_ALERT_INTERNAL_ERROR and neither side says why. The client cannot fix
    it, so the least it can do is name it.
    """

    def _hint(self, url, libressl=True):
        import ssl
        from unittest import mock
        from korely_memory.client import _sni_hint
        version = "LibreSSL 2.8.3" if libressl else "OpenSSL 3.5.7 9 Jun 2026"
        with mock.patch.object(ssl, "OPENSSL_VERSION", version):
            return _sni_hint(url, ssl.SSLError("tlsv1 alert internal error"))

    def test_it_offers_the_dashed_name(self):
        self.assertIn('https://2-29-27-64.nip.io', self._hint("https://2.29.27.64.nip.io"))

    def test_it_says_the_server_is_not_at_fault(self):
        self.assertIn("not your server", self._hint("https://2.29.27.64.nip.io"))

    def test_an_ordinary_name_gets_nothing(self):
        self.assertEqual("", self._hint("https://api.korely.ai"))

    def test_a_name_that_only_looks_numeric_gets_nothing(self):
        """999 is not an octet, so this name is not read as an address."""
        self.assertEqual("", self._hint("https://999.1.1.1.nip.io"))

    def test_a_modern_python_gets_nothing(self):
        """It sends the name correctly, so the failure is something else and
        guessing would send the reader down the wrong path."""
        self.assertEqual("", self._hint("https://2.29.27.64.nip.io", libressl=False))

    def test_a_failure_that_is_not_tls_gets_nothing(self):
        from korely_memory.client import _sni_hint
        self.assertEqual("", _sni_hint("https://2.29.27.64.nip.io", OSError("refused")))


class TheServerItTalksTo(unittest.TestCase):
    """Chi installa sulla propria macchina non deve finire sulla nostra.

    `KORELY_BASE_URL` era letto dalla CLI e non dalla classe. Chi installava
    Korely sul proprio server, esportava KORELY_API_KEY e KORELY_BASE_URL e
    scriveva `Korely()` parlava con api.korely.ai. La chiave veniva rifiutata
    con un 401, quindi non si memorizzava niente, ma la memoria era gia'
    partita nel corpo della richiesta.

    Per un prodotto venduto sul fatto che i dati restano sulla tua macchina,
    mandarli altrove in silenzio non e' una scomodita'.
    """

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("KORELY_BASE_URL", "KORELY_API_KEY")}
        os.environ["KORELY_API_KEY"] = "kor_live_test"

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_la_variabile_e_rispettata(self):
        os.environ["KORELY_BASE_URL"] = "https://2-29-27-64.nip.io"
        self.assertEqual(Korely().base_url, "https://2-29-27-64.nip.io")

    def test_senza_variabile_resta_il_servizio_ospitato(self):
        os.environ.pop("KORELY_BASE_URL", None)
        self.assertEqual(Korely().base_url, "https://api.korely.ai")

    def test_l_argomento_esplicito_vince_sulla_variabile(self):
        """Un argomento e' una decisione, una variabile e' un'impostazione."""
        os.environ["KORELY_BASE_URL"] = "https://variabile.example"
        self.assertEqual(
            Korely(base_url="https://esplicito.example").base_url,
            "https://esplicito.example")

    def test_la_barra_finale_non_raddoppia(self):
        os.environ["KORELY_BASE_URL"] = "https://mio.example/"
        self.assertEqual(Korely().base_url, "https://mio.example")
