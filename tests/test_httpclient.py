"""The four behaviors httpclient.py exists to guarantee.

Each was learned by getting it wrong against a live API, and each fails
silently rather than loudly — which is why they are pinned here rather than
left to be rediscovered:

  - a non-default User-Agent (urllib's is banned by some WAFs);
  - one network entry point, so page two gets the same retries as page one;
  - retries on rate limits and transient faults, for unattended launchd runs;
  - 401/403 as NoScope, because a partial token is the normal case.
"""
from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brainiphy_cli.httpclient import HttpClient, NoScope  # noqa: E402


def _response(payload: dict):
    """A urlopen context manager returning JSON."""
    body = json.dumps(payload).encode("utf-8")
    resp = mock.MagicMock()
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _http_error(code: int, body: str = "denied"):
    err = urllib.error.HTTPError(
        url="https://api.example.com/x", code=code, msg="err", hdrs=None,
        fp=io.BytesIO(body.encode("utf-8")))
    return err


class ClientSetupTests(unittest.TestCase):
    def test_a_client_with_no_credential_is_refused_at_construction(self):
        # Better here than at the first request, halfway through a sync.
        with self.assertRaises(ValueError):
            HttpClient("https://api.example.com")

    def test_the_user_agent_is_not_urllibs_default(self):
        # Cloudflare error 1010: a 403 that never reaches the vendor and reads
        # exactly like an auth problem.
        headers = HttpClient("https://api.example.com", token="t").headers()
        self.assertIn("User-Agent", headers)
        self.assertNotIn("Python-urllib", headers["User-Agent"])

    def test_the_token_is_sent_as_a_bearer_by_default(self):
        headers = HttpClient("https://api.example.com", token="secret").headers()
        self.assertEqual(headers["Authorization"], "Bearer secret")

    def test_a_vendor_specific_auth_header_is_supported(self):
        headers = HttpClient("https://api.example.com", token="k",
                             auth_header="X-Api-Key", auth_scheme="").headers()
        self.assertEqual(headers["X-Api-Key"], "k")

    def test_the_credential_is_read_lazily(self):
        # So `--probe` on a connector whose secret is not registered yet fails
        # with keychain's actionable message, at the moment it is needed.
        with mock.patch("brainiphy_cli.httpclient.get_secret") as get_secret:
            client = HttpClient("https://api.example.com", secret_item="item")
            get_secret.assert_not_called()
            client.headers()
            get_secret.assert_called_once_with("item")

    def test_a_trailing_slash_on_the_base_url_is_dropped(self):
        # Or every request path ends up with a double slash in it.
        self.assertEqual(HttpClient("https://api.example.com/", token="t").base_url,
                         "https://api.example.com")


class RequestTests(unittest.TestCase):
    def setUp(self):
        # backoff 0: these tests are about *what* is retried, not how long it waits.
        self.client = HttpClient("https://api.example.com", token="t",
                                 retries=3, backoff_seconds=0)

    def test_a_successful_request_returns_parsed_json(self):
        with mock.patch("urllib.request.urlopen", return_value=_response({"ok": True})):
            self.assertEqual(self.client.request("https://api.example.com/x"), {"ok": True})

    def test_401_and_403_become_no_scope_without_retrying(self):
        # A token is normally issued with a subset of scopes. Retrying a
        # permission decision only wastes the sync's time.
        for code in (401, 403):
            with self.subTest(code=code):
                urlopen = mock.patch("urllib.request.urlopen", side_effect=_http_error(code))
                with urlopen as patched, self.assertRaises(NoScope):
                    self.client.request("https://api.example.com/x")
                self.assertEqual(patched.call_count, 1)

    def test_a_rate_limit_is_retried_and_then_succeeds(self):
        responses = [_http_error(429), _response({"ok": True})]
        with mock.patch("urllib.request.urlopen", side_effect=responses) as patched:
            self.assertEqual(self.client.request("https://api.example.com/x"), {"ok": True})
        self.assertEqual(patched.call_count, 2)

    def test_a_server_error_is_retried_until_the_attempts_run_out(self):
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(503)) as patched, \
                self.assertRaises(RuntimeError):
            self.client.request("https://api.example.com/x")
        self.assertEqual(patched.call_count, 3)

    def test_a_transient_network_fault_is_retried(self):
        # A DNS blip under launchd must not become a stale graph.
        responses = [urllib.error.URLError("dns"), TimeoutError("tls"), _response({"ok": 1})]
        with mock.patch("urllib.request.urlopen", side_effect=responses) as patched:
            self.assertEqual(self.client.request("https://api.example.com/x"), {"ok": 1})
        self.assertEqual(patched.call_count, 3)

    def test_a_client_error_fails_immediately(self):
        # A 404 will not fix itself; retrying it just delays the report.
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(404)) as patched, \
                self.assertRaises(RuntimeError):
            self.client.request("https://api.example.com/x")
        self.assertEqual(patched.call_count, 1)


class GetTests(unittest.TestCase):
    def setUp(self):
        self.client = HttpClient("https://api.example.com", token="t", backoff_seconds=0)

    def _url_for(self, **kwargs):
        with mock.patch.object(self.client, "request", return_value={}) as request:
            self.client.get("/contacts", kwargs or None)
        return request.call_args[0][0]

    def test_params_are_query_encoded(self):
        self.assertIn("limit=10", self._url_for(limit=10))

    def test_none_params_are_dropped_rather_than_sent_as_the_string_none(self):
        url = self._url_for(limit=10, cursor=None)
        self.assertNotIn("cursor", url)

    def test_a_path_with_no_params_stays_clean(self):
        with mock.patch.object(self.client, "request", return_value={}) as request:
            self.client.get("/contacts")
        self.assertEqual(request.call_args[0][0], "https://api.example.com/contacts")


class PaginateTests(unittest.TestCase):
    def setUp(self):
        self.client = HttpClient("https://api.example.com", token="t",
                                 backoff_seconds=0, page_limit=2, max_pages=10)

    def test_a_next_page_url_is_followed_through_request(self):
        """The bug this method exists to make impossible: a second, bare
        urlopen for the next page that skips every retry and NoScope path."""
        pages = [
            {"items": [{"id": 1}, {"id": 2}],
             "meta": {"nextPageUrl": "https://api.example.com/x?page=2"}},
            {"items": [{"id": 3}], "meta": {}},
        ]
        with mock.patch.object(self.client, "request", side_effect=pages) as request:
            items = self.client.paginate("/x", {}, "items")
        self.assertEqual([i["id"] for i in items], [1, 2, 3])
        # Both pages went through the one entry point.
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[1][0][0], "https://api.example.com/x?page=2")

    def test_a_cursor_is_fed_back_into_the_next_request(self):
        pages = [
            {"items": [{"id": 1}, {"id": 2}], "meta": {"startAfterId": "abc"}},
            {"items": [{"id": 3}], "meta": {}},
        ]
        with mock.patch.object(self.client, "request", side_effect=pages):
            items = self.client.paginate("/x", {}, "items")
        self.assertEqual(len(items), 3)

    def test_a_short_page_ends_the_walk_whatever_the_cursor_says(self):
        # Some APIs return a cursor forever; trusting it means max_pages
        # requests for one page of data on every single sync.
        page = {"items": [{"id": 1}], "meta": {"startAfterId": "still-here"}}
        with mock.patch.object(self.client, "request", return_value=page) as request:
            items = self.client.paginate("/x", {}, "items")
        self.assertEqual(len(items), 1)
        self.assertEqual(request.call_count, 1)

    def test_a_missing_key_reads_as_an_empty_page(self):
        with mock.patch.object(self.client, "request", return_value={}):
            self.assertEqual(self.client.paginate("/x", {}, "items"), [])

    def test_the_page_cap_stops_an_endless_walk(self):
        endless = {"items": [{"id": 1}, {"id": 2}],
                   "meta": {"nextPageUrl": "https://api.example.com/next"}}
        with mock.patch.object(self.client, "request", return_value=endless) as request:
            items = self.client.paginate("/x", {}, "items")
        self.assertEqual(request.call_count, self.client.max_pages)
        # And the caller can tell it was truncated rather than under-reporting.
        self.assertTrue(self.client.hit_page_cap(items))


if __name__ == "__main__":
    unittest.main()
