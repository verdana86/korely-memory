"""A 429 is waited out and the call sent again (0.4.0, decided on 26 Nov 2026).

The client waits the seconds the server asks (Retry-After, or `retry_after`
in the body) and sends the same request again: at most `max_retries` more
times (3) and at most `max_retry_wait` seconds of waiting in all (60). A wait
past that bound, or a 429 that names none, raises QuotaExceededError at
once, with `retry_after`, as up to 0.3.0. A write keeps its Idempotency-Key.
`max_retries=0` turns it off. The async client does the same.
"""
import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from korely_memory import AsyncKorely, Korely, KorelyError  # noqa: E402
from korely_memory import client as client_module  # noqa: E402
from korely_memory.exceptions import QuotaExceededError  # noqa: E402


def _limited(seconds, code="rate_limit_exceeded"):
    """A 429 as `_send` returns it: the Retry-After header under `_retry_after`."""
    body = {"code": code, "message": "Too many requests."}
    if seconds is not None:
        body["_retry_after"] = str(seconds)
    return 429, body


class _Server:
    """Stand-in for Korely._send: answers in turn, records each request."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, method, path, *, params=None, json_body=None, headers=None):
        self.calls.append({"method": method, "path": path, "json": json_body,
                           "headers": dict(headers or {})})
        return self.answers.pop(0)


def _client(server, **kw):
    k = Korely(api_key="kor_live_test", **kw)
    k._send = server
    return k


class AFourTwentyNineIsWaitedOut(unittest.TestCase):

    def setUp(self):
        self.slept = []
        patcher = mock.patch.object(client_module.time, "sleep", self.slept.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_call_is_sent_again_after_the_wait_the_server_asks(self):
        server = _Server(_limited(2), (200, {"results": []}))
        hits = _client(server).search("tea", user_id="u")
        self.assertEqual(list(hits), [])
        self.assertEqual(len(server.calls), 2)
        self.assertEqual(self.slept, [2.0])

    def test_the_wait_in_the_body_counts_too(self):
        server = _Server((429, {"code": "rate_limit_exceeded", "retry_after": 3}),
                         (200, {"results": []}))
        _client(server).search("tea", user_id="u")
        self.assertEqual(self.slept, [3.0])

    def test_a_write_is_sent_again_with_the_same_idempotency_key(self):
        server = _Server(_limited(1), (201, {"id": "mem_1", "content": "c"}))
        m = _client(server).add("c", user_id="u")
        self.assertEqual(m.id, "mem_1")
        keys = [c["headers"].get("Idempotency-Key") for c in server.calls]
        self.assertEqual(len(keys), 2)
        self.assertTrue(keys[0])
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(server.calls[0]["json"], server.calls[1]["json"])

    def test_at_most_three_more_tries(self):
        server = _Server(*[_limited(1)] * 5)
        with self.assertRaises(QuotaExceededError) as caught:
            _client(server).search("tea", user_id="u")
        self.assertEqual(len(server.calls), 4)
        self.assertEqual(self.slept, [1.0, 1.0, 1.0])
        self.assertEqual(caught.exception.retry_after, 1)

    def test_at_most_sixty_seconds_of_waiting_in_all(self):
        server = _Server(_limited(30), _limited(30), _limited(30), (200, {"results": []}))
        with self.assertRaises(QuotaExceededError) as caught:
            _client(server).search("tea", user_id="u")
        self.assertEqual(self.slept, [30.0, 30.0])
        self.assertEqual(len(server.calls), 3)
        self.assertEqual(caught.exception.retry_after, 30)

    def test_a_wait_past_the_bound_raises_at_once_with_its_retry_after(self):
        server = _Server(_limited(61))
        with self.assertRaises(QuotaExceededError) as caught:
            _client(server).search("tea", user_id="u")
        self.assertEqual((len(server.calls), self.slept), (1, []))
        self.assertEqual(caught.exception.retry_after, 61)

    def test_a_four_twenty_nine_that_names_no_wait_is_not_retried(self):
        for code in ("quota_exceeded", "agent_cap_exceeded"):
            with self.subTest(code=code):
                server = _Server(_limited(None, code))
                with self.assertRaises(QuotaExceededError) as caught:
                    _client(server).add("c", user_id="u")
                self.assertEqual((len(server.calls), caught.exception.code), (1, code))
        self.assertEqual(self.slept, [])

    def test_the_trial_four_twenty_nine_stays_within_the_bound(self):
        """The trial's 429 that asks to adopt the key, a minute at a time: one
        wait of 60 s is within the bound, a second is not; never forever."""
        trial = (429, {"code": "rate_limit_exceeded", "_retry_after": "60",
                       "message": "This key is on trial: adopt it to lift the limit."})
        server = _Server(trial, trial, trial, trial)
        with self.assertRaises(QuotaExceededError):
            _client(server).add("c", user_id="u")
        self.assertEqual(self.slept, [60.0])
        self.assertEqual(len(server.calls), 2)

    def test_max_retries_zero_sends_every_call_once(self):
        server = _Server(_limited(1))
        with self.assertRaises(QuotaExceededError) as caught:
            _client(server, max_retries=0).search("tea", user_id="u")
        self.assertEqual((len(server.calls), self.slept, caught.exception.retry_after),
                         (1, [], 1))

    def test_the_bounds_are_the_clients_to_set(self):
        server = _Server(_limited(5), _limited(5), (200, {"results": []}))
        with self.assertRaises(QuotaExceededError):
            _client(server, max_retries=5, max_retry_wait=9).search("tea", user_id="u")
        self.assertEqual(self.slept, [5.0])
        for bad in ({"max_retries": -1}, {"max_retries": 1.5}, {"max_retries": True},
                    {"max_retry_wait": -1}, {"max_retry_wait": "60"},
                    {"max_retry_wait": float("nan")}):
            with self.subTest(**bad), self.assertRaises(KorelyError):
                Korely(api_key="kor_live_test", **bad)

    def test_other_errors_are_never_waited_for(self):
        for status in (400, 401, 403, 404, 409, 422, 500, 503):
            with self.subTest(status=status):
                server = _Server((status, {"code": "x", "_retry_after": "1"}))
                with self.assertRaises(KorelyError):
                    _client(server).search("tea", user_id="u")
                self.assertEqual(len(server.calls), 1)
        self.assertEqual(self.slept, [])


class TheAsyncClientDoesTheSame(unittest.TestCase):

    def test_it_waits_and_sends_again_and_can_be_turned_off(self):
        slept = []
        with mock.patch.object(client_module.time, "sleep", slept.append):
            ak = AsyncKorely(api_key="kor_live_test")
            ak._sync._send = server = _Server(_limited(2), (201, {"id": "mem_1", "content": "c"}))
            m = asyncio.run(ak.add("c", user_id="u"))
            self.assertEqual((m.id, slept, len(server.calls)), ("mem_1", [2.0], 2))
            self.assertEqual(server.calls[0]["headers"]["Idempotency-Key"],
                             server.calls[1]["headers"]["Idempotency-Key"])
            off = AsyncKorely(api_key="kor_live_test", max_retries=0, max_retry_wait=5)
            self.assertEqual((off._sync.max_retries, off._sync.max_retry_wait), (0, 5.0))
            off._sync._send = once = _Server(_limited(1))
            with self.assertRaises(QuotaExceededError):
                asyncio.run(off.search("tea", user_id="u"))
            self.assertEqual(len(once.calls), 1)


if __name__ == "__main__":
    unittest.main()
