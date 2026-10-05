"""The LangGraph integration (korely_memory.integrations.langgraph), no network.

The client's one transport seam, Korely._send, is replaced by FakeKorely: an
in-memory stand-in for the endpoints the integration calls, written from the
API reference (filters, newest-first pages, metadata compared as strings,
snippets cut at 280 characters, 404 after a delete). The store is then tested
through the real client code, request by request.

The classes that need LangGraph skip when the extra is not installed, so the
release check (`python3 -m unittest discover -s tests`, on Python 3.9) stays
green; run them with the extra:

    cd python && python3.13 -m venv .venv
    .venv/bin/pip install langgraph langchain-core pytest
    .venv/bin/python -m pytest tests -q
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import unittest
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from korely_memory import AsyncKorely, Korely  # noqa: E402

try:
    import langchain_core.tools  # noqa: F401
    import langgraph.store.base  # noqa: F401
    HAVE_LANGGRAPH = True
except ImportError:
    HAVE_LANGGRAPH = False

if HAVE_LANGGRAPH:
    from langgraph.store.base import (  # noqa: E402
        GetOp,
        InvalidNamespaceError,
        ListNamespacesOp,
        PutOp,
        SearchOp,
    )

    from korely_memory.integrations.langgraph import (  # noqa: E402
        KorelyScope,
        KorelyStore,
        akorely_context,
        create_korely_tools,
        default_namespace_to_scope,
        korely_context,
    )

needs_langgraph = unittest.skipUnless(
    HAVE_LANGGRAPH, "the [langgraph] extra is not installed (Python 3.10+)")

_PYTHON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _as_text(value):
    """What Postgres' ->> gives back for a JSON value: the server compares
    metadata filters against this, as strings."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    return json.dumps(value)


class FakeKorely:
    """The endpoints the integration uses, in memory, behind Korely._send."""

    def __init__(self, *, ignore_run_id=False, page_cap=200, context="## Known facts\n- fact"):
        self.memories = {}       # id -> stored row
        self.calls = []          # (method, path, params, json)
        self.ignore_run_id = ignore_run_id  # an older server: run_id not filtered
        self.page_cap = page_cap            # older servers capped a page at 100
        self.context = context
        self._seq = 0

    # the transport seam
    def __call__(self, method, path, *, params=None, json_body=None):
        self.calls.append((method, path, params, json_body))
        if method == "POST" and path == "/v1/memories":
            return self._add(json_body)
        if method == "GET" and path == "/v1/memories":
            return self._list(params or {})
        if method == "POST" and path == "/v1/memories/search":
            return self._search(json_body)
        if method == "DELETE" and path.startswith("/v1/memories/"):
            return self._delete(path.rsplit("/", 1)[1])
        if method == "GET" and path == "/v1/context":
            return 200, {"context": self.context, "tokens": 7, "sources": []}
        return 404, {"code": "not_found", "message": f"no route {method} {path}"}

    def requests(self, method=None, path=None):
        return [c for c in self.calls
                if (method is None or c[0] == method) and (path is None or c[1] == path)]

    def live(self):
        return [m for m in self.memories.values() if not m["deleted"]]

    # endpoints
    def _add(self, body):
        content = (body.get("content") or "").strip()
        if not content:
            return 422, {"code": "invalid_request", "message": "content is empty"}
        self._seq += 1
        mid = "mem_%032x" % self._seq
        row = {
            "id": mid, "content": content, "user_id": body.get("user_id"),
            "agent_id": body.get("agent_id"), "run_id": body.get("run_id"),
            "metadata": json.loads(json.dumps(body.get("metadata") or {})),
            "created_at": "2026-10-05T10:%02d:%02dZ" % divmod(self._seq, 60),
            "seq": self._seq, "deleted": False,
        }
        self.memories[mid] = row
        return 201, self._public(row, status="processing")

    def _public(self, row, **extra):
        out = {k: row[k] for k in ("id", "content", "user_id", "agent_id", "run_id",
                                   "metadata", "created_at")}
        out["updated_at"] = None
        out.update(extra)
        return out

    def _scoped(self, user_id, agent_id, run_id):
        rows = self.live()
        if user_id is not None:
            rows = [m for m in rows if m["user_id"] == user_id]
        if agent_id is not None:
            rows = [m for m in rows if m["agent_id"] == agent_id]
        if run_id is not None and not self.ignore_run_id:
            rows = [m for m in rows if m["run_id"] == run_id]
        return rows

    def _list(self, params):
        limit, offset = int(params.get("limit", 50)), int(params.get("offset", 0))
        if not 1 <= limit <= 200 or offset < 0:
            return 422, {"code": "invalid_request", "message": "limit out of range"}
        rows = sorted(self._scoped(params.get("user_id"), params.get("agent_id"),
                                   params.get("run_id")),
                      key=lambda m: -m["seq"])  # newest first
        page = rows[offset:offset + min(limit, self.page_cap)]
        return 200, {"memories": [self._public(m) for m in page], "total": len(rows)}

    def _search(self, body):
        query = (body.get("query") or "").strip()
        limit = body.get("limit", 15)
        if not query or not 1 <= limit <= 50:
            return 422, {"code": "invalid_request", "message": "bad query or limit"}
        rows = self._scoped(body.get("user_id"), body.get("agent_id"), body.get("run_id"))
        for k, v in (body.get("metadata") or {}).items():
            rows = [m for m in rows if _as_text(m["metadata"].get(k)) == str(v)]
        words = set(re.findall(r"\w+", query.lower()))

        def score(m):
            have = set(re.findall(r"\w+", m["content"].lower()))
            return len(words & have) / (len(words) or 1)

        ranked = sorted(rows, key=lambda m: (-score(m), -m["seq"]))[:limit]
        return 200, {"results": [{
            "id": m["id"], "score": round(score(m), 3), "snippet": m["content"][:280],
            "user_id": m["user_id"], "agent_id": m["agent_id"], "metadata": m["metadata"],
        } for m in ranked]}

    def _delete(self, mid):
        row = self.memories.get(mid)
        if row is None or row["deleted"]:
            return 404, {"code": "not_found", "message": "Memory not found"}
        row["deleted"] = True
        return 200, {"id": mid, "status": "forgotten", "facts_invalidated": 0,
                     "audit_id": "aud_1"}


def _client(fake):
    k = Korely(api_key="kor_live_test")
    k._send = fake
    return k


def _async_client(fake):
    k = AsyncKorely(api_key="kor_live_test")
    k._sync._send = fake
    return k


NS = ("memories", "maria")


# ── the store ──────────────────────────────────────────────────────────────


@needs_langgraph
class TheStoreWritesKorelyMemories(unittest.TestCase):
    def setUp(self):
        self.fake = FakeKorely()
        self.store = KorelyStore(_client(self.fake))

    def test_a_put_is_one_memory_of_the_user_in_the_namespace_run(self):
        self.store.put(NS, "k1", {"content": "Maria is vegetarian", "kind": "diet"})
        post = self.fake.requests("POST", "/v1/memories")[-1][3]
        self.assertEqual(post["content"], "Maria is vegetarian")
        self.assertEqual(post["user_id"], "maria")
        self.assertNotIn("agent_id", post)
        self.assertEqual(post["run_id"], "langgraph:memories.maria")
        self.assertEqual(post["metadata"]["content"], "Maria is vegetarian")
        self.assertEqual(post["metadata"]["kind"], "diet")
        record = post["metadata"]["langgraph"]
        self.assertEqual(record["namespace"], ["memories", "maria"])
        self.assertEqual(record["key"], "k1")
        self.assertEqual(record["created_at"], record["updated_at"])

    def test_get_returns_the_value_exactly(self):
        value = {"content": "Maria likes jazz", "score": 4.5, "tags": ["music", "evening"],
                 "nested": {"a": [1, 2, {"b": None}]}, "flag": True}
        self.store.put(NS, "k1", value)
        item = self.store.get(NS, "k1")
        self.assertEqual(item.value, value)
        self.assertEqual(item.key, "k1")
        self.assertEqual(item.namespace, NS)
        self.assertIsInstance(item.created_at, datetime)
        self.assertIsNotNone(item.created_at.tzinfo)

    def test_get_of_a_missing_key_is_none(self):
        self.store.put(NS, "k1", {"content": "x"})
        self.assertIsNone(self.store.get(NS, "nope"))

    def test_a_put_on_an_existing_key_replaces_it(self):
        self.store.put(NS, "k1", {"content": "Maria lives in Rome"})
        first = self.store.get(NS, "k1")
        self.store.put(NS, "k1", {"content": "Maria lives in Milan"})
        item = self.store.get(NS, "k1")
        self.assertEqual(item.value, {"content": "Maria lives in Milan"})
        self.assertEqual(item.created_at, first.created_at)
        self.assertGreaterEqual(item.updated_at, first.updated_at)
        self.assertEqual([m["content"] for m in self.fake.live()], ["Maria lives in Milan"])
        # the new memory is stored before the old one is forgotten
        kinds = [(c[0], c[1]) for c in self.fake.calls if c[0] in ("POST", "DELETE")]
        self.assertEqual(kinds[-2][0], "POST")
        self.assertEqual(kinds[-1][0], "DELETE")

    def test_delete_forgets_the_memory(self):
        self.store.put(NS, "k1", {"content": "Maria likes jazz"})
        self.store.put(NS, "k2", {"content": "Maria likes rock"})
        self.store.delete(NS, "k1")
        self.assertIsNone(self.store.get(NS, "k1"))
        self.assertEqual(self.store.get(NS, "k2").value, {"content": "Maria likes rock"})
        self.assertEqual(len(self.fake.requests("DELETE")), 1)
        hits = self.store.search(NS, query="jazz")
        self.assertEqual([h.key for h in hits], ["k2"])

    def test_deleting_a_missing_key_sends_no_delete(self):
        self.store.delete(NS, "nope")
        self.assertEqual(self.fake.requests("DELETE"), [])

    def test_a_concurrent_delete_is_not_an_error(self):
        self.store.put(NS, "k1", {"content": "x"})
        real = self.fake._delete
        self.fake._delete = lambda mid: (real(mid), (404, {"code": "not_found"}))[1]
        self.store.delete(NS, "k1")  # the 404 means it is gone, which was the point
        self.assertIsNone(self.store.get(NS, "k1"))


@needs_langgraph
class TheTextKorelyDigests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeKorely()
        self.store = KorelyStore(_client(self.fake))

    def _text_of(self, value, **kw):
        self.store.put(NS, "k", value, **kw)
        return self.fake.requests("POST", "/v1/memories")[-1][3]["content"]

    def test_the_usual_text_fields(self):
        self.assertEqual(self._text_of({"data": "User likes pizza"}), "User likes pizza")
        self.assertEqual(self._text_of({"memory": "Will likes AI"}), "Will likes AI")

    def test_langmem_nests_it(self):
        value = {"kind": "Memory", "content": {"content": "Maria moved to Milan"}}
        self.assertEqual(self._text_of(value), "Maria moved to Milan")

    def test_otherwise_one_line_per_field(self):
        text = self._text_of({"food_preference": "pizza", "rating": 5, "empty": ""})
        self.assertEqual(text, "food_preference: pizza\nrating: 5")

    def test_index_paths_choose_the_fields(self):
        value = {"summary": "Maria plans a trip", "notes": ["Lisbon", "May"], "id": 7}
        self.assertEqual(self._text_of(value, index=["summary", "notes[*]"]),
                         "Maria plans a trip\nLisbon\nMay")
        store = KorelyStore(_client(self.fake), index_fields=["summary"])
        store.put(NS, "k2", value)
        self.assertEqual(self.fake.requests("POST", "/v1/memories")[-1][3]["content"],
                         "Maria plans a trip")

    def test_index_false_is_refused_before_sending(self):
        with self.assertRaises(NotImplementedError):
            self.store.put(NS, "k", {"content": "x"}, index=False)
        self.assertEqual(self.fake.calls, [])

    def test_a_value_without_text_is_refused_before_sending(self):
        with self.assertRaises(ValueError):
            self.store.put(NS, "k", {})
        with self.assertRaises(ValueError):
            self.store.put(NS, "k", {"content": "x"}, index=["missing"])
        self.assertEqual(self.fake.calls, [])

    def test_values_that_cannot_travel_are_refused_before_sending(self):
        with self.assertRaises(TypeError):
            self.store.put(NS, "k", {"when": datetime.now()})
        with self.assertRaises(ValueError):
            self.store.put(NS, "k", {"content": "x", "langgraph": {}})
        with self.assertRaises(TypeError):
            self.store.batch([PutOp(NS, "k", ["not", "a", "dict"])])
        self.assertEqual(self.fake.calls, [])


@needs_langgraph
class SearchingTheStore(unittest.TestCase):
    def setUp(self):
        self.fake = FakeKorely()
        self.store = KorelyStore(_client(self.fake))
        self.store.put(NS, "jazz", {"content": "Maria likes jazz concerts", "kind": "music", "stars": 5})
        self.store.put(NS, "rome", {"content": "Maria lives in Rome", "kind": "home", "stars": 3})
        self.store.put(NS, "food", {"content": "Maria likes pizza", "kind": "food", "stars": 4})

    def test_a_query_is_one_search_in_the_namespace_run(self):
        before = len(self.fake.calls)
        hits = self.store.search(NS, query="does maria like jazz", limit=2)
        self.assertEqual(len(self.fake.calls) - before, 1)
        body = self.fake.calls[-1][3]
        self.assertEqual(body["run_id"], "langgraph:memories.maria")
        self.assertEqual(body["user_id"], "maria")
        self.assertEqual(body["limit"], 2)
        self.assertEqual(hits[0].key, "jazz")
        self.assertGreater(hits[0].score, hits[1].score)
        self.assertEqual(hits[0].value["stars"], 5)
        self.assertEqual(hits[0].namespace, NS)

    def test_values_come_from_metadata_not_from_the_280_character_snippet(self):
        long_text = "Maria's travel diary. " + "word " * 100
        self.store.put(NS, "diary", {"content": long_text})
        hit = [h for h in self.store.search(NS, query="travel diary", limit=5)
               if h.key == "diary"][0]
        self.assertEqual(hit.value["content"], long_text)

    def test_offset_and_limit_slice_the_ranking(self):
        everything = self.store.search(NS, query="maria likes", limit=3)
        page = self.store.search(NS, query="maria likes", limit=1, offset=1)
        self.assertEqual([h.key for h in page], [everything[1].key])
        self.assertEqual(self.fake.calls[-1][3]["limit"], 2)

    def test_more_than_fifty_hits_is_refused_before_sending(self):
        before = len(self.fake.calls)
        with self.assertRaises(NotImplementedError):
            self.store.search(NS, query="maria", limit=40, offset=20)
        self.assertEqual(len(self.fake.calls), before)

    def test_an_equality_filter_runs_on_the_server_as_strings(self):
        hits = self.store.search(NS, query="maria", filter={"stars": 5})
        self.assertEqual([h.key for h in hits], ["jazz"])
        self.assertEqual(self.fake.calls[-1][3]["metadata"], {"stars": "5"})
        self.store.put(NS, "flag", {"content": "Maria is a member", "member": True})
        self.store.search(NS, query="member", filter={"member": {"$eq": True}})
        self.assertEqual(self.fake.calls[-1][3]["metadata"], {"member": "true"})

    def test_a_string_five_does_not_pass_a_filter_on_five(self):
        # Korely compares strings, so the server lets "5" through; the store
        # applies LangGraph's typed comparison to what comes back.
        self.store.put(NS, "text5", {"content": "Maria gave five stars", "stars": "5"})
        hits = self.store.search(NS, query="maria stars", filter={"stars": 5})
        self.assertEqual([h.key for h in hits], ["jazz"])

    def test_a_filter_korely_cannot_run_is_refused_before_sending(self):
        before = len(self.fake.calls)
        for flt in ({"stars": {"$gt": 3}}, {"tags": ["a"]}, {"kind": None},
                    {"meta": {"source": "x"}}):
            with self.subTest(filter=flt), self.assertRaises(NotImplementedError):
                self.store.search(NS, query="maria", filter=flt)
        with self.assertRaises(ValueError):
            self.store.search(NS, query="maria", filter={"stars": {"$between": [1, 2]}})
        self.assertEqual(len(self.fake.calls), before)

    def test_without_a_query_it_lists_newest_first_with_every_operator(self):
        self.assertEqual([h.key for h in self.store.search(NS)], ["food", "rome", "jazz"])
        hits = self.store.search(NS, filter={"stars": {"$gte": 4}})
        self.assertEqual([h.key for h in hits], ["food", "jazz"])
        self.assertEqual([h.key for h in self.store.search(NS, limit=1, offset=1)], ["rome"])
        self.assertIsNone(self.store.search(NS)[0].score)
        self.assertEqual(self.fake.requests("POST", "/v1/memories/search"), [])

    def test_a_listing_stops_paging_once_it_has_enough(self):
        fake = FakeKorely(page_cap=2)
        store = KorelyStore(_client(fake))
        for i in range(7):
            store.put(NS, f"k{i}", {"content": f"memory {i}"})
        before = len(fake.requests("GET", "/v1/memories"))
        hits = store.search(NS, limit=3)
        self.assertEqual([h.key for h in hits], ["k6", "k5", "k4"])
        self.assertEqual(len(fake.requests("GET", "/v1/memories")) - before, 2)

    def test_a_prefix_across_namespaces_is_refused(self):
        with self.assertRaises(NotImplementedError):
            self.store.search(("memories",), query="maria")
        with self.assertRaises(NotImplementedError):
            self.store.search((), query="maria")


@needs_langgraph
class EachNamespaceSeesOnlyItsOwnItems(unittest.TestCase):
    def test_two_users_do_not_mix(self):
        fake = FakeKorely()
        store = KorelyStore(_client(fake))
        store.put(("memories", "maria"), "k", {"content": "Maria likes jazz"})
        store.put(("memories", "luca"), "k", {"content": "Luca likes jazz"})
        self.assertEqual(store.get(("memories", "maria"), "k").value["content"], "Maria likes jazz")
        self.assertEqual(store.get(("memories", "luca"), "k").value["content"], "Luca likes jazz")
        hits = store.search(("memories", "luca"), query="likes jazz")
        self.assertEqual([h.value["content"] for h in hits], ["Luca likes jazz"])

    def test_the_users_other_memories_are_not_items(self):
        fake = FakeKorely()
        korely = _client(fake)
        store = KorelyStore(korely)
        korely.add("Maria said she likes jazz", user_id="maria")
        store.put(NS, "k", {"content": "Maria likes jazz"})
        self.assertEqual([h.key for h in store.search(NS, query="jazz")], ["k"])
        self.assertEqual([h.key for h in store.search(NS)], ["k"])
        # ...while the rest of Korely sees the store's items as Maria's memories
        snippets = {h.snippet for h in korely.search("jazz", user_id="maria")}
        self.assertEqual(snippets, {"Maria said she likes jazz", "Maria likes jazz"})

    def test_an_older_server_that_ignores_run_id_still_does_not_leak(self):
        fake = FakeKorely(ignore_run_id=True)
        korely = _client(fake)
        store = KorelyStore(korely, namespace_to_scope=lambda ns: ns[1])
        korely.add("Maria said she likes jazz", user_id="maria")
        store.put(("profile", "maria"), "k", {"content": "Maria's profile: jazz"})
        store.put(("memories", "maria"), "k", {"content": "Maria likes jazz"})
        # Same user, same key, two namespaces: neither put touched the other.
        self.assertEqual(len(fake.live()), 3)
        self.assertEqual(fake.requests("DELETE"), [])
        self.assertEqual(store.get(("memories", "maria"), "k").value["content"], "Maria likes jazz")
        self.assertEqual(store.get(("profile", "maria"), "k").value["content"],
                         "Maria's profile: jazz")
        hits = store.search(("memories", "maria"), query="jazz")
        self.assertEqual([h.value["content"] for h in hits], ["Maria likes jazz"])
        self.assertEqual([h.value["content"] for h in store.search(("profile", "maria"))],
                         ["Maria's profile: jazz"])
        store.delete(("memories", "maria"), "k")
        self.assertEqual(store.get(("profile", "maria"), "k").value["content"],
                         "Maria's profile: jazz")

    def test_pages_capped_at_100_are_walked_by_what_they_hold(self):
        fake = FakeKorely(page_cap=3)
        store = KorelyStore(_client(fake))
        for i in range(8):
            store.put(NS, f"k{i}", {"content": f"memory {i}"})
        self.assertEqual(store.get(NS, "k0").value["content"], "memory 0")
        self.assertEqual(len(store.search(NS, limit=100)), 8)


@needs_langgraph
class ConcurrentWritersLeaveTwoMemoriesForOneKey(unittest.TestCase):
    """Korely has no unique constraint on a key of ours: two processes putting
    the same key at once can both create a memory. Reads take the newest, and
    the next put or delete removes every other one, across pages."""

    def setUp(self):
        self.fake = FakeKorely(page_cap=2)  # small pages: deletes must not shift them
        self.korely = _client(self.fake)
        self.store = KorelyStore(self.korely)

    def _raw(self, key, content, updated_at):
        """A memory exactly as another process's KorelyStore would write it."""
        self.korely.add(content, user_id="maria", run_id="langgraph:memories.maria",
                        metadata={"content": content, "langgraph": {
                            "namespace": list(NS), "key": key,
                            "created_at": "2026-10-01T00:00:00+00:00",
                            "updated_at": updated_at}})

    def _copies(self, key):
        return [m for m in self.fake.live() if m["metadata"]["langgraph"]["key"] == key]

    def _seed(self):
        self._raw("k", "Maria lives in Rome", "2026-10-02T00:00:00+00:00")
        self._raw("other", "Maria likes tea", "2026-10-02T00:00:00+00:00")
        self._raw("k", "Maria lives in Turin", "2026-10-03T00:00:00+00:00")
        self._raw("other2", "Maria likes jazz", "2026-10-02T00:00:00+00:00")
        self._raw("k", "Maria lives in Milan", "2026-10-04T00:00:00+00:00")

    def test_reads_take_the_newest(self):
        self._seed()
        self.assertEqual(self.store.get(NS, "k").value["content"], "Maria lives in Milan")
        listed = [(h.key, h.value["content"]) for h in self.store.search(NS)]
        self.assertEqual(listed, [("k", "Maria lives in Milan"), ("other2", "Maria likes jazz"),
                                  ("other", "Maria likes tea")])
        hits = self.store.search(NS, query="maria lives in rome")
        self.assertEqual([(h.key, h.value["content"]) for h in hits if h.key == "k"],
                         [("k", "Maria lives in Milan")])

    def test_delete_removes_every_copy(self):
        self._seed()
        self.store.delete(NS, "k")
        self.assertEqual(self._copies("k"), [])
        self.assertEqual(len(self.fake.live()), 2)

    def test_put_removes_every_older_copy(self):
        self._seed()
        self.store.put(NS, "k", {"content": "Maria lives in Bologna"})
        self.assertEqual([m["content"] for m in self._copies("k")], ["Maria lives in Bologna"])
        self.assertEqual(self.store.get(NS, "k").created_at,
                         datetime(2026, 10, 1, tzinfo=timezone.utc))


@needs_langgraph
class NamespacesMapToTheEndUser(unittest.TestCase):
    def test_the_default_is_memories_user_id(self):
        self.assertEqual(default_namespace_to_scope(("memories", "maria")),
                         KorelyScope(user_id="maria"))
        for bad in [("maria", "memories"), ("memories",), ("memories", "maria", "work"),
                    ("users", "maria")]:
            with self.subTest(namespace=bad), self.assertRaises(InvalidNamespaceError):
                default_namespace_to_scope(bad)

    def test_a_namespace_it_cannot_map_is_refused_before_sending(self):
        fake = FakeKorely()
        store = KorelyStore(_client(fake))
        with self.assertRaises(InvalidNamespaceError):
            store.put(("maria", "memories"), "k", {"content": "x"})
        with self.assertRaises(InvalidNamespaceError):
            store.get(("memories", "maria.rossi"), "k")  # a period cannot be in a label
        self.assertEqual(fake.calls, [])

    def test_a_custom_mapping_and_the_store_agent(self):
        fake = FakeKorely()
        store = KorelyStore(_client(fake), agent_id="support-bot",
                            namespace_to_scope=lambda ns: (ns[0], None))
        store.put(("maria", "memories"), "k", {"content": "x"})
        post = fake.requests("POST", "/v1/memories")[-1][3]
        self.assertEqual((post["user_id"], post["agent_id"]), ("maria", "support-bot"))
        self.assertEqual(post["run_id"], "langgraph:maria.memories")
        store2 = KorelyStore(_client(fake), agent_id="support-bot",
                             namespace_to_scope=lambda ns: KorelyScope(ns[1], "billing"))
        store2.put(("memories", "luca"), "k", {"content": "y"})
        post = fake.requests("POST", "/v1/memories")[-1][3]
        self.assertEqual((post["user_id"], post["agent_id"]), ("luca", "billing"))
        get = fake.requests("GET", "/v1/memories")[-1][2]
        self.assertEqual((get["user_id"], get["agent_id"]), ("luca", "billing"))

    def test_a_mapping_that_returns_nonsense_is_a_type_error(self):
        store = KorelyStore(_client(FakeKorely()), namespace_to_scope=lambda ns: 42)
        with self.assertRaises(TypeError):
            store.get(NS, "k")


@needs_langgraph
class WhatKorelyCannotDoIsRefused(unittest.TestCase):
    def setUp(self):
        self.fake = FakeKorely()
        self.store = KorelyStore(_client(self.fake))

    def test_list_namespaces(self):
        with self.assertRaises(NotImplementedError) as ctx:
            self.store.list_namespaces(prefix=("memories",))
        self.assertIn("users()", str(ctx.exception))

    def test_ttl(self):
        with self.assertRaises(NotImplementedError):
            self.store.put(NS, "k", {"content": "x"}, ttl=5)
        with self.assertRaises(NotImplementedError):
            self.store.batch([PutOp(NS, "k", {"content": "x"}, ttl=5.0)])

    def test_one_bad_operation_and_the_batch_sends_nothing(self):
        with self.assertRaises(NotImplementedError):
            self.store.batch([
                PutOp(NS, "k", {"content": "x"}),
                GetOp(NS, "k"),
                ListNamespacesOp(),
            ])
        self.assertEqual(self.fake.calls, [])

    def test_a_batch_runs_in_order(self):
        results = self.store.batch([
            PutOp(NS, "k", {"content": "first"}),
            GetOp(NS, "k"),
            PutOp(NS, "k", {"content": "second"}),
            SearchOp(NS),
            PutOp(NS, "k", None),
            GetOp(NS, "k"),
        ])
        self.assertEqual(results[1].value, {"content": "first"})
        self.assertEqual([i.value for i in results[3]], [{"content": "second"}])
        self.assertIsNone(results[5])
        self.assertEqual(results[0], None)


@needs_langgraph
class TheStoreIsAsyncToo(unittest.TestCase):
    def _round_trip(self, client, fake):
        store = KorelyStore(client)

        async def go():
            await store.aput(NS, "k", {"content": "Maria likes jazz"})
            got = await store.aget(NS, "k")
            hits = await store.asearch(NS, query="jazz")
            await store.adelete(NS, "k")
            gone = await store.aget(NS, "k")
            return got, hits, gone

        got, hits, gone = asyncio.run(go())
        self.assertEqual(got.value, {"content": "Maria likes jazz"})
        self.assertEqual([h.key for h in hits], ["k"])
        self.assertIsNone(gone)
        self.assertEqual(fake.live(), [])

    def test_with_the_sync_client(self):
        fake = FakeKorely()
        self._round_trip(_client(fake), fake)

    def test_with_the_async_client(self):
        fake = FakeKorely()
        self._round_trip(_async_client(fake), fake)

    def test_alist_namespaces_is_refused(self):
        store = KorelyStore(_client(FakeKorely()))
        with self.assertRaises(NotImplementedError):
            asyncio.run(store.alist_namespaces())


@needs_langgraph
class TheStoreInAGraph(unittest.TestCase):
    """compile(store=KorelyStore(...)), a node writing and reading through
    runtime.store, two threads of the same user."""

    def test_memories_written_in_one_thread_are_read_in_another(self):
        from dataclasses import dataclass

        from langchain_core.messages import AIMessage
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import START, MessagesState, StateGraph
        from langgraph.runtime import Runtime

        @dataclass
        class Context:
            user_id: str

        def remember_and_recall(state: MessagesState, runtime: Runtime[Context]):
            namespace = ("memories", runtime.context.user_id)
            text = state["messages"][-1].text
            if text.startswith("remember "):
                runtime.store.put(namespace, str(len(text)), {"content": text[9:]})
                return {"messages": [AIMessage("ok")]}
            hits = runtime.store.search(namespace, query=text, limit=3)
            return {"messages": [AIMessage("; ".join(h.value["content"] for h in hits))]}

        fake = FakeKorely()
        builder = StateGraph(MessagesState, context_schema=Context)
        builder.add_node(remember_and_recall)
        builder.add_edge(START, "remember_and_recall")
        graph = builder.compile(checkpointer=InMemorySaver(), store=KorelyStore(_client(fake)))

        graph.invoke({"messages": [{"role": "user", "content": "remember I am vegetarian"}]},
                     {"configurable": {"thread_id": "1"}}, context=Context(user_id="maria"))
        out = graph.invoke({"messages": [{"role": "user", "content": "am I vegetarian"}]},
                           {"configurable": {"thread_id": "2"}}, context=Context(user_id="maria"))
        self.assertEqual(out["messages"][-1].text, "I am vegetarian")
        other = graph.invoke({"messages": [{"role": "user", "content": "am I vegetarian"}]},
                             {"configurable": {"thread_id": "3"}}, context=Context(user_id="luca"))
        self.assertEqual(other["messages"][-1].text, "")


# ── the context helper ─────────────────────────────────────────────────────


@needs_langgraph
class TheContextHelper(unittest.TestCase):
    def test_the_date_line_then_the_context(self):
        fake = FakeKorely(context="## Known facts\n- Maria lives in Milan (since 2026-09-01)")
        text = korely_context(_client(fake), "maria", "where do I live?",
                              token_budget=500, agent_id="bot", today=date(2026, 10, 5))
        self.assertEqual(text, "Current date: 2026-10-05\n\n"
                               "## Known facts\n- Maria lives in Milan (since 2026-09-01)")
        params = fake.calls[-1][2]
        self.assertEqual(params, {"query": "where do I live?", "user_id": "maria",
                                  "agent_id": "bot", "token_budget": 500})

    def test_the_date_defaults_to_today_in_utc(self):
        before = datetime.now(timezone.utc).date().isoformat()
        text = korely_context(_client(FakeKorely()), "maria", "q")
        after = datetime.now(timezone.utc).date().isoformat()
        self.assertIn(text.splitlines()[0], {f"Current date: {before}", f"Current date: {after}"})

    def test_without_the_date(self):
        text = korely_context(_client(FakeKorely(context="ctx")), "maria", "q", include_date=False)
        self.assertEqual(text, "ctx")

    def test_an_empty_question_makes_no_call(self):
        fake = FakeKorely()
        self.assertEqual(korely_context(_client(fake), "maria", "  ", today=date(2026, 1, 2)),
                         "Current date: 2026-01-02")
        self.assertEqual(fake.calls, [])

    def test_an_empty_context_leaves_the_date(self):
        text = korely_context(_client(FakeKorely(context="")), "maria", "q",
                              today=datetime(2026, 1, 2, 23, 59))
        self.assertEqual(text, "Current date: 2026-01-02")

    def test_no_user_id_is_refused(self):
        # GET /v1/context without user_id reads every end user of the key.
        fake = FakeKorely()
        for uid in (None, "", "  "):
            with self.subTest(user_id=uid), self.assertRaises(ValueError):
                korely_context(_client(fake), uid, "q")
        self.assertEqual(fake.calls, [])

    def test_async(self):
        fake = FakeKorely(context="ctx")
        for client in (_client(fake), _async_client(fake)):
            with self.subTest(client=type(client).__name__):
                text = asyncio.run(akorely_context(client, "maria", "q", today=date(2026, 10, 5)))
                self.assertEqual(text, "Current date: 2026-10-05\n\nctx")
        self.assertEqual(len(fake.requests("GET", "/v1/context")), 2)


# ── the tools ──────────────────────────────────────────────────────────────


@needs_langgraph
class TheTools(unittest.TestCase):
    def setUp(self):
        self.fake = FakeKorely(context="## Known facts\n- Maria is vegetarian")
        self.search, self.save = create_korely_tools(_client(self.fake), "maria", "bot")

    def test_names_and_what_the_model_sees(self):
        self.assertEqual((self.search.name, self.save.name), ("search_memory", "save_memory"))
        self.assertEqual(set(self.search.args), {"query"})
        self.assertEqual(set(self.save.args), {"content"})
        for t in (self.search, self.save):
            schema = json.dumps(t.tool_call_schema.model_json_schema())
            self.assertNotIn("user_id", schema)
            self.assertNotIn("agent_id", schema)

    def test_search_memory_is_the_context_block_for_the_bound_user(self):
        out = self.search.invoke({"query": "what do I eat?"})
        self.assertTrue(out.startswith("Current date: "))
        self.assertIn("Maria is vegetarian", out)
        params = self.fake.calls[-1][2]
        self.assertEqual((params["user_id"], params["agent_id"]), ("maria", "bot"))

    def test_save_memory_writes_for_the_bound_user(self):
        out = self.save.invoke({"content": "Maria is allergic to nuts"})
        self.assertIn("Saved to long-term memory (mem_", out)
        body = self.fake.requests("POST", "/v1/memories")[-1][3]
        self.assertEqual(body, {"content": "Maria is allergic to nuts",
                                "user_id": "maria", "agent_id": "bot"})

    def test_the_model_cannot_choose_the_user(self):
        self.save.invoke({"content": "x", "user_id": "luca", "agent_id": "other"})
        self.search.invoke({"query": "x", "user_id": "luca"})
        body = self.fake.requests("POST", "/v1/memories")[-1][3]
        self.assertEqual((body["user_id"], body["agent_id"]), ("maria", "bot"))
        self.assertEqual(self.fake.calls[-1][2]["user_id"], "maria")

    def test_nothing_found_says_so(self):
        search, _ = create_korely_tools(_client(FakeKorely(context="")), "maria")
        self.assertEqual(search.invoke({"query": "x"}),
                         "Nothing in long-term memory matches this query.")

    def test_a_korely_error_is_the_answer_not_a_crash(self):
        def down(method, path, **kw):
            return 503, {"code": "search_unavailable", "message": "Memory search is down"}

        korely = _client(FakeKorely())
        korely._send = down
        search, save = create_korely_tools(korely, "maria")
        self.assertIn("Memory search is down", search.invoke({"query": "x"}))
        self.assertIn("Could not save", save.invoke({"content": "x"}))

    def test_async_with_either_client(self):
        for make in (_client, _async_client):
            fake = FakeKorely(context="ctx")
            search, save = create_korely_tools(make(fake), "maria")
            with self.subTest(client=make.__name__):
                out = asyncio.run(search.ainvoke({"query": "q"}))
                self.assertTrue(out.endswith("ctx"))
                asyncio.run(save.ainvoke({"content": "Maria likes tea"}))
                self.assertEqual(fake.live()[0]["content"], "Maria likes tea")
                self.assertEqual(fake.live()[0]["user_id"], "maria")

    def test_a_user_id_is_required(self):
        with self.assertRaises(ValueError):
            create_korely_tools(_client(FakeKorely()), "")


# ── the README example ─────────────────────────────────────────────────────


@needs_langgraph
class TheReadmeExampleRuns(unittest.TestCase):
    """The graph in python/README.md, with a stand-in model: context in a
    SystemMessage before the answer, the turn written back, the checkpointer
    keeping the thread."""

    def test_it(self):
        from dataclasses import dataclass

        from langchain_core.messages import AIMessage, SystemMessage
        from langchain_core.runnables import RunnableLambda
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import START, MessagesState, StateGraph
        from langgraph.runtime import Runtime

        fake = FakeKorely(context="## Known facts\n- Maria lives in Milan")
        korely = _client(fake)
        seen = []

        def answer(messages):
            seen.append(messages)
            return AIMessage("To Milan.")

        model = RunnableLambda(answer)

        @dataclass
        class Context:
            user_id: str

        def call_model(state: MessagesState, runtime: Runtime[Context]):
            user_id = runtime.context.user_id
            question = state["messages"][-1].text
            memory = korely_context(korely, user_id, question, token_budget=800)
            reply = model.invoke([SystemMessage(memory), *state["messages"]])
            korely.add([{"role": "user", "content": question},
                        {"role": "assistant", "content": reply.text}], user_id=user_id)
            return {"messages": [reply]}

        builder = StateGraph(MessagesState, context_schema=Context)
        builder.add_node(call_model)
        builder.add_edge(START, "call_model")
        graph = builder.compile(checkpointer=InMemorySaver())

        config = {"configurable": {"thread_id": "1"}}
        graph.invoke({"messages": [{"role": "user", "content": "Where should I send the package?"}]},
                     config, context=Context(user_id="maria"))
        out = graph.invoke({"messages": [{"role": "user", "content": "Thanks"}]},
                           config, context=Context(user_id="maria"))

        system = seen[0][0]
        self.assertIsInstance(system, SystemMessage)
        self.assertTrue(system.content.startswith("Current date: "))
        self.assertIn("Maria lives in Milan", system.content)
        self.assertEqual(len(out["messages"]), 4)  # the checkpointer kept the thread
        self.assertEqual(fake.live()[0]["content"],
                         "user: Where should I send the package?\nassistant: To Milan.")


# ── packaging ──────────────────────────────────────────────────────────────


class TheCoreStaysDependencyFree(unittest.TestCase):
    """These run with or without the extra, on every Python the SDK supports."""

    def _run(self, code):
        env = dict(os.environ, PYTHONPATH=_PYTHON_DIR)
        return subprocess.run([sys.executable, "-c", code], capture_output=True,
                              text=True, cwd=_PYTHON_DIR, env=env, timeout=120)

    def test_importing_korely_memory_imports_no_framework(self):
        out = self._run(
            "import sys, korely_memory, korely_memory.integrations\n"
            "loaded = sorted(m for m in sys.modules if m.split('.')[0] in "
            "('langgraph', 'langchain_core', 'langchain', 'pydantic'))\n"
            "print(loaded)\n")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "[]")

    def test_without_the_extra_the_error_says_what_to_install(self):
        # Hide LangGraph even where it is installed, then import the module.
        out = self._run(
            "import importlib.abc, sys\n"
            "class Hide(importlib.abc.MetaPathFinder):\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name.split('.')[0] in ('langgraph', 'langchain_core'):\n"
            "            raise ModuleNotFoundError(name)\n"
            "sys.meta_path.insert(0, Hide())\n"
            "import korely_memory\n"
            "try:\n"
            "    import korely_memory.integrations.langgraph\n"
            "except ImportError as e:\n"
            "    print(e)\n")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("pip install 'korely-memory[langgraph]'", out.stdout)

    def test_the_extra_is_declared_and_the_core_has_no_dependencies(self):
        with open(os.path.join(_PYTHON_DIR, "pyproject.toml"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertRegex(text, r"(?m)^dependencies = \[\]$")
        extra = re.search(r"(?m)^langgraph = \[(.*)\]$", text)
        self.assertIsNotNone(extra, "no [langgraph] extra in pyproject.toml")
        self.assertIn('"langgraph>=', extra.group(1))
        self.assertIn('"langchain-core>=', extra.group(1))


if __name__ == "__main__":
    unittest.main()
