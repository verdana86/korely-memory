"""SDK tests, no network. We replace the one transport seam (Korely._send)
with a recorder that captures the request and returns a canned response, then
assert the SDK builds the right request and parses the right model. Run with:

    cd python && python3 -m unittest discover -s tests -v
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
from korely_memory.exceptions import NamespaceForbiddenError  # noqa: E402,F401


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
        # content now raises KorelyError client-side, see TestMethodParity).
        rec = _Recorder().queue(422, {"detail": "bad"})
        with self.assertRaises(APIError):
            _client(rec).add("x")


class TestMethodParity(unittest.TestCase):
    """Sprint 18 Method Parity: add(messages), add_fact_triple, get_profile,
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
        rec = _Recorder()  # no response queued: must raise before sending
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
    (init, then import the SDK) dies on "No API key", which is exactly what a
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
        self.assertIn('https://203-0-113-64.nip.io', self._hint("https://203.0.113.64.nip.io"))

    def test_it_says_the_server_is_not_at_fault(self):
        self.assertIn("not your server", self._hint("https://203.0.113.64.nip.io"))

    def test_an_ordinary_name_gets_nothing(self):
        self.assertEqual("", self._hint("https://api.korely.ai"))

    def test_a_name_that_only_looks_numeric_gets_nothing(self):
        """999 is not an octet, so this name is not read as an address."""
        self.assertEqual("", self._hint("https://999.1.1.1.nip.io"))

    def test_a_modern_python_gets_nothing(self):
        """It sends the name correctly, so the failure is something else and
        guessing would send the reader down the wrong path."""
        self.assertEqual("", self._hint("https://203.0.113.64.nip.io", libressl=False))

    def test_a_failure_that_is_not_tls_gets_nothing(self):
        from korely_memory.client import _sni_hint
        self.assertEqual("", _sni_hint("https://203.0.113.64.nip.io", OSError("refused")))


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
        os.environ["KORELY_API_KEY"] = "kor_self_test"

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_la_variabile_e_rispettata(self):
        os.environ["KORELY_BASE_URL"] = "https://203-0-113-64.nip.io"
        self.assertEqual(Korely().base_url, "https://203-0-113-64.nip.io")

    def test_senza_variabile_resta_il_servizio_ospitato(self):
        """Con una chiave del servizio ospitato, si intende: una `kor_self_`
        senza indirizzo adesso viene rifiutata prima di partire, ed e' il punto
        della riparazione."""
        os.environ.pop("KORELY_BASE_URL", None)
        os.environ["KORELY_API_KEY"] = "kor_live_test"
        self.assertEqual(Korely().base_url, "https://api.korely.ai")

    def test_una_chiave_di_casa_senza_indirizzo_viene_fermata(self):
        """Il caso esatto che ha fatto partire una memoria verso di noi: chiave
        della propria installazione, nessun indirizzo, e il default che vince.
        Adesso non parte niente."""
        from korely_memory.exceptions import KorelyError

        os.environ.pop("KORELY_BASE_URL", None)
        os.environ["KORELY_API_KEY"] = "kor_self_test"
        with self.assertRaises(KorelyError) as caught:
            Korely()
        self.assertIn("kor_self_", str(caught.exception))
        self.assertIn("base_url", str(caught.exception))

    def test_una_chiave_ospitata_su_una_macchina_altrui_viene_fermata(self):
        """Lo specchio: una credenziale che abbiamo emesso noi non si consegna
        alla macchina di qualcun altro."""
        from korely_memory.exceptions import KorelyError

        os.environ["KORELY_API_KEY"] = "kor_live_test"
        with self.assertRaises(KorelyError):
            Korely(base_url="https://non-e-nostra.example")

    def test_l_argomento_esplicito_vince_sulla_variabile(self):
        """Un argomento e' una decisione, una variabile e' un'impostazione."""
        os.environ["KORELY_BASE_URL"] = "https://variabile.example"
        self.assertEqual(
            Korely(base_url="https://esplicito.example").base_url,
            "https://esplicito.example")

    def test_la_barra_finale_non_raddoppia(self):
        os.environ["KORELY_BASE_URL"] = "https://mio.example/"
        self.assertEqual(Korely().base_url, "https://mio.example")


class IlPercorsoDocumentato(unittest.TestCase):
    """`korely init --base-url ...`, poi `from korely_memory import Korely`.

    La CLI scrive nel file di configurazione sia la chiave sia l'indirizzo, e
    li rilegge entrambi. La classe rileggeva solo la chiave, quindi il percorso
    che la documentazione chiama documentato prendeva una chiave self-hosted e
    la mandava ad api.korely.ai. Stesso difetto della variabile d'ambiente,
    nell'ultimo posto dove restava.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._saved = {k: os.environ.get(k) for k in
                       ("KORELY_CONFIG_HOME", "KORELY_BASE_URL", "KORELY_API_KEY")}
        os.environ["KORELY_CONFIG_HOME"] = self.dir
        os.environ.pop("KORELY_BASE_URL", None)
        os.environ.pop("KORELY_API_KEY", None)
        with open(os.path.join(self.dir, "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"api_key": "kor_self_dal_file",
                       "base_url": "https://il-mio-server.example"}, fh)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_prende_dal_file_sia_la_chiave_sia_l_indirizzo(self):
        k = Korely()
        self.assertEqual(k.api_key, "kor_self_dal_file")
        self.assertEqual(k.base_url, "https://il-mio-server.example")

    def test_la_variabile_vince_sul_file(self):
        os.environ["KORELY_BASE_URL"] = "https://dalla-variabile.example"
        self.assertEqual(Korely().base_url, "https://dalla-variabile.example")

    def test_un_file_senza_indirizzo_non_rompe_niente(self):
        """I file scritti da una `korely init` piu' vecchia hanno solo la
        chiave. Devono continuare a funzionare, sul servizio ospitato."""
        with open(os.path.join(self.dir, "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"api_key": "kor_live_vecchio"}, fh)
        self.assertEqual(Korely().base_url, "https://api.korely.ai")

    def test_un_file_illeggibile_non_rompe_niente(self):
        with open(os.path.join(self.dir, "config.json"), "w", encoding="utf-8") as fh:
            fh.write("non e json")
        os.environ["KORELY_API_KEY"] = "kor_live_x"
        self.assertEqual(Korely().base_url, "https://api.korely.ai")


class LoStatoDellaScrittura(unittest.TestCase):
    """`status` era sul filo e non nel modello.

    Il README del server dice, del risultato di una scrittura: "what the
    `"status": "processing"` in the reply means". Il campo arrivava e veniva
    buttato via, perche' `Memory` non lo dichiarava e `_take` tiene solo i campi
    dichiarati.

    Non e' cosmetico: i fatti vengono estratti da un worker qualche secondo dopo
    la scrittura, e "processing" e' il modo in cui una scrittura dice che i
    fatti non ci sono ancora. Senza, distinguere una lista vuota da una lista
    non-ancora si puo' fare solo tirando a indovinare o interrogando alla cieca.

    Trovato da un collaudatore che stava seguendo il README.
    """

    def test_lo_stato_arriva_fino_a_chi_chiama(self):
        m = Memory.from_dict({"id": "mem_1", "content": "x", "status": "processing"})
        self.assertEqual(m.status, "processing")

    def test_una_risposta_senza_stato_non_rompe_niente(self):
        m = Memory.from_dict({"id": "mem_1", "content": "x"})
        self.assertIsNone(m.status)

    def test_i_fatti_continuano_ad_arrivare(self):
        """Il controllo del controllo: aggiungere un campo non deve aver
        spostato quello che c'era."""
        m = Memory.from_dict({
            "id": "mem_1", "content": "x", "status": "done",
            "facts": [{"id": "fct_1", "subject": "a", "predicate": "b", "object": "c"}],
        })
        self.assertEqual(len(m.facts), 1)
        self.assertEqual(m.facts[0].subject, "a")


class ErrorsCarryWhatTheServerSaid(unittest.TestCase):
    """A 422 from a self-hosted install names the row. The SDK used to drop it.

    The hosted service answers `{code, message}`; a self-hosted install answers
    FastAPI's `{detail: [...]}`. Reading only `message` turned every one of
    those into the string "HTTP 422", so on a five-hundred-row batch there was
    no way to learn which row was bad without repeating the call with curl.
    """

    def test_the_message_names_the_field(self):
        from korely_memory.client import Korely
        from korely_memory.exceptions import APIError

        body = {"detail": [
            {"loc": ["body", "memories", 1, "content"],
             "msg": "String should have at least 1 character"},
        ]}
        with self.assertRaises(APIError) as caught:
            Korely._raise(422, body)
        self.assertIn("memories.1.content", str(caught.exception))
        self.assertEqual(caught.exception.body, body)

    def test_a_hosted_style_envelope_still_wins(self):
        from korely_memory.client import Korely
        from korely_memory.exceptions import QuotaExceededError

        with self.assertRaises(QuotaExceededError) as caught:
            Korely._raise(429, {"code": "quota_exceeded", "message": "over the cap"})
        self.assertEqual(str(caught.exception), "over the cap")

    def test_with_neither_it_still_says_something(self):
        from korely_memory.client import Korely
        from korely_memory.exceptions import APIError

        with self.assertRaises(APIError) as caught:
            Korely._raise(500, {})
        self.assertIn("500", str(caught.exception))


# ── audit 2026-09-28: contract drift, error handling, hygiene ──────────────

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "korely_memory")


class EveryHttpErrorIsAnAPIError(unittest.TestCase):
    """The public SDK docs teach one pattern: `except APIError as err` and branch
    on `err.code` ("invalid_key", "not_found", "quota_exceeded"). The typed
    errors were siblings of APIError, so that except let a 401, a 404 and a 429
    straight through. Both styles must work."""

    CASES = [
        (401, "invalid_key", AuthenticationError),
        (403, "agent_cap_exceeded", None),
        (404, "not_found", NotFoundError),
        (409, "stale_write", StaleWriteError),
        (429, "quota_exceeded", QuotaExceededError),
        (422, "invalid_request", None),
        (503, "search_unavailable", None),
    ]

    def test_the_documented_pattern_catches_every_status(self):
        for status, code, typed in self.CASES:
            with self.subTest(status=status):
                rec = _Recorder().queue(status, {"code": code, "message": "m"})
                try:
                    _client(rec).get("mem_1")
                except APIError as err:
                    self.assertEqual(err.code, code)
                    self.assertEqual(err.status, status)
                    if typed is not None:
                        self.assertIsInstance(err, typed)
                else:
                    self.fail("no exception raised")

    def test_client_side_errors_are_not_api_errors(self):
        """No key, a mismatched pair, a connection error: nothing the server
        said, so not an APIError, but still a KorelyError."""
        os.environ.pop("KORELY_API_KEY", None)
        with self.assertRaises(KorelyError) as caught:
            Korely(api_key="kor_self_x", base_url="https://api.korely.ai")
        self.assertNotIsInstance(caught.exception, APIError)


class TheBodyIsWhatTheServerSaid(unittest.TestCase):
    def test_the_retry_after_note_does_not_leak_into_the_body(self):
        """`.body` is documented as the server's response verbatim; the
        transport's `_retry_after` note used to be in it, as `None` on every
        error."""
        body = {"code": "rate_limit_exceeded", "message": "slow down", "_retry_after": "12"}
        with self.assertRaises(QuotaExceededError) as caught:
            Korely._raise(429, body)
        self.assertEqual(caught.exception.retry_after, 12)
        self.assertEqual(caught.exception.body,
                         {"code": "rate_limit_exceeded", "message": "slow down"})

    def test_a_monthly_quota_429_has_no_retry_after(self):
        """The monthly quota 429 carries no Retry-After: there is nothing to
        wait for this month, and `retry_after` must say so with None."""
        with self.assertRaises(QuotaExceededError) as caught:
            Korely._raise(429, {"code": "quota_exceeded", "message": "limit"})
        self.assertIsNone(caught.exception.retry_after)

    def test_a_fractional_retry_after_rounds_up(self):
        with self.assertRaises(QuotaExceededError) as caught:
            Korely._raise(429, {"code": "rate_limit_exceeded", "_retry_after": "1.5"})
        self.assertEqual(caught.exception.retry_after, 2)


class IdsAreOnePathSegment(unittest.TestCase):
    """An id went into the URL as it came. `delete_agent("bot#1")` sent
    `DELETE /v1/agents/bot`: urllib drops everything after `#`, so the call
    purged a different namespace instead of failing."""

    def test_ids_are_percent_encoded(self):
        cases = [
            (lambda k: k.delete_agent("bot#1"), "/v1/agents/bot%231"),
            (lambda k: k.delete_all(user_id="a/b c?d"), "/v1/users/a%2Fb%20c%3Fd/memories"),
            (lambda k: k.get("mem_1/history"), "/v1/memories/mem_1%2Fhistory"),
            (lambda k: k.update("m 1", content="x"), "/v1/memories/m%201"),
            (lambda k: k.delete("m?1"), "/v1/memories/m%3F1"),
            (lambda k: k.history("m#1"), "/v1/memories/m%231/history"),
            (lambda k: k.forget_fact("fct/1"), "/v1/facts/fct%2F1/forget"),
            (lambda k: k.correct_fact("fct 1", object="x"), "/v1/facts/fct%201"),
            (lambda k: k.batch_status("job#1"), "/v1/batch/job%231"),
        ]
        for call, path in cases:
            with self.subTest(path=path):
                rec = _Recorder()
                call(_client(rec))
                self.assertEqual(rec.last["path"], path)

    def test_an_email_is_encoded_and_the_server_decodes_it_back(self):
        rec = _Recorder()
        _client(rec).delete_all(user_id="maria@example.com")
        self.assertEqual(rec.last["path"], "/v1/users/maria%40example.com/memories")

    def test_an_empty_id_never_reaches_the_server(self):
        for call in (lambda k: k.get(""), lambda k: k.delete_agent(""),
                     lambda k: k.delete_all(user_id=""), lambda k: k.forget_fact("")):
            rec = _Recorder()
            with self.assertRaises(KorelyError):
                call(_client(rec))
            self.assertEqual(rec.calls, [])


class _FakeResponse:
    def __init__(self, status=200, body=b"{}", read_error=None):
        self.status, self._body, self._err = status, body, read_error

    def read(self):
        if self._err is not None:
            raise self._err
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TheTransportOnlyRaisesKorelyErrors(unittest.TestCase):
    """Only URLError was caught, which covers failures while connecting. A
    timeout while READING the answer, a reset mid-body or a 200 carrying a
    proxy's HTML page escaped as TimeoutError, ConnectionResetError or
    JSONDecodeError, past the `except KorelyError` in the CLI and the MCP tools."""

    def _korely(self):
        os.environ.pop("KORELY_BASE_URL", None)
        return Korely(api_key="kor_live_transport")

    def _with(self, fake):
        from unittest import mock
        return mock.patch("korely_memory.client._urlrequest.urlopen", fake)

    def test_a_read_timeout(self):
        with self._with(lambda req, timeout: _FakeResponse(read_error=TimeoutError("timed out"))):
            with self.assertRaises(KorelyError) as caught:
                self._korely().get("mem_1")
        self.assertIn("timed out", str(caught.exception))

    def test_a_connection_reset_mid_body(self):
        with self._with(lambda req, timeout: _FakeResponse(read_error=ConnectionResetError("reset"))):
            with self.assertRaises(KorelyError):
                self._korely().users()

    def test_a_200_that_is_not_json(self):
        with self._with(lambda req, timeout: _FakeResponse(body=b"<html>502 Bad Gateway</html>")):
            with self.assertRaises(KorelyError) as caught:
                self._korely().users()
        self.assertEqual(caught.exception.status, 200)
        self.assertIn("not JSON", str(caught.exception))

    def test_an_http_error_is_mapped_and_its_response_closed(self):
        import io
        from email.message import Message
        from urllib.error import HTTPError

        headers = Message()
        headers["Retry-After"] = "7"
        closed = []

        class _Err(HTTPError):
            def close(self):
                closed.append(True)
                super().close()

        def boom(req, timeout):
            raise _Err(req.full_url, 429, "Too Many Requests", headers,
                       io.BytesIO(b'{"code": "rate_limit_exceeded", "message": "slow"}'))

        with self._with(boom):
            with self.assertRaises(QuotaExceededError) as caught:
                self._korely().users()
        self.assertEqual(caught.exception.retry_after, 7)
        self.assertEqual(caught.exception.body,
                         {"code": "rate_limit_exceeded", "message": "slow"})
        self.assertTrue(closed, "the HTTPError response was never closed")

    def test_the_id_reaches_the_wire_encoded(self):
        seen = []

        def capture(req, timeout):
            seen.append(req.full_url)
            return _FakeResponse(body=b'{"agent_id": "bot#1"}')

        with self._with(capture):
            self._korely().delete_agent("bot#1")
        self.assertTrue(seen[0].endswith("/v1/agents/bot%231"), seen[0])


class WhatTheServerSendsToday(unittest.TestCase):
    """Fields the contract carries that the models dropped on the floor."""

    def test_get_facts_carries_the_total(self):
        rec = _Recorder().queue(200, {"facts": [{"id": "fct_1"}, {"id": "fct_2"}], "total": 57})
        facts = _client(rec).get_facts(user_id="u", limit=2)
        self.assertIsInstance(facts, list)
        self.assertEqual(len(facts), 2)
        self.assertEqual(facts.total, 57)
        self.assertEqual([f.id for f in facts], ["fct_1", "fct_2"])

    def test_a_fact_keeps_tense_confirmations_and_canonical_names(self):
        from korely_memory import Fact

        f = Fact.from_dict({
            "id": "fct_1", "subject": "Acme", "predicate": "uses", "object": "Groq",
            "tense": "past", "observation_count": 3,
            "last_confirmed_at": "2026-09-27T10:00:00+00:00",
            "subject_canonical": "Globex", "object_canonical": "Groq",
            "source_memory_ids": ["mem_1", "mem_2", "mem_3"],
        })
        self.assertEqual(f.tense, "past")
        self.assertEqual(f.observation_count, 3)
        self.assertEqual(f.last_confirmed_at, "2026-09-27T10:00:00+00:00")
        self.assertEqual(f.subject_canonical, "Globex")
        self.assertEqual(f.object_canonical, "Groq")
        self.assertEqual(f.source_memory_ids, ["mem_1", "mem_2", "mem_3"])

    def test_delete_all_says_the_erasure_is_permanent(self):
        rec = _Recorder().queue(200, {"user_id": "c1", "memories_forgotten": 2,
                                      "facts_invalidated": 1, "erasure": "permanent",
                                      "audit_id": "aud_1"})
        self.assertEqual(_client(rec).delete_all(user_id="c1").erasure, "permanent")

    def test_add_fact_triple_can_say_the_tense(self):
        rec = _Recorder().queue(201, {"id": "fct_1", "subject": "u", "predicate": "p",
                                      "object": "o", "tense": "past"})
        f = _client(rec).add_fact_triple("u", "p", "o", tense="past")
        self.assertEqual(rec.last["json"]["tense"], "past")
        self.assertEqual(f.tense, "past")

    def test_add_fact_triple_leaves_the_tense_to_the_server_by_default(self):
        rec = _Recorder().queue(201, {"id": "fct_1"})
        _client(rec).add_fact_triple("u", "p", "o")
        self.assertNotIn("tense", rec.last["json"])


# ── the contract as deployed on 2026-09-28 evening (GordonPro d46ab5e, c7cf69c,
#    7d7f0eb; korely-agent 6699c14, ac3a2da, ef44feb) ──────────────────────────

class ErrorsFromEitherServer(unittest.TestCase):
    """The hosted /v1 answers `{code, message}` on every 4xx and 5xx. A
    self-hosted install answers the same two keys next to FastAPI's `detail`
    (since 2026-09-28), an older one `detail` alone. Every shape ends in the
    same exception with a code and a message."""

    def _err(self, status, body):
        with self.assertRaises(APIError) as caught:
            Korely._raise(status, body)
        return caught.exception

    def test_the_hosted_envelope(self):
        e = self._err(404, {"code": "not_found", "message": "No memory with that id."})
        self.assertIsInstance(e, NotFoundError)
        self.assertEqual((e.code, e.message), ("not_found", "No memory with that id."))

    def test_a_self_hosted_envelope_reads_the_top_level_and_keeps_detail(self):
        body = {"detail": [{"type": "missing", "loc": ["body", "content"],
                            "msg": "Field required"}],
                "code": "invalid_request", "message": "content: Field required"}
        e = self._err(422, body)
        self.assertEqual((e.code, e.message), ("invalid_request", "content: Field required"))
        self.assertEqual(e.body, body)

    def test_an_older_install_with_a_code_pair_in_detail(self):
        """It came back with code None and the message "HTTP 409"."""
        e = self._err(409, {"detail": {"code": "stale_write",
                                       "message": "expected_updated_at does not match"}})
        self.assertIsInstance(e, StaleWriteError)
        self.assertEqual(e.code, "stale_write")
        self.assertEqual(e.message, "expected_updated_at does not match")
        self.assertEqual(str(e), "expected_updated_at does not match")

    def test_an_older_install_with_a_sentence(self):
        e = self._err(403, {"detail": "API key missing required scope(s): memories:write"})
        self.assertIsInstance(e, NamespaceForbiddenError)
        self.assertIsNone(e.code)
        self.assertEqual(e.message, "API key missing required scope(s): memories:write")

    def test_the_top_level_wins_over_detail(self):
        e = self._err(422, {"detail": "old words", "code": "invalid_request",
                            "message": "new words"})
        self.assertEqual(e.message, "new words")

    def test_a_blank_message_falls_back_to_detail(self):
        e = self._err(400, {"code": "bad_request", "message": "", "detail": "why"})
        self.assertEqual((e.code, e.message), ("bad_request", "why"))

    def test_through_the_real_transport(self):
        import io
        from email.message import Message
        from unittest import mock
        from urllib.error import HTTPError

        def boom(req, timeout):
            raise HTTPError(req.full_url, 404, "Not Found", Message(), io.BytesIO(
                b'{"detail": {"code": "not_found", '
                b'"message": "No agent namespace \'bot\' in this project."}}'))

        os.environ.pop("KORELY_BASE_URL", None)
        with mock.patch("korely_memory.client._urlrequest.urlopen", boom):
            with self.assertRaises(NotFoundError) as caught:
                Korely(api_key="kor_live_transport").delete_agent("bot")
        self.assertEqual(caught.exception.code, "not_found")
        self.assertIn("in this project", caught.exception.message)


class TheErasureReceiptSaysDeleted(unittest.TestCase):
    """DELETE /v1/users/{id}/memories deletes rows physically, and since
    2026-09-28 says so: `memories_deleted` / `facts_deleted`, with the old
    `memories_forgotten` / `facts_invalidated` kept as deprecated aliases."""

    def _receipt(self, body):
        rec = _Recorder().queue(200, dict({"user_id": "c1", "erasure": "permanent",
                                           "audit_id": "aud_1"}, **body))
        return _client(rec).delete_all(user_id="c1")

    def test_the_new_names_are_read(self):
        r = self._receipt({"memories_deleted": 5, "facts_deleted": 3,
                           "memories_forgotten": 5, "facts_invalidated": 3})
        self.assertEqual((r.memories_deleted, r.facts_deleted), (5, 3))
        self.assertEqual((r.memories_forgotten, r.facts_invalidated), (5, 3))

    def test_an_older_server_fills_the_new_names(self):
        r = self._receipt({"memories_forgotten": 2, "facts_invalidated": 1})
        self.assertEqual((r.memories_deleted, r.facts_deleted), (2, 1))

    def test_a_server_without_the_old_names_still_fills_them(self):
        """Zero is a count, not an absence: it must travel too."""
        r = self._receipt({"memories_deleted": 4, "facts_deleted": 0})
        self.assertEqual((r.memories_forgotten, r.facts_invalidated), (4, 0))

    def test_positional_construction_means_what_it_meant(self):
        from korely_memory import BulkReceipt

        r = BulkReceipt("c1", 2, 1, "permanent", "aud_1")
        self.assertEqual((r.memories_forgotten, r.facts_invalidated, r.audit_id),
                         (2, 1, "aud_1"))

    def test_the_old_names_are_documented_as_deprecated(self):
        from korely_memory import BulkReceipt

        self.assertIn("deprecated", BulkReceipt.__doc__.lower())


class AgentsBelongToAProject(unittest.TestCase):
    """GET /v1/agents: `total` counts this key's project, `used` the account.
    DELETE /v1/agents/{id}: 404 outside the project, and `slot_freed`."""

    def test_used_can_be_above_total(self):
        rec = _Recorder().queue(200, {
            "agents": [{"agent_id": "bot", "memories": 1, "facts": 0, "last_active": None}],
            "total": 1, "cap": 2, "used": 2,
        })
        page = _client(rec).list_agents()
        self.assertEqual((page.total, page.used, page.cap), (1, 2, 2))
        self.assertEqual([a.agent_id for a in page], ["bot"])

    def test_delete_agent_says_whether_the_slot_is_free(self):
        rec = _Recorder().queue(200, {"agent_id": "bot", "memories_deleted": 3,
                                      "facts_deleted": 1, "audit_id": "aud_1",
                                      "slot_freed": False})
        r = _client(rec).delete_agent("bot")
        self.assertIs(r.slot_freed, False)
        self.assertEqual((r.memories_deleted, r.facts_deleted), (3, 1))

    def test_a_server_that_does_not_say_leaves_it_none(self):
        """A self-hosted install has no cap and sends no slot_freed."""
        rec = _Recorder().queue(200, {"agent_id": "bot", "memories_deleted": 0,
                                      "facts_deleted": 0, "audit_id": "aud_1"})
        self.assertIsNone(_client(rec).delete_agent("bot").slot_freed)

    def test_a_name_outside_the_project_is_not_found(self):
        rec = _Recorder().queue(404, {"code": "not_found",
                                      "message": "No agent namespace 'bot' in this project."})
        with self.assertRaises(NotFoundError):
            _client(rec).delete_agent("bot")


class BatchItemsTakeATimestamp(unittest.TestCase):
    """POST /v1/batch accepts `timestamp` per item, with the meaning it has on
    a single add(); an unreadable one is a 422 naming `memories[i].timestamp`."""

    _MSG = ("memories[1].timestamp 'yesterday' is not an ISO 8601 date or datetime "
            "(e.g. '2026-03-01' or '2026-03-01T14:30:00Z')")

    def test_each_item_carries_its_timestamp(self):
        items = [{"content": "Franco signed up on Pro.", "user_id": "franco",
                  "timestamp": "2026-01-15"},
                 {"content": "Franco downgraded to Free.", "user_id": "franco",
                  "timestamp": "2026-06-20T09:00:00Z"}]
        rec = _Recorder().queue(202, {"id": "job_1", "status": "processing", "received": 2})
        _client(rec).batch(items)
        self.assertEqual(rec.last["json"], {"memories": items})

    def test_an_unreadable_timestamp_names_the_item_on_either_server(self):
        for body in ({"code": "invalid_request", "message": self._MSG},            # hosted
                     {"detail": self._MSG, "code": "invalid_request",
                      "message": self._MSG},                                       # self-hosted
                     {"detail": self._MSG}):                                       # older install
            with self.subTest(keys=sorted(body)):
                rec = _Recorder().queue(422, body)
                with self.assertRaises(APIError) as caught:
                    _client(rec).batch([{"content": "a"}, {"content": "b",
                                                           "timestamp": "yesterday"}])
                self.assertIn("memories[1].timestamp", str(caught.exception))

    def test_batch_memory_declares_the_keys(self):
        from korely_memory import BatchMemory

        self.assertEqual(BatchMemory.__required_keys__, frozenset({"content"}))
        self.assertEqual(BatchMemory.__optional_keys__,
                         frozenset({"user_id", "agent_id", "run_id", "metadata", "timestamp"}))


class CorrectionsSayWhatTheyClosed(unittest.TestCase):
    """PATCH /v1/facts/{id} answered `invalidated: []` although it had just
    closed the fact it corrected. It now lists every id it superseded, and a
    correction that restates the fact reconfirms it instead."""

    def test_invalidated_lists_every_superseded_id(self):
        rec = _Recorder().queue(200, {
            "id": "fct_new", "subject": "maria", "predicate": "lives_in", "object": "Rome",
            "invalidated": ["fct_old", "fct_other"], "tense": "current",
            "observation_count": 1,
        })
        f = _client(rec).correct_fact("fct_old", object="Rome")
        self.assertEqual(rec.last["method"], "PATCH")
        self.assertEqual(rec.last["json"], {"object": "Rome"})
        self.assertEqual(f.id, "fct_new")
        self.assertEqual(f.invalidated, ["fct_old", "fct_other"])

    def test_a_correction_that_changes_nothing_reconfirms(self):
        rec = _Recorder().queue(200, {
            "id": "fct_1", "subject": "maria", "predicate": "lives_in", "object": "Milan",
            "invalidated": [], "observation_count": 2,
        })
        f = _client(rec).correct_fact("fct_1", object="Milan")
        self.assertEqual(f.id, "fct_1")
        self.assertEqual(f.invalidated, [])
        self.assertEqual(f.observation_count, 2)


class PagesGoTo200(unittest.TestCase):
    """/v1/memories and /v1/events honour `limit` up to 200 (get_all was cut at
    100 server-side until 2026-09-28). The SDK passes it through untouched."""

    def test_get_all_and_events_send_200(self):
        rec = (_Recorder().queue(200, {"memories": [], "total": 0})
               .queue(200, {"events": [], "processing": 0}))
        k = _client(rec)
        k.get_all(user_id="u", limit=200)
        k.events(status="error", limit=200)
        self.assertEqual(rec.calls[0]["params"]["limit"], 200)
        self.assertEqual(rec.calls[1]["params"], {"status": "error", "limit": 200})


def _published_text_files():
    """The files a reader sees on PyPI, npm and GitHub, plus the shipped code."""
    out = []
    for root, _dirs, files in os.walk(_PKG):
        out += [os.path.join(root, f) for f in files if f.endswith(".py")]
    for rel in ("README.md", "CHANGELOG.md", "python/README.md", "python/pyproject.toml",
                "js/README.md", "js/package.json", "n8n/README.md", "RELEASING.md"):
        p = os.path.join(_REPO, rel)
        if os.path.exists(p):
            out.append(p)
    js_src = os.path.join(_REPO, "js", "src")
    if os.path.isdir(js_src):
        out += [os.path.join(js_src, f) for f in os.listdir(js_src)]
    return out


class NoFalsePromises(unittest.TestCase):
    def test_no_fact_extracted_webhook_is_promised(self):
        """The webhook events are memory.created, fact.invalidated and
        quota.warning. `fact_extracted` is a history() entry; the README and
        the events() docstring sent people to a webhook that never fires."""
        import re

        for path in _published_text_files():
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            with self.subTest(path=os.path.relpath(path, _REPO)):
                # `fact_extracted` and "webhook" in one sentence, either order.
                self.assertIsNone(
                    re.search(r"webhook[^.]{0,120}fact_extracted"
                              r"|fact_extracted[^.]{0,60}webhook", text, re.I),
                    "promises a fact_extracted webhook")

    def test_no_text_says_batch_refuses_timestamp(self):
        """POST /v1/batch takes `timestamp` per item since 2026-09-28. The
        docstrings of both clients and the README said it was refused."""
        import re

        for path in _published_text_files():
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            with self.subTest(path=os.path.relpath(path, _REPO)):
                self.assertIsNone(
                    re.search(r"`+timestamp`+ included|not take a `+timestamp", text),
                    "says batch() refuses timestamp")

    def test_no_text_says_processing_counts_only_the_latest_200(self):
        """`processing` counts every write still in flight since 2026-09-28."""
        for path in _published_text_files():
            with open(path, encoding="utf-8") as fh:
                text = " ".join(fh.read().split())
            with self.subTest(path=os.path.relpath(path, _REPO)):
                self.assertNotIn("200 most recent", text)

    def test_no_em_dash_in_published_text(self):
        for path in _published_text_files():
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            with self.subTest(path=os.path.relpath(path, _REPO)):
                self.assertNotIn("\u2014", text)
                self.assertNotIn("\\u2014", text)


class RunsOnThePythonItDeclares(unittest.TestCase):
    """pyproject says >=3.9. Syntax from a later Python would install fine and
    break at import on 3.9, where no test here runs."""

    def test_every_module_parses_as_python_3_9(self):
        import ast

        for name in sorted(os.listdir(_PKG)):
            if not name.endswith(".py"):
                continue
            with self.subTest(module=name):
                with open(os.path.join(_PKG, name), encoding="utf-8") as fh:
                    ast.parse(fh.read(), filename=name, feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
