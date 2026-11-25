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
    ConflictError,
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

    def __call__(self, method, path, *, params=None, json_body=None, headers=None):
        self.calls.append({"method": method, "path": path, "params": params, "json": json_body,
                           "headers": headers})
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

    def test_an_unknown_region_is_refused_not_turned_into_eu(self):
        """Up to 0.2.2 any region but "eu" fell back to "eu" in silence:
        region="global", the name of a processing region, looked chosen and
        changed nothing (finding of 25 Nov 2026). Refused as a wrong
        processing_region is, before any request, on every door."""
        from korely_memory import AsyncKorely

        for wrong in ("global", "EU", "us", "", 1):
            with self.subTest(region=wrong):
                with self.assertRaises(KorelyError) as said:
                    Korely(api_key="x", region=wrong)
                self.assertIn("processing_region", str(said.exception))
                with self.assertRaises(KorelyError):
                    Korely(api_key="x", region=wrong, base_url="http://localhost:8000")
                with self.assertRaises(KorelyError):
                    AsyncKorely(api_key="x", region=wrong)
                with self.assertRaises(KorelyError):
                    Korely.init_agent("app", region=wrong, base_url="http://localhost:8000")
        self.assertEqual(Korely(api_key="x", region=None).base_url, "https://api.korely.ai",
                         "None is the default, as undefined is in Node")

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

    def test_search_sends_min_score_only_when_given(self):
        """2026-10-07: the optional floor of both servers."""
        rec = _Recorder().queue(200, {"results": []}).queue(200, {"results": []})
        _client(rec).search("northwind pricing", min_score=0.2)
        self.assertEqual(rec.last["json"]["min_score"], 0.2)
        _client(rec).search("northwind pricing")
        self.assertNotIn("min_score", rec.last["json"])

    def test_search_takes_the_servers_types_and_full_text(self):
        """Korely 1.0.13 searches the facts too (`types`) and returns the
        whole text (`full_text`); the SDK sent neither and dropped `type`,
        `content` and `fact`, so a hit was always 280 characters of a memory."""
        fact = {"id": "fct_1", "subject": "Maria", "predicate": "lives_in", "object": "Turin",
                "valid_from": "2026-03-01T00:00:00+00:00"}
        rec = _Recorder().queue(200, {"results": [
            {"id": "mem_1", "score": 0.81, "snippet": "x" * 280, "user_id": "maria",
             "agent_id": None, "metadata": {}, "type": "memory", "content": "x" * 900},
            {"id": "fct_1", "score": 0.77, "snippet": "Maria · lives_in · Turin",
             "user_id": "maria", "agent_id": None, "metadata": {}, "type": "fact",
             "content": "Maria · lives_in · Turin", "fact": fact},
        ]}).queue(200, {"results": [{"id": "mem_2", "score": 0.5, "snippet": "s"}]})
        hits = _client(rec).search("where does Maria live", user_id="maria",
                                   types=["memories", "facts"], full_text=True)
        self.assertEqual(rec.last["json"]["types"], ["memories", "facts"])
        self.assertIs(rec.last["json"]["full_text"], True)
        self.assertEqual([h.type for h in hits], ["memory", "fact"])
        self.assertEqual(len(hits[0].content), 900)
        self.assertIsNone(hits[0].fact)
        self.assertEqual(hits[1].fact.object, "Turin")
        self.assertEqual(hits[1].fact.valid_from, "2026-03-01T00:00:00+00:00")
        # Not given, not sent: a server older than 1.0.13 answers 422 to either.
        plain = _client(rec).search("where does Maria live")
        self.assertNotIn("types", rec.last["json"])
        self.assertNotIn("full_text", rec.last["json"])
        self.assertEqual((plain[0].type, plain[0].content, plain[0].fact), (None, None, None))

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

    def test_the_facts_true_again_reach_the_receipt(self):
        """The server's answers as Korely 1.0.2 and later give them
        (services/facts/erasure.py): `facts_reopened` was dropped by the
        receipts, and a program could not see a deletion give facts back."""
        import asyncio

        from korely_memory import AsyncKorely

        forgotten = {"id": "mem_1", "status": "forgotten", "facts_invalidated": 1,
                     "facts_reopened": 2, "audit_id": "aud_3d0f"}
        purged = {"agent_id": "bot", "memories_deleted": 3, "facts_deleted": 4,
                  "facts_reopened": 1, "read_marks_deleted": 0, "audit_id": "aud_4"}
        k = _client(_Recorder().queue(200, forgotten).queue(200, purged))
        self.assertEqual(k.delete("mem_1").facts_reopened, 2)
        self.assertEqual(k.delete_agent("bot").facts_reopened, 1)
        ak = AsyncKorely(api_key="kor_live_test")
        ak._sync._send = _Recorder().queue(200, forgotten)
        self.assertEqual(asyncio.run(ak.delete("mem_1")).facts_reopened, 2)
        # An older server does not send it: None, not 0.
        k = _client(_Recorder().queue(200, {"id": "mem_1", "status": "forgotten",
                                           "facts_invalidated": 1, "audit_id": "aud_1"}))
        self.assertIsNone(k.delete("mem_1").facts_reopened)

    def test_kind_and_exclude_sessions_are_sent_only_when_asked(self):
        rec = _Recorder().queue(201, {"id": "mem_1", "content": "c"}).queue(201, {"id": "mem_2", "content": "c"})
        k = _client(rec)
        k.add("log", user_id="u", kind="session")
        self.assertEqual(rec.last["json"]["kind"], "session")
        k.add("note", user_id="u")
        self.assertNotIn("kind", rec.last["json"], "an older server refuses an unknown field")
        k.search("q", user_id="u", exclude_sessions=True)
        self.assertIs(rec.last["json"]["exclude_sessions"], True)
        k.search("q", user_id="u")
        self.assertNotIn("exclude_sessions", rec.last["json"])
        k.get_facts(user_id="u", exclude_sessions=True)
        self.assertEqual(rec.last["params"]["exclude_sessions"], "true")
        k.get_context("q", user_id="u", exclude_sessions=True)
        self.assertEqual(rec.last["params"]["exclude_sessions"], "true")

    def test_declare_name_posts_the_name_for_the_end_user(self):
        rec = _Recorder().queue(201, {"user_id": "maria", "name": "Maria Rossi",
                                      "agent_id": None, "since": "2026-10-18T10:00:00Z"})
        d = _client(rec).declare_name("Maria Rossi", user_id="maria")
        self.assertEqual((rec.last["method"], rec.last["path"]), ("POST", "/v1/users/maria/names"))
        self.assertEqual(rec.last["json"], {"name": "Maria Rossi"})
        self.assertEqual((d.user_id, d.name), ("maria", "Maria Rossi"))

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

    def test_get_facts_known_as_of(self):
        """Korely Agent 0.1.26: what the store knew on a date, and when it
        learned a fact had ended."""
        rec = _Recorder().queue(200, {"facts": [
            {"object": "Milan", "invalid_at": None, "closed_known_at": None},
        ], "total": 1})
        facts = _client(rec).get_facts(user_id="maria", known_as_of="2026-09-20")
        self.assertEqual(rec.last["params"]["known_as_of"], "2026-09-20")
        self.assertNotIn("as_of", rec.last["params"])
        self.assertEqual(facts[0].object, "Milan")
        rec = _Recorder().queue(200, {"facts": [
            {"object": "Milan", "invalid_at": "2026-09-01T00:00:00+00:00",
             "closed_known_at": "2026-10-05T09:00:00+00:00"},
        ], "total": 1})
        facts = _client(rec).get_facts(user_id="maria", include_invalidated=True)
        self.assertEqual(facts[0].closed_known_at, "2026-10-05T09:00:00+00:00")
        self.assertNotIn("known_as_of", rec.last["params"])

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

    def test_a_read_that_names_no_budget_asks_for_the_apis_1500(self):
        """Korely 1.0.35 packs the block into 1,500 tokens when a read names
        no budget (the server's services/context.py, DEFAULT_TOKEN_BUDGET).
        The client names one on every read, so its default must be the API's:
        0.2.1 sent 800 and kept the old, smaller block after the server moved."""
        import asyncio

        from korely_memory import AsyncKorely

        rec = _Recorder().queue(200, {"context": "", "tokens": 0, "sources": []})
        _client(rec).get_context("where does Maria live?", user_id="maria")
        self.assertEqual(rec.last["params"]["token_budget"], 1500)
        ak = AsyncKorely(api_key="kor_live_test")
        ak._sync._send = rec
        asyncio.run(ak.get_context("where does Maria live?", user_id="maria"))
        self.assertEqual(rec.last["params"]["token_budget"], 1500)

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
        self.assertIsNone(cm.exception.limit, "a server that does not send it")

    def test_429_quota_carries_limit_used_and_resets_at(self):
        """2026-10-07: the Cloud's machine fields, never the message's words."""
        rec = _Recorder().queue(429, {"code": "quota_exceeded", "message": "2,000 writes on the ...",
                                      "limit": 2000, "used": 2200, "resets_at": "2026-11-01"})
        with self.assertRaises(QuotaExceededError) as cm:
            _client(rec).add("x")
        e = cm.exception
        self.assertEqual((e.limit, e.used, e.resets_at), (2000, 2200, "2026-11-01"))

    def test_409_fact_not_current_names_the_fact_to_correct(self):
        """2026-10-07: the id as a field, inside `detail` (the Self-hosted) or
        at the top (either server may), never read off the message."""
        for body in ({"detail": {"code": "fact_not_current", "message": "superseded by fct_new",
                                 "current_fact_id": "fct_new"},
                      "code": "fact_not_current", "message": "superseded by fct_new"},
                     {"code": "fact_not_current", "message": "superseded by fct_new",
                      "current_fact_id": "fct_new"}):
            rec = _Recorder().queue(409, body)
            with self.assertRaises(ConflictError) as cm:
                _client(rec).correct_fact("fct_old", object="Team plan")
            self.assertNotIsInstance(cm.exception, StaleWriteError)
            self.assertEqual((cm.exception.code, cm.exception.current_fact_id),
                             ("fact_not_current", "fct_new"))

    def test_409_fact_not_current_with_no_successor(self):
        rec = _Recorder().queue(409, {"detail": {"code": "fact_not_current", "message": "write it",
                                                 "current_fact_id": None},
                                      "code": "fact_not_current", "message": "write it"})
        with self.assertRaises(ConflictError) as cm:
            _client(rec).correct_fact("fct_old", object="Team plan")
        self.assertIsNone(cm.exception.current_fact_id)

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
        # 2026-10-07: an older server left the owner out of the write shape,
        # and the Fact said user_id None: what was sent is what it was stored under.
        self.assertEqual(f.user_id, "mario")
        self.assertIsNone(f.agent_id)

    def test_add_fact_triple_takes_the_owner_the_server_answers(self):
        rec = _Recorder().queue(201, {
            "id": "fct_1", "subject": "Mario", "predicate": "works_at", "object": "Acme",
            "user_id": "mario", "agent_id": None,
        })
        f = _client(rec).add_fact_triple("Mario", "works_at", "Acme", user_id="mario", agent_id="x")
        self.assertEqual(f.user_id, "mario")
        self.assertIsNone(f.agent_id, "the server's answer wins")

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

    def test_get_profile_known_as_of(self):
        rec = _Recorder().queue(200, {"user_id": "maria", "as_of": None,
                                      "known_as_of": "2026-09-20", "facts": [],
                                      "by_family": {}, "total": 0})
        p = _client(rec).get_profile(user_id="maria", known_as_of="2026-09-20")
        self.assertEqual(rec.last["params"]["known_as_of"], "2026-09-20")
        self.assertEqual(p.known_as_of, "2026-09-20")

    def test_get_context_known_as_of(self):
        rec = _Recorder().queue(200, {"context": "## Known facts", "tokens": 3, "sources": []})
        _client(rec).get_context("where does she live?", user_id="maria", known_as_of="2026-09-20")
        self.assertEqual(rec.last["path"], "/v1/context")
        self.assertEqual(rec.last["params"]["known_as_of"], "2026-09-20")
        rec = _Recorder().queue(200, {"context": "", "tokens": 0, "sources": []})
        _client(rec).get_context("where does she live?", user_id="maria")
        self.assertNotIn("known_as_of", rec.last["params"])

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
        """Awaitable, or for the iterators (iter_audit) an async iterator:
        `async for`, each page fetched off the event loop."""
        import inspect
        from korely_memory import AsyncKorely

        for name, fn in inspect.getmembers(AsyncKorely, inspect.isfunction):
            if name.startswith("_"):
                continue
            with self.subTest(method=name):
                if name.startswith("iter_"):
                    self.assertTrue(inspect.isasyncgenfunction(fn),
                                    f"{name} should be an async iterator")
                else:
                    self.assertTrue(inspect.iscoroutinefunction(fn),
                                    f"{name} should be async")

    def test_every_mirrored_method_takes_every_argument_of_the_sync_one(self):
        """Read from the two signatures, so a parameter added to a `Korely`
        method reaches its `AsyncKorely` mirror or this fails (the
        `idempotency_key` of `batch()` did not: the mirror took `memories`
        alone)."""
        import inspect
        from korely_memory import AsyncKorely

        kinds = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        for name, fn in inspect.getmembers(AsyncKorely, inspect.isfunction):
            if name.startswith("_"):
                continue
            mine = inspect.signature(fn).parameters
            takes_any_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD
                                    for p in mine.values())
            for p in list(inspect.signature(getattr(Korely, name)).parameters.values())[1:]:
                with self.subTest(method=name, parameter=p.name):
                    if p.kind in kinds:
                        self.assertIn(p.kind, {q.kind for q in mine.values()})
                    else:
                        self.assertTrue(p.name in mine or takes_any_keyword,
                                        f"AsyncKorely.{name} does not take {p.name}")

    def test_batch_sends_the_idempotency_key_from_async_too(self):
        import asyncio
        from korely_memory import AsyncKorely

        rec = _Recorder()
        rec.queue(202, {"job_id": "j1", "status": "queued", "total": 1})
        korely = AsyncKorely()
        korely._sync._send = rec
        asyncio.run(korely.batch([{"content": "x"}], idempotency_key="k-1"))
        self.assertEqual(rec.calls[0]["headers"], {"Idempotency-Key": '"k-1"'})

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

    def test_an_id_that_is_not_a_string_never_reaches_the_server(self):
        """A dict, a list, a function or a boolean is no id: str() used to send
        it as the path (the JS client's "/v1/facts/[object Object]" in the
        hosted product's logs, 2026-10-05). A whole number still goes."""
        for bad in ({"id": "m1"}, ["m1"], print, True, 1.5):
            with self.subTest(bad=repr(bad)):
                rec = _Recorder()
                with self.assertRaises(KorelyError):
                    _client(rec).get(bad)
                self.assertEqual(rec.calls, [])
        rec = _Recorder()
        _client(rec).get(42)
        self.assertEqual(rec.last["path"], "/v1/memories/42")

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
        return mock.patch("korely_memory.client._open", fake)

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


# ── the contract as deployed on 2026-09-28 evening, on the Cloud and the
#    Self-hosted ───────────────────────────────────────────────────────────────

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
        with mock.patch("korely_memory.client._open", boom):
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
    DELETE /v1/agents/{id}: 404 outside the project; no `slot_freed`."""

    def test_used_can_be_above_total(self):
        rec = _Recorder().queue(200, {
            "agents": [{"agent_id": "bot", "memories": 1, "facts": 0, "last_active": None}],
            "total": 1, "cap": 2, "used": 2,
        })
        page = _client(rec).list_agents()
        self.assertEqual((page.total, page.used, page.cap), (1, 2, 2))
        self.assertEqual([a.agent_id for a in page], ["bot"])

    def test_delete_agent_reads_what_the_server_sends(self):
        """The server's answer (services/facts/erasure.py delete_agent): the
        counts and the audit id. No Korely server sends `slot_freed`, and
        none ever has: the field stays, deprecated, and is None; whether a
        slot is free is `cap` and `used` of `list_agents()`."""
        rec = _Recorder().queue(200, {"agent_id": "bot", "memories_deleted": 3,
                                      "facts_deleted": 1, "facts_reopened": 0,
                                      "read_marks_deleted": 0, "audit_id": "aud_1"})
        r = _client(rec).delete_agent("bot")
        self.assertEqual((r.agent_id, r.memories_deleted, r.facts_deleted, r.audit_id),
                         ("bot", 3, 1, "aud_1"))
        self.assertIsNone(r.slot_freed)
        self.assertIn("deprecated", type(r).__doc__)

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
                         frozenset({"user_id", "agent_id", "run_id", "metadata", "timestamp", "kind"}))


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
        # Subpackages too: korely_memory/integrations/ arrived with the
        # LangGraph extra, and a listing of the top folder alone missed it.
        import ast

        for root, _dirs, files in os.walk(_PKG):
            for name in sorted(files):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                with self.subTest(module=os.path.relpath(path, _PKG)):
                    with open(path, encoding="utf-8") as fh:
                        ast.parse(fh.read(), filename=name, feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()


class TestTheCertificateAuthority(unittest.TestCase):
    """A server whose certificate a private CA signed (Caddy's local CA on a
    laptop install): the Python 3.9 of macOS does not read SSL_CERT_FILE, so
    the client takes the CA file itself (found by the blind installs of
    Korely Agent 0.1.13, 2026-09-28)."""

    def setUp(self):
        self._env = dict(os.environ)
        os.environ.pop("KORELY_CA_FILE", None)
        fd, self.ca = tempfile.mkstemp(suffix=".pem")
        os.close(fd)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        os.unlink(self.ca)

    def _request_context(self, **kw):
        from unittest import mock

        seen = {}

        class _Resp:
            status = 200
            headers = {}

            def read(self):
                return b"{}"

            def getcode(self):
                return 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None, context=None):
            seen["context"] = context
            return _Resp()

        with mock.patch("ssl.create_default_context") as cdc, \
                mock.patch("korely_memory.client._open", side_effect=fake_urlopen):
            cdc.return_value = "ctx-with-ca"
            k = Korely(api_key="kor_self_x", base_url="https://korely.localhost", **kw)
            k._send("GET", "/v1/memories")
        return seen["context"], cdc

    def test_default_uses_python_s_trust_store(self):
        ctx, cdc = self._request_context()
        self.assertIsNone(ctx)
        cdc.assert_not_called()

    def test_ca_file_argument(self):
        ctx, cdc = self._request_context(ca_file=self.ca)
        self.assertEqual(ctx, "ctx-with-ca")
        cdc.assert_called_once_with(cafile=self.ca)

    def test_ca_file_from_the_environment(self):
        os.environ["KORELY_CA_FILE"] = self.ca
        ctx, cdc = self._request_context()
        cdc.assert_called_once_with(cafile=self.ca)

    def test_a_missing_ca_file_is_an_error_not_a_silent_default(self):
        with self.assertRaises(KorelyError):
            Korely(api_key="kor_self_x", base_url="https://korely.localhost", ca_file="/nope.pem")

    def test_verify_false_warns_and_skips_the_check(self):
        import ssl
        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            k = Korely(api_key="kor_self_x", base_url="https://korely.localhost", verify=False)
        self.assertEqual(k._ssl_context.verify_mode, ssl.CERT_NONE)
        self.assertTrue(any("not checked" in str(x.message) for x in w))


# ── 2026-10-06: the gaps found comparing every /v1 route of the Cloud and the
#    Self-hosted with the SDKs ─────────────────────────────────────────────────

class OnlyAStaleWriteIsAStaleWrite(unittest.TestCase):
    """Every 409 was a StaleWriteError. `DELETE /v1/account` answers 409
    `account_has_login`, which has nothing to do with an update, and the
    Self-hosted names a 409 it has no other name for `conflict`."""

    def _err(self, body):
        from korely_memory import ConflictError

        with self.assertRaises(ConflictError) as caught:
            Korely._raise(409, body)
        return caught.exception

    def test_account_has_login_is_a_conflict_not_a_stale_write(self):
        e = self._err({"code": "account_has_login",
                       "message": "This key belongs to an account with a Korely login."})
        self.assertNotIsInstance(e, StaleWriteError)
        self.assertIsInstance(e, APIError)
        self.assertEqual((e.status, e.code), (409, "account_has_login"))
        self.assertIn("Korely login", e.message)

    def test_the_self_hosted_generic_conflict(self):
        e = self._err({"detail": "a key with that name already exists",
                       "code": "conflict", "message": "a key with that name already exists"})
        self.assertNotIsInstance(e, StaleWriteError)
        self.assertEqual(e.code, "conflict")

    def test_a_409_that_names_no_code_is_not_called_a_stale_write(self):
        e = self._err({"detail": "something clashed"})
        self.assertNotIsInstance(e, StaleWriteError)
        self.assertIsNone(e.code)

    def test_stale_write_is_still_a_stale_write_and_a_conflict(self):
        from korely_memory import ConflictError

        for body in ({"code": "stale_write", "message": "stale"},
                     {"detail": {"code": "stale_write", "message": "stale"}}):
            with self.subTest(keys=sorted(body)):
                rec = _Recorder().queue(409, body)
                with self.assertRaises(StaleWriteError) as caught:
                    _client(rec).update("mem_1", content="x",
                                        expected_updated_at="2000-01-01T00:00:00Z")
                self.assertIsInstance(caught.exception, ConflictError)
                self.assertEqual(caught.exception.code, "stale_write")


class TheRetryAfterOfAPausedWrite(unittest.TestCase):
    """The Cloud answers 503 `writes_paused`, with Retry-After, when its daily
    model budget is spent (the Cloud's spending brake). The transport
    read the header on every error and only a 429 kept it, so the one number
    that says when writes resume was thrown away."""

    _PAUSED = {"code": "writes_paused",
               "message": "Writes that need a model are paused until 00:00 UTC."}

    def test_a_503_keeps_its_retry_after(self):
        with self.assertRaises(APIError) as caught:
            Korely._raise(503, dict(self._PAUSED, _retry_after="41234"))
        e = caught.exception
        self.assertNotIsInstance(e, QuotaExceededError)
        self.assertEqual((e.status, e.code, e.retry_after), (503, "writes_paused", 41234))
        self.assertEqual(e.body, self._PAUSED)

    def test_through_the_real_transport(self):
        import io
        from email.message import Message
        from unittest import mock
        from urllib.error import HTTPError

        headers = Message()
        headers["Retry-After"] = "41234"

        def paused(req, timeout):
            raise HTTPError(req.full_url, 503, "Service Unavailable", headers,
                            io.BytesIO(json.dumps(self._PAUSED).encode("utf-8")))

        os.environ.pop("KORELY_BASE_URL", None)
        with mock.patch("korely_memory.client._open", paused):
            with self.assertRaises(APIError) as caught:
                Korely(api_key="kor_live_transport").update("mem_1", content="x")
        self.assertEqual(caught.exception.code, "writes_paused")
        self.assertEqual(caught.exception.retry_after, 41234)

    def test_an_error_without_the_header_says_none(self):
        for status in (401, 404, 409, 422, 500, 503):
            with self.subTest(status=status):
                with self.assertRaises(APIError) as caught:
                    Korely._raise(status, {"code": "x", "message": "m"})
                self.assertIsNone(caught.exception.retry_after)

    def test_the_429_signature_still_works(self):
        e = QuotaExceededError("slow", status=429, code="rate_limit_exceeded",
                               retry_after=3, body={"code": "rate_limit_exceeded"})
        self.assertEqual((e.retry_after, e.code, e.body), (3, "rate_limit_exceeded",
                                                           {"code": "rate_limit_exceeded"}))
        self.assertIsNone(APIError("m").retry_after)


class TooManyBatchesIsNotTheMonthlyQuota(unittest.TestCase):
    """The Cloud refuses a fourth batch while three are still being imported:
    429 `too_many_batches`, no Retry-After (the Cloud's import limit).
    As a plain QuotaExceededError with `retry_after` None it looked exactly
    like the monthly quota used up, which the docs say to stop on."""

    def test_its_own_class_still_a_quota_error(self):
        from korely_memory import TooManyBatchesError

        rec = _Recorder().queue(429, {"code": "too_many_batches",
                                      "message": "3 batches are still being imported; "
                                                 "wait for one to finish."})
        with self.assertRaises(TooManyBatchesError) as caught:
            _client(rec).batch([{"content": "a"}])
        e = caught.exception
        self.assertIsInstance(e, QuotaExceededError)
        self.assertIsInstance(e, APIError)
        self.assertEqual((e.status, e.code), (429, "too_many_batches"))
        self.assertIsNone(e.retry_after)

    def test_the_monthly_quota_is_not_called_too_many_batches(self):
        from korely_memory import TooManyBatchesError

        for code in ("quota_exceeded", "rate_limit_exceeded"):
            with self.subTest(code=code):
                rec = _Recorder().queue(429, {"code": code, "message": "m"})
                with self.assertRaises(QuotaExceededError) as caught:
                    _client(rec).batch([{"content": "a"}])
                self.assertNotIsInstance(caught.exception, TooManyBatchesError)
                self.assertEqual(caught.exception.code, code)


class PingChecksAKeyForFree(unittest.TestCase):
    """GET /v1/ping, on both products: no scope, no rate limit, no quota."""

    def test_the_cloud_answer(self):
        from korely_memory import PingResponse

        rec = _Recorder().queue(200, {"ok": True, "tier": "hobby", "region": "eu-hel1",
                                      "scopes": ["memories:read", "memories:write"]})
        p = _client(rec).ping()
        self.assertEqual((rec.last["method"], rec.last["path"]), ("GET", "/v1/ping"))
        self.assertIsNone(rec.last["params"])
        self.assertIsNone(rec.last["json"])
        self.assertIsInstance(p, PingResponse)
        self.assertIs(p.ok, True)
        self.assertEqual((p.tier, p.region), ("hobby", "eu-hel1"))
        self.assertEqual(p.scopes, ["memories:read", "memories:write"])

    def test_the_self_hosted_answer_has_the_same_shape(self):
        rec = _Recorder().queue(200, {"ok": True, "tier": "hobby", "region": "eu",
                                      "scopes": ["memories:read"]})
        p = _client(rec).ping()
        self.assertEqual((p.ok, p.region, p.scopes), (True, "eu", ["memories:read"]))

    def test_a_key_that_does_not_work_is_an_authentication_error(self):
        rec = _Recorder().queue(401, {"code": "invalid_key", "message": "Invalid API key."})
        with self.assertRaises(AuthenticationError):
            _client(rec).ping()


def _audit_event(ts, action="read", **kw):
    return dict({"ts": ts, "actor": "rest", "action": action, "result": "ok",
                 "user_id": "maria", "target_id": None, "meta": None, "ip": None,
                 "read": None}, **kw)


class TheAuditTrail(unittest.TestCase):
    """GET /v1/audit, on both products (the Cloud and the Self-hosted): the
    events of the key's project, newest first,
    `{events, total}`. The SDKs had no way to read it."""

    # Rows as Korely 1.0.23 answers them (api/audit.py AuditRow): the key of
    # each call, and a forget filed apart from an erasure.
    _ROWS_1_0_23 = [
        {"ts": "2026-11-14T15:00:00Z", "actor": "rest", "action": "forget", "result": "ok",
         "api_key_id": "6b1d4c0e-2f3a-4e5b-8c7d-9a0b1c2d3e4f", "user_id": "maria",
         "target_id": "mem_1", "meta": {"facts_invalidated": 1}, "ip": None, "read": None},
        {"ts": "2026-11-14T14:00:00Z", "actor": "rest", "action": "erase", "result": "ok",
         "api_key_id": "6b1d4c0e-2f3a-4e5b-8c7d-9a0b1c2d3e4f", "user_id": "maria",
         "target_id": "mem_2", "meta": {"erase": True}, "ip": None, "read": None},
        {"ts": "2026-11-14T13:00:00Z", "actor": "rest", "action": "fact_invalidate",
         "result": "ok", "api_key_id": "6b1d4c0e-2f3a-4e5b-8c7d-9a0b1c2d3e4f",
         "user_id": "maria", "target_id": "fct_1", "meta": None, "ip": None, "read": None},
        {"ts": "2026-11-14T12:00:00Z", "actor": "dashboard", "action": "key_create",
         "result": "ok", "api_key_id": None, "user_id": None, "target_id": None,
         "meta": None, "ip": None, "read": None, "a_field_of_a_later_server": {"x": 1}},
    ]

    def test_the_key_of_each_call_and_the_forget_of_1_0_23(self):
        """`api_key_id` was dropped (only declared fields are kept); a field
        a later server adds is still left out without an error."""
        rec = _Recorder().queue(200, {"events": self._ROWS_1_0_23, "total": 4})
        page = _client(rec).audit(action="forget")
        self.assertEqual(rec.last["params"], {"action": "forget", "limit": 100, "offset": 0})
        self.assertEqual([e.action for e in page], ["forget", "erase", "fact_invalidate",
                                                    "key_create"])
        self.assertEqual([e.api_key_id for e in page],
                         ["6b1d4c0e-2f3a-4e5b-8c7d-9a0b1c2d3e4f"] * 3 + [None])
        self.assertFalse(hasattr(page[3], "a_field_of_a_later_server"))
        older = _Recorder().queue(200, {"events": [_audit_event("2026-10-06T09:00:00Z")],
                                        "total": 1})
        self.assertIsNone(_client(older).audit()[0].api_key_id)

    def test_the_request_and_the_page(self):
        from korely_memory import AuditEvent, AuditPage, AuditRead

        rec = _Recorder().queue(200, {"events": [
            _audit_event("2026-10-06T10:00:00.123456Z", meta={"memories_read": 2, "facts_read": 1},
                         ip="203.0.113.7", read={"memories": ["mem_1", "mem_2"], "facts": ["fct_1"]}),
            _audit_event("2026-10-06T09:00:00Z", action="erase", target_id=None,
                         meta={"memories": 3}),
        ], "total": 57})
        page = _client(rec).audit(user_id="maria", action="read", since="2026-10-01",
                                  limit=2, offset=4)
        self.assertEqual((rec.last["method"], rec.last["path"]), ("GET", "/v1/audit"))
        self.assertEqual(rec.last["params"], {"user_id": "maria", "action": "read",
                                              "since": "2026-10-01", "limit": 2, "offset": 4})
        self.assertIsInstance(page, AuditPage)
        self.assertEqual((page.total, len(page)), (57, 2))
        first = page[0]
        self.assertIsInstance(first, AuditEvent)
        self.assertEqual((first.ts, first.actor, first.action, first.result),
                         ("2026-10-06T10:00:00.123456Z", "rest", "read", "ok"))
        self.assertEqual((first.user_id, first.ip), ("maria", "203.0.113.7"))
        self.assertEqual(first.meta, {"memories_read": 2, "facts_read": 1})
        self.assertIsInstance(first.read, AuditRead)
        self.assertEqual((first.read.memories, first.read.facts), (["mem_1", "mem_2"], ["fct_1"]))
        self.assertIsNone(page[1].read)
        self.assertEqual([e.action for e in page], ["read", "erase"])

    def test_the_defaults_are_the_api_s(self):
        rec = _Recorder().queue(200, {"events": [], "total": 0})
        _client(rec).audit()
        self.assertEqual(rec.last["params"], {"limit": 100, "offset": 0})

    def test_actor_and_action_are_open_strings(self):
        """The Self-hosted has `manage` and `tenant_create`, the Cloud
        `dashboard`, and a server may add one: whatever comes, comes through."""
        rec = _Recorder().queue(200, {"events": [
            _audit_event("2026-10-06T10:00:00Z", action="tenant_create", actor="manage"),
            _audit_event("2026-10-06T09:00:00Z", action="something_new", actor="dashboard"),
        ], "total": 2})
        page = _client(rec).audit(action="tenant_create")
        self.assertEqual([(e.actor, e.action) for e in page],
                         [("manage", "tenant_create"), ("dashboard", "something_new")])

    def test_dates_and_datetimes_are_sent_as_utc(self):
        from datetime import date, datetime, timedelta, timezone

        rec = _Recorder().queue(200, {"events": [], "total": 0})
        _client(rec).audit(since=datetime(2026, 10, 1, 8, 30),
                           until=datetime(2026, 10, 2, 8, 30, tzinfo=timezone(timedelta(hours=2))))
        self.assertEqual(rec.last["params"]["since"], "2026-10-01T08:30:00+00:00")
        self.assertEqual(rec.last["params"]["until"], "2026-10-02T08:30:00+02:00")
        rec.queue(200, {"events": [], "total": 0})
        _client(rec).audit(until=date(2026, 10, 1))
        self.assertEqual(rec.last["params"]["until"], "2026-10-01T00:00:00+00:00")

    def test_what_cannot_be_a_moment_never_leaves(self):
        rec = _Recorder()
        with self.assertRaises(KorelyError):
            _client(rec).audit(since=1696118400)
        self.assertEqual(rec.calls, [])

    def test_an_empty_filter_is_refused_not_read_as_every_user(self):
        """Both servers read `user_id=` as no filter: an id that came out of a
        variable empty answered with every end user's events."""
        for kw in ({"user_id": ""}, {"action": ""}):
            with self.subTest(**kw):
                rec = _Recorder()
                with self.assertRaises(KorelyError) as caught:
                    _client(rec).audit(**kw)
                self.assertIn("empty", str(caught.exception))
                self.assertEqual(rec.calls, [])
                with self.assertRaises(KorelyError):
                    list(_client(rec).iter_audit(**kw))
                self.assertEqual(rec.calls, [])

    def test_a_key_without_memories_read_is_refused(self):
        rec = _Recorder().queue(403, {"code": "forbidden",
                                      "message": "API key missing required scope(s): memories:read"})
        with self.assertRaises(NamespaceForbiddenError) as caught:
            _client(rec).audit()
        self.assertEqual(caught.exception.code, "forbidden")


class ExportingTheAuditTrail(unittest.TestCase):
    """iter_audit(): every page, offset += len(page) while offset < total."""

    def _pages(self, rec, *pages):
        for events, total in pages:
            rec.queue(200, {"events": events, "total": total})
        return rec

    def test_it_walks_every_page_and_stops_at_total(self):
        rec = self._pages(_Recorder(),
                          ([_audit_event("2026-10-06T10:00:00.5Z"), _audit_event("2026-10-06T09:00:00Z")], 5),
                          ([_audit_event("2026-10-06T08:00:00Z"), _audit_event("2026-10-06T07:00:00Z")], 5),
                          ([_audit_event("2026-10-06T06:00:00Z")], 5))
        events = list(_client(rec).iter_audit(user_id="maria", page_size=2))
        self.assertEqual([e.ts[11:13] for e in events], ["10", "09", "08", "07", "06"])
        self.assertEqual([c["params"]["offset"] for c in rec.calls], [0, 2, 4])
        self.assertEqual({c["params"]["limit"] for c in rec.calls}, {2})
        self.assertEqual({c["params"]["user_id"] for c in rec.calls}, {"maria"})

    def test_until_is_pinned_to_the_first_page_so_new_events_do_not_shift_the_rest(self):
        rec = self._pages(_Recorder(),
                          ([_audit_event("2026-10-06T10:00:00.123456Z")], 2),
                          ([_audit_event("2026-10-06T09:00:00Z")], 2))
        list(_client(rec).iter_audit(page_size=1))
        self.assertNotIn("until", rec.calls[0]["params"])
        self.assertEqual(rec.calls[1]["params"]["until"], "2026-10-06T10:00:00.123456Z")

    def test_an_until_given_is_kept(self):
        rec = self._pages(_Recorder(),
                          ([_audit_event("2026-10-05T10:00:00Z")], 2),
                          ([_audit_event("2026-10-05T09:00:00Z")], 2))
        list(_client(rec).iter_audit(until="2026-10-05T12:00:00Z", page_size=1))
        self.assertEqual([c["params"]["until"] for c in rec.calls],
                         ["2026-10-05T12:00:00Z", "2026-10-05T12:00:00Z"])

    def test_resuming_starts_at_the_offset_given(self):
        rec = self._pages(_Recorder(), ([_audit_event("2026-10-05T09:00:00Z")], 4))
        events = list(_client(rec).iter_audit(until="2026-10-05T12:00:00Z", offset=3))
        self.assertEqual(len(events), 1)
        self.assertEqual(rec.calls[0]["params"]["offset"], 3)
        self.assertEqual(rec.calls[0]["params"]["limit"], 1000)

    def test_an_empty_page_ends_the_walk_whatever_total_says(self):
        rec = self._pages(_Recorder(), ([_audit_event("2026-10-06T10:00:00Z")], 10), ([], 10))
        self.assertEqual(len(list(_client(rec).iter_audit(page_size=1))), 1)
        self.assertEqual(len(rec.calls), 2)

    def test_an_empty_trail_is_one_request(self):
        rec = self._pages(_Recorder(), ([], 0))
        self.assertEqual(list(_client(rec).iter_audit()), [])
        self.assertEqual(len(rec.calls), 1)

    def test_the_async_client_walks_the_same_pages(self):
        import asyncio
        from korely_memory import AsyncKorely

        rec = self._pages(_Recorder(),
                          ([_audit_event("2026-10-06T10:00:00Z")], 2),
                          ([_audit_event("2026-10-06T09:00:00Z")], 2))
        korely = AsyncKorely(api_key="kor_live_async_audit")
        korely._sync._send = rec

        async def export():
            return [e.ts async for e in korely.iter_audit(page_size=1)]

        self.assertEqual(asyncio.run(export()), ["2026-10-06T10:00:00Z", "2026-10-06T09:00:00Z"])
        self.assertEqual(rec.calls[1]["params"]["until"], "2026-10-06T10:00:00Z")


class FakeCloud:
    """DELETE /v1/account and GET /admin/whoami as a server answers them, at
    the client's transport seam. `confirms_with` "id": Korely 1.0.23 and
    later, ``?confirm=`` must be the account's id. "flag": before 1.0.23,
    ``confirm`` is a boolean query parameter (FastAPI), so the id is a 422 of
    validation and ``true`` deletes. ``whoami`` is the answer of GET
    /admin/whoami, the account's own by default."""

    ACCOUNT = "3f1c9a52-7b1e-4d4a-9c2e-0b6f5d8e1a77"
    OTHER = "9d2b7c41-0a6e-4f3b-8e1d-5c4a3b2f1e09"

    def __init__(self, confirms_with: str, whoami=None):
        self.confirms_with, self.calls, self.deleted = confirms_with, [], False
        self.whoami = whoami or (200, {"kind": "api_key", "name": "api key",
                                       "tenant_id": self.ACCOUNT})

    @property
    def deletes(self):
        return [c[2] for c in self.calls if c[0] == "DELETE"]

    def __call__(self, method, path, *, params=None, json_body=None, headers=None):
        self.calls.append((method, path, dict(params or {})))
        if (method, path) == ("GET", "/admin/whoami"):
            return self.whoami
        if (method, path) != ("DELETE", "/v1/account"):
            return 404, {"code": "not_found", "message": "Not Found"}
        confirm = (params or {}).get("confirm")
        if self.confirms_with == "id":
            ok = confirm == self.ACCOUNT
        else:
            words = {"true": True, "1": True, "yes": True, "on": True,
                     "false": False, "0": False, "no": False, "off": False}
            if confirm is not None and str(confirm).lower() not in words:
                return 422, {"detail": [{"type": "bool_parsing", "loc": ["query", "confirm"],
                                         "msg": "Input should be a valid boolean"}],
                             "code": "invalid_request",
                             "message": "query.confirm: Input should be a valid boolean"}
            ok = words.get(str(confirm).lower(), False)
        if not ok:
            return 400, {"code": "confirmation_required", "message": "Send ?confirm=..."}
        self.deleted = True
        return 200, {"deleted": True, "removed": {"memories": 12, "facts": 30, "keys": 1}}


class DeletingTheAccountOfAKey(unittest.TestCase):
    """DELETE /v1/account (Cloud only): an account made by `korely init
    --agent` has no login, its key is the account, and this is how it is
    closed. Since Korely 1.0.23 the deletion names the account, and since
    0.2.0 the caller types its id: the client reading it from GET
    /admin/whoami itself made a flag and the key enough again."""

    _TYPED = f'To delete account {FakeCloud.ACCOUNT}, pass confirm="{FakeCloud.ACCOUNT}".'

    def _client(self, cloud):
        k = Korely(api_key="kor_live_test")
        k._send = cloud
        return k

    def test_the_account_goes_with_its_id_typed(self):
        from korely_memory import AccountDeleteReceipt

        for typed in (FakeCloud.ACCOUNT, f"  {FakeCloud.ACCOUNT}\n"):
            with self.subTest(typed=typed):
                cloud = FakeCloud("id")
                r = self._client(cloud).delete_account(confirm=typed)
                self.assertEqual(cloud.calls, [
                    ("GET", "/admin/whoami", {}),
                    ("DELETE", "/v1/account", {"confirm": FakeCloud.ACCOUNT})])
                self.assertTrue(cloud.deleted)
                self.assertIsInstance(r, AccountDeleteReceipt)
                self.assertIs(r.deleted, True)
                self.assertEqual(r.removed, {"memories": 12, "facts": 30, "keys": 1})

    def test_no_flag_and_no_other_id_sends_the_delete(self):
        """Missing, a flag, a word, not an id, an id in another spelling, the
        id of another account: the DELETE never leaves, on either server, and
        the error names the id to type."""
        import uuid

        acc = FakeCloud.ACCOUNT
        for kw in ({}, {"confirm": None}, {"confirm": False}, {"confirm": True},
                   {"confirm": "true"}, {"confirm": "True"}, {"confirm": "1"}, {"confirm": 1},
                   {"confirm": "yes"}, {"confirm": ""}, {"confirm": "   "},
                   {"confirm": "not-an-account"}, {"confirm": acc.replace("-", "")},
                   {"confirm": "{" + acc + "}"}, {"confirm": "urn:uuid:" + acc},
                   {"confirm": acc.upper()}, {"confirm": uuid.UUID(acc)},
                   {"confirm": FakeCloud.OTHER}):
            for server in ("id", "flag"):
                with self.subTest(server=server, **{k: repr(v) for k, v in kw.items()}):
                    cloud = FakeCloud(server)
                    with self.assertRaises(KorelyError) as caught:
                        self._client(cloud).delete_account(**kw)
                    self.assertNotIsInstance(caught.exception, APIError)
                    self.assertEqual(caught.exception.code, "confirmation_required")
                    self.assertIn("nothing was deleted", str(caught.exception).lower())
                    self.assertIn(self._TYPED, str(caught.exception))
                    self.assertEqual(cloud.deletes, [])
                    self.assertFalse(cloud.deleted)

    def test_the_id_of_another_account_says_whose_the_key_is(self):
        cloud = FakeCloud("id")
        with self.assertRaises(KorelyError) as caught:
            self._client(cloud).delete_account(confirm=FakeCloud.OTHER)
        self.assertIn(f"The account of this key is {FakeCloud.ACCOUNT}, and the confirmation "
                      f"names {FakeCloud.OTHER}", str(caught.exception))
        self.assertEqual(cloud.calls, [("GET", "/admin/whoami", {})])

    def test_a_whoami_with_no_uuid_never_becomes_the_confirmation(self):
        """A tenant_id of "true" sent as the id deletes on a server before
        1.0.23: nothing that is not a UUID leaves, whatever was typed."""
        for whoami in ((200, {"tenant_id": "true"}), (200, {"tenant_id": "1"}),
                       (200, {"tenant_id": "  "}), (200, {"tenant_id": 123}),
                       (200, {"tenant_id": [FakeCloud.ACCOUNT]}), (200, {"kind": "api_key"}),
                       (200, [FakeCloud.ACCOUNT]), (200, {})):
            for typed in (True, "true", FakeCloud.ACCOUNT, None):
                with self.subTest(whoami=whoami, typed=typed):
                    cloud = FakeCloud("flag", whoami=whoami)
                    with self.assertRaises(KorelyError) as caught:
                        self._client(cloud).delete_account(confirm=typed)
                    self.assertEqual(caught.exception.code, "confirmation_required")
                    self.assertEqual(cloud.deletes, [])
                    self.assertFalse(cloud.deleted)

    def test_a_key_that_may_not_read_its_account_deletes_nothing(self):
        refused = (403, {"code": "forbidden", "message": "needs the admin scope"})
        cloud = FakeCloud("id", whoami=refused)
        with self.assertRaises(NamespaceForbiddenError) as caught:
            self._client(cloud).delete_account(confirm=FakeCloud.ACCOUNT)
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(cloud.calls, [("GET", "/admin/whoami", {})])
        # Without the id the refusal is the confirmation's, and says why it
        # cannot name the id.
        cloud = FakeCloud("id", whoami=refused)
        with self.assertRaises(KorelyError) as caught:
            self._client(cloud).delete_account(confirm=True)
        self.assertEqual(caught.exception.code, "confirmation_required")
        self.assertIn("could not read: needs the admin scope", str(caught.exception))
        self.assertEqual(cloud.deletes, [])

    def test_only_the_answer_deleted_true_is_a_deletion(self):
        """A redirect, or a 2xx without `deleted: true`, is not the server
        saying the account is gone (a proxy's 307 was a deletion)."""
        for status, body in ((301, {}), (302, {}), (303, {}), (307, {}), (308, {}),
                             (200, {}), (202, {}), (204, {}), (200, {"deleted": False}),
                             (200, {"deleted": "true"}), (200, {"removed": {"keys": 1}})):
            with self.subTest(status=status, body=body):
                rec = _Recorder().queue(*self._WHOAMI).queue(status, body)
                with self.assertRaises(KorelyError) as caught:
                    self._client(rec).delete_account(confirm=FakeCloud.ACCOUNT)
                if status >= 300:
                    self.assertIsInstance(caught.exception, APIError)
                    self.assertEqual(caught.exception.status, status)
                    self.assertIn("redirect this client does not follow", str(caught.exception))
                else:
                    self.assertIn("without `deleted: true`", str(caught.exception))
                self.assertEqual([c["method"] for c in rec.calls], ["GET", "DELETE"])

    def test_every_call_takes_only_a_2xx_as_its_answer(self):
        rec = _Recorder().queue(307, {"id": "mem_1"})
        with self.assertRaises(APIError) as caught:
            self._client(rec).delete("mem_1")
        self.assertEqual(caught.exception.status, 307)

    def test_a_server_before_1_0_23_is_told_to_upgrade_never_sent_the_flag(self):
        """It would delete with ?confirm=true; the client never sends it."""
        import asyncio

        from korely_memory import AsyncKorely

        cloud = FakeCloud("flag")
        with self.assertRaises(APIError) as caught:
            self._client(cloud).delete_account(confirm=FakeCloud.ACCOUNT)
        e = caught.exception
        self.assertEqual((e.status, e.code), (422, "server_too_old"))
        for words in ("1.0.23", "korely-memory 0.1.29", "Nothing was deleted"):
            self.assertIn(words, str(e))
        self.assertFalse(cloud.deleted)
        self.assertEqual(cloud.deletes, [{"confirm": FakeCloud.ACCOUNT}])
        ak = AsyncKorely(api_key="kor_live_test")
        ak._sync._send = cloud = FakeCloud("flag")
        with self.assertRaises(APIError) as caught:
            asyncio.run(ak.delete_account(confirm=FakeCloud.ACCOUNT))
        self.assertEqual(caught.exception.code, "server_too_old")
        self.assertFalse(cloud.deleted)

    def test_the_async_client_types_the_id_too(self):
        import asyncio

        from korely_memory import AsyncKorely

        ak = AsyncKorely(api_key="kor_live_test")
        ak._sync._send = cloud = FakeCloud("id")
        with self.assertRaises(KorelyError) as caught:
            asyncio.run(ak.delete_account(confirm=True))
        self.assertEqual(caught.exception.code, "confirmation_required")
        self.assertEqual(cloud.deletes, [])
        r = asyncio.run(ak.delete_account(confirm=FakeCloud.ACCOUNT))
        self.assertIs(r.deleted, True)
        self.assertTrue(cloud.deleted)

    _WHOAMI = (200, {"kind": "api_key", "name": "api key", "tenant_id": FakeCloud.ACCOUNT})

    def test_an_account_with_a_login_is_a_conflict(self):
        from korely_memory import ConflictError

        rec = _Recorder().queue(*self._WHOAMI).queue(409, {
            "code": "account_has_login",
            "message": "This key belongs to an account with a Korely login."})
        with self.assertRaises(ConflictError) as caught:
            _client(rec).delete_account(confirm=FakeCloud.ACCOUNT)
        self.assertNotIsInstance(caught.exception, StaleWriteError)
        self.assertEqual(caught.exception.code, "account_has_login")

    def test_the_server_s_own_refusal_keeps_its_code(self):
        rec = _Recorder().queue(*self._WHOAMI).queue(400, {
            "code": "confirmation_required", "message": "Send ?confirm=<this account's id>."})
        with self.assertRaises(APIError) as caught:
            _client(rec).delete_account(confirm=FakeCloud.ACCOUNT)
        self.assertEqual((caught.exception.status, caught.exception.code),
                         (400, "confirmation_required"))

    def test_the_self_hosted_has_no_such_route(self):
        """No account to read and no route to delete it: 404 (405 where it
        serves its dashboard), before anything is deleted."""
        rec = _Recorder().queue(404, {"detail": "Not Found", "code": "not_found",
                                      "message": "Not Found"})
        with self.assertRaises(NotFoundError):
            _client(rec).delete_account(confirm=FakeCloud.ACCOUNT)
        rec.queue(*self._WHOAMI).queue(405, {"detail": "Method Not Allowed",
                                             "code": "method_not_allowed",
                                             "message": "Method Not Allowed"})
        with self.assertRaises(APIError) as caught:
            _client(rec).delete_account(confirm=FakeCloud.ACCOUNT)
        self.assertEqual(caught.exception.code, "method_not_allowed")


class _Server:
    """A real HTTP server on 127.0.0.1, in this process: each request is
    recorded (method, path, headers) and answered by ``answer(method, path)``
    -> (status, headers, body)."""

    def __init__(self, answer):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.seen = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                server.seen.append((self.command, self.path, dict(self.headers.items())))
                status, headers, body = answer(self.command, self.path)
                data = json.dumps(body).encode("utf-8") if body is not None else b""
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_DELETE = do_PATCH = do_PUT = _answer

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()


class TheKeyGoesOnlyToItsServer(unittest.TestCase):
    """urllib copies a request's headers onto the request a redirect makes,
    to any host: a GET answered 302 by the server sent the key to wherever
    the redirect pointed. Two real servers, on two ports (two origins)."""

    KEY = "kor_self_redirect_probe_0123456789"

    def setUp(self):
        from unittest import mock

        # Straight to 127.0.0.1, whatever proxy the machine sets.
        patcher = mock.patch.dict(os.environ, {"no_proxy": "*", "NO_PROXY": "*"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.other = _Server(lambda m, p: (200, {}, {
            "ok": True, "tier": "pro", "region": "elsewhere", "scopes": [],
            "id": "mem_1", "tenant_id": FakeCloud.OTHER}))
        self.addCleanup(self.other.close)

    def _ours(self, answer):
        s = _Server(answer)
        self.addCleanup(s.close)
        return s, Korely(api_key=self.KEY, base_url=s.url)

    def _the_key_in(self, seen):
        return [(m, p) for m, p, headers in seen if any(self.KEY in v for v in headers.values())]

    def test_a_redirect_to_another_server_goes_without_the_key(self):
        import urllib.request

        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.other.seen.clear()
                ours, k = self._ours(lambda m, p: (status, {"Location": self.other.url + p}, {}))
                if hasattr(urllib.request.HTTPRedirectHandler, f"http_error_{status}"):
                    self.assertTrue(k.ping().ok)
                    self.assertEqual([(m, p) for m, p, _ in self.other.seen],
                                     [("GET", "/v1/ping")])
                    self.assertNotIn("Authorization", self.other.seen[0][2])
                else:  # 308 before Python 3.11: not followed, so not an answer
                    with self.assertRaises(APIError) as caught:
                        k.ping()
                    self.assertEqual(caught.exception.status, status)
                    self.assertEqual(self.other.seen, [])
                self.assertEqual(ours.seen[0][2].get("Authorization"), "Bearer " + self.KEY)
                self.assertEqual(self._the_key_in(self.other.seen), [])

    def test_a_call_that_is_not_a_get_is_never_redirected(self):
        """urllib follows a POST answered 301, 302 or 303 as a GET with no
        body, and that GET's answer came back as the POST's: add() said
        stored from another address's reply. Every redirect of a POST, a
        PATCH or a DELETE is now its answer: an APIError naming the status
        and where it pointed, and nothing reaches the other server."""
        calls = {
            "POST": lambda k: k.add("hello", user_id="dana"),
            "PATCH": lambda k: k.update("mem_1", content="hello"),
            "DELETE": lambda k: k.delete("mem_1"),
        }
        for method, call in calls.items():
            for status in (301, 302, 303, 307, 308):
                with self.subTest(method=method, status=status):
                    self.other.seen.clear()
                    ours, k = self._ours(
                        lambda m, p: (status, {"Location": self.other.url + "/x?token=t"}, {}))
                    with self.assertRaises(APIError) as caught:
                        call(k)
                    e = caught.exception
                    self.assertEqual(e.status, status)
                    self.assertIn(f"redirect to {self.other.url} this client does not follow",
                                  str(e))
                    self.assertNotIn("token", str(e))
                    self.assertEqual(e.body, {})
                    self.assertEqual([m for m, _, _ in ours.seen], [method])
                    self.assertEqual(self.other.seen, [])

    def test_a_redirect_on_the_same_server_goes_without_the_key_as_well(self):
        """The key goes to the address the client was given, never to a
        redirect's: the other path answers 401, an error, not a leak."""
        def answer(m, p):
            if p == "/v1/ping":
                return 302, {"Location": "/v1/elsewhere"}, {}
            return 401, {}, {"code": "invalid_key", "message": "Missing key."}

        ours, k = self._ours(answer)
        with self.assertRaises(AuthenticationError) as caught:
            k.ping()
        self.assertEqual([p for _, p, _ in ours.seen], ["/v1/ping", "/v1/elsewhere"])
        self.assertEqual(self._the_key_in(ours.seen), [("GET", "/v1/ping")])
        # Same origin: no hint to change base_url, which is that address.
        self.assertEqual(str(caught.exception), "Missing key.")

    def test_a_get_redirect_urllib_does_not_follow_says_so(self):
        """A 300 (and a 308 before Python 3.11) of a GET is not followed: the
        error says it was a GET and which redirects urllib follows, not that
        a call other than a GET is never redirected."""
        _, k = self._ours(lambda m, p: (300, {"Location": self.other.url + "/v1/ping"}, {}))
        with self.assertRaises(APIError) as caught:
            k.ping()
        e = caught.exception
        self.assertEqual(e.status, 300)
        self.assertEqual(str(e), f"The server answered 300 to a GET, a redirect to {self.other.url} "
                                 "that urllib does not follow (it follows 301, 302, 303 and 307, "
                                 "and 308 from Python 3.11): that is not the answer to the call.")
        self.assertEqual(self.other.seen, [])

    def test_a_401_after_a_redirect_says_so(self):
        """The key stayed behind: the 401 says where the call went and what
        to set, and a 401 with no redirect says nothing of the kind."""
        refusing = _Server(lambda m, p: (401, {}, {"code": "invalid_key",
                                                    "message": "Missing key."}))
        self.addCleanup(refusing.close)
        _, k = self._ours(lambda m, p: (302, {"Location": refusing.url + p}, {}))
        with self.assertRaises(AuthenticationError) as caught:
            k.ping()
        e = caught.exception
        self.assertEqual((e.status, e.code), (401, "invalid_key"))
        self.assertEqual(str(e), f"Missing key. The call was redirected to {refusing.url}, and "
                                 "the key is never sent after a redirect: if that is the "
                                 "server, use that address as base_url.")
        self.assertEqual(e.body, {"code": "invalid_key", "message": "Missing key."})
        with self.assertRaises(AuthenticationError) as caught:
            Korely(api_key=self.KEY, base_url=refusing.url).ping()
        self.assertEqual(str(caught.exception), "Missing key.")

    def test_a_delete_answered_with_a_redirect_is_no_deletion(self):
        def answer(m, p):
            if p == "/admin/whoami":
                return 200, {}, {"tenant_id": FakeCloud.ACCOUNT}
            return 307, {"Location": self.other.url + p}, {}

        ours, k = self._ours(answer)
        with self.assertRaises(APIError) as caught:
            k.delete_account(confirm=FakeCloud.ACCOUNT)
        self.assertEqual(caught.exception.status, 307)
        self.assertEqual([m for m, _, _ in ours.seen], ["GET", "DELETE"])
        self.assertEqual(self.other.seen, [])


class SigningUpWithoutAKey(unittest.TestCase):
    """POST /v1/agents/init (Cloud only, the signup of Korely's server):
    the one call with no key, since it is how one is obtained. The SDKs had no
    way to make it; only `korely init` did, with its own copy of the HTTP."""

    _MINTED = {"api_key": "kor_live_0123456789abcdef0123456789abcdef", "tier": "hobby",
               "region": "eu-hel1", "scopes": ["memories:read", "memories:write"],
               "quotas": {"writes_per_month": 2000, "queries_per_month": 10000, "agents": 2}}

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("KORELY_API_KEY", "KORELY_BASE_URL", "KORELY_CONFIG_HOME")}
        for k in self._saved:
            os.environ.pop(k, None)
        os.environ["KORELY_CONFIG_HOME"] = "/nonexistent-korely-config-dir"
        self.seen = []

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _answering(self, status=201, body=None):
        from unittest import mock

        def fake(req, timeout):
            self.seen.append(req)
            return _FakeResponse(status=status, body=json.dumps(body or self._MINTED).encode())
        return mock.patch("korely_memory.client._open", fake)

    def test_no_key_is_needed_and_none_is_sent(self):
        from korely_memory import AgentInitResult

        with self._answering():
            r = Korely.init_agent("claude-code")
        req = self.seen[0]
        self.assertEqual((req.get_method(), req.full_url),
                         ("POST", "https://api.korely.ai/v1/agents/init"))
        self.assertIsNone(req.get_header("Authorization"))
        self.assertEqual(json.loads(req.data), {"agent_caller": "claude-code"})
        self.assertIsInstance(r, AgentInitResult)
        self.assertEqual((r.api_key, r.tier, r.region), (self._MINTED["api_key"], "hobby", "eu-hel1"))
        self.assertEqual(r.quotas["agents"], 2)
        self.assertEqual((r.expires_at, r.note), (None, None), "a server older than 1.0.10")

    def test_the_key_s_end_and_the_trial_are_kept(self):
        """The 201 of Korely Agent 1.0.10 and later: when the key stops working
        (`expires_at`) and the trial in words (`note`). Dropped by the model,
        `korely init --agent` could say neither (15 Nov 2026)."""
        answer = dict(self._MINTED, expires_at="2026-11-22T18:00:00Z",
                      note="This key is valid for 7 days (expires_at).")
        with self._answering(body=answer):
            r = Korely.init_agent("claude-code")
        self.assertEqual((r.expires_at, r.note), (answer["expires_at"], answer["note"]))

    def test_the_processing_region_is_sent_only_when_chosen(self):
        """2026-10-07: where the new project's memories are read by a model."""
        minted = dict(self._MINTED, processing_region="eu")
        with self._answering(body=minted):
            r = Korely.init_agent("app", processing_region="eu")
        self.assertEqual(json.loads(self.seen[0].data),
                         {"agent_caller": "app", "processing_region": "eu"})
        self.assertEqual(r.processing_region, "eu")
        with self.assertRaises(KorelyError):
            Korely.init_agent("app", processing_region="us")
        self.assertEqual(len(self.seen), 1, "a wrong region is refused before sending")

    def test_the_key_stays_out_of_repr(self):
        with self._answering():
            r = Korely.init_agent()
        self.assertNotIn(self._MINTED["api_key"], repr(r))
        self.assertEqual(json.loads(self.seen[0].data), {})

    def test_the_server_is_the_argument_then_the_environment(self):
        os.environ["KORELY_BASE_URL"] = "http://localhost:8000/"
        with self._answering():
            Korely.init_agent()
            Korely.init_agent(base_url="https://staging.example/")
        self.assertEqual([r.full_url for r in self.seen],
                         ["http://localhost:8000/v1/agents/init",
                          "https://staging.example/v1/agents/init"])

    def test_the_refusals_keep_their_code(self):
        rec = _Recorder().queue(403, {"code": "signup_disabled",
                                      "message": "Agent self-signup is currently closed."})
        rec.queue(429, {"code": "signup_rate_limited", "message": "Try again tomorrow.",
                        "_retry_after": "86400"})
        from unittest import mock
        with mock.patch.object(Korely, "_send", rec):
            with self.assertRaises(NamespaceForbiddenError) as closed:
                Korely.init_agent("x")
            with self.assertRaises(QuotaExceededError) as capped:
                Korely.init_agent("x")
        self.assertEqual(closed.exception.code, "signup_disabled")
        self.assertEqual((capped.exception.code, capped.exception.retry_after),
                         ("signup_rate_limited", 86400))
        self.assertEqual(rec.calls[0]["json"], {"agent_caller": "x"})

    def test_a_label_that_is_not_text_never_leaves(self):
        with self._answering():
            with self.assertRaises(KorelyError):
                Korely.init_agent({"base_url": "https://x.example"})
        self.assertEqual(self.seen, [])

    def test_the_async_client_signs_up_too(self):
        import asyncio
        from korely_memory import AsyncKorely

        with self._answering():
            r = asyncio.run(AsyncKorely.init_agent("claude-code"))
        self.assertEqual(r.tier, "hobby")
        self.assertIsNone(self.seen[0].get_header("Authorization"))

    def test_class_methods_are_mirrored_too(self):
        """The parity test lists functions; a class method is not one, so it
        checks this separately."""
        import inspect
        from korely_memory import AsyncKorely

        sync = {n for n, m in inspect.getmembers(Korely, inspect.ismethod) if not n.startswith("_")}
        self.assertIn("init_agent", sync)
        for name in sync:
            with self.subTest(method=name):
                self.assertTrue(inspect.iscoroutinefunction(getattr(AsyncKorely, name, None)))


class TheContextInTwoParts(unittest.TestCase):
    """/v1/context sends `stable`, `volatile`, `stable_hash`, `degraded` and
    `degraded_parts` on both products; the JS client typed them in 0.1.8 and
    this one dropped them on the floor (`_take` keeps declared fields only)."""

    _BODY = {"context": "Note.\n\n## Known facts\n- maria lives_in Milan", "tokens": 18,
             "sources": ["fct_1"], "degraded": True, "degraded_parts": ["memories"],
             "stable": "Note.", "volatile": "## Known facts\n- maria lives_in Milan",
             "stable_hash": "a" * 64}

    def test_every_field_arrives(self):
        rec = _Recorder().queue(200, dict(self._BODY))
        ctx = _client(rec).get_context("where does maria live?", user_id="maria")
        self.assertEqual((ctx.stable, ctx.volatile, ctx.stable_hash),
                         ("Note.", "## Known facts\n- maria lives_in Milan", "a" * 64))
        self.assertIs(ctx.degraded, True)
        self.assertEqual(ctx.degraded_parts, ["memories"])
        self.assertEqual(ctx.context, ctx.stable + "\n\n" + ctx.volatile)

    def test_an_older_server_leaves_them_unset(self):
        ctx = Context.from_dict({"context": "c", "tokens": 1, "sources": []})
        self.assertIsNone(ctx.degraded)
        self.assertEqual(ctx.degraded_parts, [])
        self.assertIsNone(ctx.stable)
        self.assertIsNone(ctx.volatile)
        self.assertIsNone(ctx.stable_hash)

    def test_positional_construction_means_what_it_meant(self):
        ctx = Context("c", 3, ["fct_1"])
        self.assertEqual((ctx.context, ctx.tokens, ctx.sources), ("c", 3, ["fct_1"]))

    def test_the_cli_json_carries_them(self):
        import io
        from contextlib import redirect_stdout
        from korely_memory import cli

        rec = _Recorder().queue(200, dict(self._BODY))
        out = io.StringIO()
        with redirect_stdout(out):
            cli.cmd_context(_client(rec), cli.build_parser().parse_args(
                ["context", "where?", "--json"]))
        data = json.loads(out.getvalue())
        self.assertEqual((data["stable_hash"], data["degraded_parts"]), ("a" * 64, ["memories"]))


class EventsAndForgetAreTyped(unittest.TestCase):
    """events() and forget_fact() returned bare dicts, the only two methods
    without a model. They are typed now, and still the dicts they were."""

    def test_events_has_typed_attributes(self):
        from korely_memory import EventsResponse, MemoryEvent

        rec = _Recorder().queue(200, {"events": [
            {"memory_id": "mem_1", "user_id": "maria", "agent_id": None,
             "status": "processing", "created_at": "2026-10-06T10:00:00+00:00"},
            {"memory_id": "mem_0", "user_id": "maria", "agent_id": "bot",
             "status": "ready", "created_at": "2026-10-06T09:00:00+00:00"},
        ], "processing": 3})
        r = _client(rec).events(user_id="maria")
        self.assertIsInstance(r, EventsResponse)
        self.assertEqual(r.processing, 3)
        self.assertIsInstance(r.events[0], MemoryEvent)
        self.assertEqual([(e.memory_id, e.status) for e in r.events],
                         [("mem_1", "processing"), ("mem_0", "ready")])
        self.assertEqual(r.events[1].agent_id, "bot")

    def test_events_is_still_the_dict_it_was(self):
        body = {"events": [{"memory_id": "mem_1", "status": "ready"}], "processing": 0}
        rec = _Recorder().queue(200, dict(body))
        r = _client(rec).events()
        self.assertIsInstance(r, dict)
        self.assertEqual(r["processing"], 0)
        self.assertEqual(r["events"][0]["status"], "ready")
        self.assertEqual(json.loads(json.dumps(r)), body)

    def test_an_empty_answer_is_no_events_and_nothing_processing(self):
        from korely_memory import EventsResponse

        r = EventsResponse.from_dict({})
        self.assertEqual((r.events, r.processing), ([], 0))

    def test_forget_fact_is_typed_and_still_a_dict(self):
        from korely_memory import ForgetReceipt

        body = {"id": "fct_1", "status": "already_forgotten",
                "invalid_at": "2026-09-30T00:00:00+00:00", "audit_id": "aud_9"}
        rec = _Recorder().queue(200, dict(body))
        r = _client(rec).forget_fact("fct_1", at="2026-09-30")
        self.assertEqual(rec.last["json"], {"at": "2026-09-30"})
        self.assertIsInstance(r, ForgetReceipt)
        self.assertEqual((r.id, r.status, r.invalid_at, r.audit_id),
                         ("fct_1", "already_forgotten", "2026-09-30T00:00:00+00:00", "aud_9"))
        self.assertEqual(r["status"], "already_forgotten")
        self.assertEqual(dict(r), body)

    def test_the_async_client_returns_the_same_types(self):
        import asyncio
        from korely_memory import AsyncKorely, EventsResponse, ForgetReceipt

        rec = (_Recorder().queue(200, {"events": [], "processing": 1})
               .queue(200, {"id": "fct_1", "status": "forgotten"}))
        korely = AsyncKorely(api_key="kor_live_async_types")
        korely._sync._send = rec

        async def both():
            return await korely.events(), await korely.forget_fact("fct_1")

        ev, fo = asyncio.run(both())
        self.assertIsInstance(ev, EventsResponse)
        self.assertIsInstance(fo, ForgetReceipt)
        self.assertEqual((ev.processing, fo.status), (1, "forgotten"))


class AWriteCarriesItsIdempotencyKey(unittest.TestCase):
    """A write whose answer never came may have been made (a timeout while a
    proxy held it through a restart of the server, which then made it), and
    sent again without a key it is made twice. With `idempotency_key` the
    server makes it once (Korely Agent 1.0.27). On the wire, to a real server:
    the header as draft-ietf-httpapi-idempotency-key-header-08 section 2.1
    wants it, a quoted String of RFC 9651."""

    def setUp(self):
        from unittest import mock

        patcher = mock.patch.dict(os.environ, {"no_proxy": "*", "NO_PROXY": "*"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server = _Server(lambda method, path: (
            201 if path == "/v1/memories" else 202, {},
            {"id": "mem_1", "content": "x"} if path == "/v1/memories" else {"job_id": "job_1"}))
        self.addCleanup(self.server.close)
        self.k = Korely(api_key="kor_self_test", base_url=self.server.url)

    def _key_sent(self):
        return {k.lower(): v for k, v in self.server.seen[-1][2].items()}.get("idempotency-key")

    def test_add_sends_the_key_it_is_given(self):
        self.k.add("Ada prefers tea.", user_id="ada", idempotency_key="3f2c9a")
        self.assertEqual(self._key_sent(), '"3f2c9a"')

    def test_batch_sends_the_key_it_is_given(self):
        self.k.batch([{"content": "x", "user_id": "ada"}], idempotency_key="b-1")
        self.assertEqual(self._key_sent(), '"b-1"')

    def test_without_a_key_each_write_carries_a_new_one(self):
        """0.2.2: a write the caller gives no key gets one of its own, so the
        client's own retry (and the caller's, with the key the error names)
        is made once. A new one per write: two writes are two memories."""
        keys = []
        for write in (lambda: self.k.add("Ada prefers tea.", user_id="ada"),
                      lambda: self.k.add("Ada prefers tea.", user_id="ada"),
                      lambda: self.k.batch([{"content": "x", "user_id": "ada"}])):
            write()
            keys.append(self._key_sent())
        for key in keys:
            self.assertRegex(key, r'^"[0-9a-f]{32}"$')
        self.assertEqual(len(set(keys)), 3)

    def test_a_quote_or_a_backslash_is_escaped(self):
        self.k.add("x", user_id="ada", idempotency_key='a"b\\c')
        self.assertEqual(self._key_sent(), '"a\\"b\\\\c"')


class ABadIdempotencyKeyIsRefusedBeforeTheCall(unittest.TestCase):
    """1 to 255 printable ASCII characters, what the server takes in a quoted
    String. A line break reached http.client, which raised a bare ValueError;
    every other failure of a call is a KorelyError. Nothing is sent."""

    def test_each_bad_key_is_a_korely_error_and_nothing_is_sent(self):
        for bad in ("", "a" * 256, "a\nb", "a\rb", "café", "tab\there", 42):
            rec = _Recorder().queue(201, {"id": "mem_1"})
            k = _client(rec)
            with self.assertRaises(KorelyError, msg=repr(bad)):
                k.add("x", user_id="ada", idempotency_key=bad)
            with self.assertRaises(KorelyError, msg=repr(bad)):
                k.batch([{"content": "x", "user_id": "ada"}], idempotency_key=bad)
            self.assertEqual(rec.calls, [], repr(bad))

    def test_the_edges_are_taken(self):
        rec = _Recorder()
        k = _client(rec)
        for good in ("a", "~" * 255, " spaced key ", "3f2c9a4e-0d1b-4c2a-9f7e-8b6d5c4a3b2a"):
            rec.queue(201, {"id": "mem_1"})
            k.add("x", user_id="ada", idempotency_key=good)
        self.assertEqual(len(rec.calls), 4)


class _KeyedServer:
    """A real HTTP server on 127.0.0.1 that honours Idempotency-Key as Korely
    1.0.27 and later do (the server's services/idempotency.py): the first
    write with a key makes it, the same key again answers what the first made
    (`Idempotent-Replayed: true`) and makes nothing.

    ``fate(n)`` says what happens to the n-th request (1-based): "answer",
    "lose" (made, then the connection closes with no answer: the answer lost
    on the way back), "hang" (no answer for longer than the client waits),
    "in_use" (the 409 of a key whose first request is still being made) or
    an int, a status answered with nothing made."""

    def __init__(self, fate=lambda n: "answer"):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.seen = []      # (method, path, Idempotency-Key)
        self.made = {}      # key -> id of what it made
        self.writes = 0     # memories or jobs made
        server = self
        lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, status, body, headers=()):
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                key = self.headers.get("Idempotency-Key")
                with lock:
                    server.seen.append((self.command, self.path, key))
                    fate = server._fate(len(server.seen))
                if isinstance(fate, int):
                    return self._reply(fate, {"code": "unavailable", "message": "down"})
                if fate == "hang":
                    threading.Event().wait(1.0)
                    self.close_connection = True
                    return
                if fate == "in_use":
                    return self._reply(409, {
                        "code": "idempotency_key_in_use",
                        "message": "A request with this Idempotency-Key is still being "
                                   "made; retry it as it is."})
                replayed = []
                if self.command == "POST":
                    with lock:
                        if key and key in server.made:
                            made = server.made[key]
                            replayed = [("Idempotent-Replayed", "true")]
                        else:
                            server.writes += 1
                            made = f"mem_{server.writes}"
                            if key:
                                server.made[key] = made
                    body = ({"id": made, "content": "x", "status": "processing"}
                            if self.path == "/v1/memories" else
                            {"id": made, "status": "queued", "received": 1})
                    status = 201 if self.path == "/v1/memories" else 202
                else:
                    status, body = 200, {"context": "", "tokens": 0, "sources": []}
                if fate == "lose":
                    self.close_connection = True
                    return
                self._reply(status, body, replayed)

            do_GET = do_POST = do_DELETE = _answer

        self._fate = fate
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()

    def posts(self):
        return [s for s in self.seen if s[0] == "POST"]

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()


class TheRetryWaitsHalfASecond(unittest.TestCase):
    def test_one_pause_before_the_retry_and_the_same_header(self):
        from unittest import mock

        from korely_memory import client as client_module

        tries = []

        def send(method, path, *, params=None, json_body=None, headers=None):
            tries.append(headers)
            if len(tries) == 1:
                raise KorelyError("Connection error: timed out", code="connection_error")
            return 201, {"id": "mem_1"}

        k = Korely(api_key="kor_live_test")
        k._send = send
        with mock.patch.object(client_module.time, "sleep") as slept:
            self.assertEqual(k.add("x", user_id="ada").id, "mem_1")
        slept.assert_called_once_with(0.5)
        self.assertEqual(len(tries), 2)
        self.assertEqual(tries[0], tries[1])


class AWriteWithNoAnswerIsSentOnceMoreWithItsKey(unittest.TestCase):
    """0.2.2. A write whose answer never came (a timeout, a connection closed
    before the answer) may have been made: the client sends it once more,
    after a short pause, with the same Idempotency-Key, which Korely 1.0.27
    and later make once. Never more than once, never a read, never a call
    the server answered. If the second try gets no answer either, the error
    says the write may have been made and names the key to send it again
    with. The Stainless SDKs do the same for every call that is not a GET
    (openai-python 3.58, _base_client.py: one key made before the first try,
    the same on each retry)."""

    def setUp(self):
        from unittest import mock

        from korely_memory import client as client_module

        patcher = mock.patch.dict(os.environ, {"no_proxy": "*", "NO_PROXY": "*"})
        patcher.start()
        self.addCleanup(patcher.stop)
        # The pause before the retry, at 0: what it is is not what these test.
        pause = mock.patch.object(client_module, "WRITE_RETRY_DELAY", 0)
        pause.start()
        self.addCleanup(pause.stop)

    def _server(self, fate):
        server = _KeyedServer(fate)
        self.addCleanup(server.close)
        return server

    def _korely(self, server, **kw):
        return Korely(api_key="kor_self_test", base_url=server.url, timeout=0.3, **kw)

    def test_an_answer_lost_after_the_write_is_one_memory_and_no_error(self):
        server = self._server(lambda n: "lose" if n == 1 else "answer")
        memory = self._korely(server).add("Ada prefers tea.", user_id="ada")
        self.assertEqual(memory.id, "mem_1")
        self.assertEqual(server.writes, 1)
        keys = [key for _, _, key in server.posts()]
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1])

    def test_a_batch_too(self):
        server = self._server(lambda n: "lose" if n == 1 else "answer")
        job = self._korely(server).batch([{"content": "x", "user_id": "ada"}])
        self.assertEqual((job.id, server.writes, len(server.posts())), ("mem_1", 1, 2))

    def test_and_from_the_async_client(self):
        import asyncio

        from korely_memory import AsyncKorely

        server = self._server(lambda n: "lose" if n == 1 else "answer")
        ak = AsyncKorely(api_key="kor_self_test", base_url=server.url, timeout=0.3)
        memory = asyncio.run(ak.add("Ada prefers tea.", user_id="ada"))
        self.assertEqual((memory.id, server.writes, len(server.posts())), ("mem_1", 1, 2))

    def test_no_answer_twice_is_a_clear_error_after_one_retry(self):
        server = self._server(lambda n: "hang")
        with self.assertRaises(KorelyError) as caught:
            self._korely(server).add("Ada prefers tea.", user_id="ada")
        self.assertEqual(len(server.posts()), 2)
        key = server.posts()[0][2].strip('"')
        self.assertEqual(caught.exception.code, "write_not_confirmed")
        self.assertIn("may have been made", str(caught.exception))
        self.assertIn(key, str(caught.exception))

    def test_a_retry_that_finds_the_first_still_being_made_says_so_too(self):
        server = self._server(lambda n: "lose" if n == 1 else "in_use")
        with self.assertRaises(KorelyError) as caught:
            self._korely(server).add("Ada prefers tea.", user_id="ada")
        self.assertEqual(caught.exception.code, "write_not_confirmed")
        self.assertEqual(len(server.posts()), 2)

    def test_a_retry_that_fails_any_other_way_leaves_the_write_unconfirmed(self):
        # The first try was made and its answer lost. Only a success of the
        # retry says so: a 5xx, a 429 or a timeout of the retry tell nothing
        # of the first, so each is write_not_confirmed, with the key and the
        # cause, never the bare 503 that read as "not made". Until 2026-11-23
        # these raised the retry's own error.
        for fate in (503, 502, 429, "hang"):
            with self.subTest(retry=fate):
                server = self._server(lambda n, fate=fate: "lose" if n == 1 else fate)
                with self.assertRaises(KorelyError) as caught:
                    self._korely(server).add("Ada prefers tea.", user_id="ada")
                e = caught.exception
                key = server.posts()[0][2].strip('"')
                self.assertEqual((e.code, len(server.posts()), server.writes),
                                 ("write_not_confirmed", 2, 1))
                self.assertIn("may have been made", str(e))
                self.assertIn(key, str(e))
                self.assertIn(f"HTTP {fate}" if isinstance(fate, int) else "timed out", str(e))
                self.assertEqual(e.__cause__.status, fate if isinstance(fate, int) else None)

    def test_the_callers_key_is_the_one_sent_both_times(self):
        server = self._server(lambda n: "lose" if n == 1 else "answer")
        self._korely(server).add("Ada prefers tea.", user_id="ada", idempotency_key="k-7")
        self.assertEqual([key for _, _, key in server.posts()], ['"k-7"', '"k-7"'])
        self.assertEqual(server.writes, 1)

    def test_retry_writes_false_sends_it_once_and_names_the_key(self):
        server = self._server(lambda n: "lose" if n == 1 else "answer")
        with self.assertRaises(KorelyError) as caught:
            self._korely(server, retry_writes=False).add("x", user_id="ada",
                                                         idempotency_key="k-8")
        self.assertEqual(len(server.posts()), 1)
        self.assertEqual(caught.exception.code, "write_not_confirmed")
        self.assertIn("k-8", str(caught.exception))

    def test_a_read_or_a_delete_is_never_sent_again(self):
        server = self._server(lambda n: "lose")
        k = self._korely(server)
        with self.assertRaises(KorelyError) as read:
            k.get_context("where?", user_id="ada")
        with self.assertRaises(KorelyError):
            k.delete("mem_1")
        self.assertEqual([m for m, _, _ in server.seen], ["GET", "DELETE"])
        self.assertEqual(read.exception.code, "connection_error")

    def test_a_write_the_server_answered_is_not_sent_again(self):
        server = self._server(lambda n: 503)
        with self.assertRaises(KorelyError) as caught:
            self._korely(server).add("x", user_id="ada")
        self.assertEqual((caught.exception.status, len(server.posts())), (503, 1))
