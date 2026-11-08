"""Standalone köprünün gerçek yaşam döngüsü, dar HTTP yüzeyi ve hata temizliği."""

import asyncio
import http.client
import io
import json
import socket
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from radar import mcp_yerel


class LocalBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "mcp-data"

    def request(self, origin, path, method="GET", headers=None, body=None):
        address = urlsplit(origin)
        client = http.client.HTTPConnection(address.hostname, address.port, timeout=3)
        try:
            client.request(method, path, headers=headers or {}, body=body)
            result = client.getresponse()
            self.response_headers = dict(result.getheaders())
            return result.status, result.read()
        finally:
            client.close()

    def assert_closed(self, origin):
        with self.assertRaises((ConnectionError, OSError)):
            self.request(origin, "/api/health")

    def test_standalone_search_opts_into_remote_summary_without_credentials(self):
        from mcp_topluluk import CommunityClient, contribution_packet
        from radar.arastirma_ozeti import arama_ozeti

        packet = contribution_packet(title="Public report", summary="Summary with limitations.",
            body="Long published research body.", sources=[{"title": "Original", "url": "https://example.org/docs"}],
            model="unknown", provider="unknown", license="CC-BY-4.0")
        card = {"id": "a" * 32, "status": "published", "version": 1, "published_version": 1,
                "created_at": packet["created_at"], "updated_at": packet["updated_at"], "packet": packet}
        calls = []

        def remote(method, path, **kwargs):
            calls.append((method, path, kwargs))
            self.assertEqual(method, "GET")
            self.assertEqual(path, "/api/community/cards")
            self.assertEqual(kwargs["params"], {"query": "Public", "kind": "research", "limit": 3,
                                                "view": "summary", "search_mode": "ranked"})
            return arama_ozeti({"items": [card], "total": 1})

        with patch("radar.topluluk_baglantisi.ToplulukBaglantisi.request", side_effect=remote), \
                patch("radar.topluluk_baglantisi.IsletimSistemiKasasi.get", side_effect=AssertionError("No credentials")):
            with mcp_yerel.yerel_kopru(self.directory) as origin:
                result = CommunityClient(origin).search("Public", 3)
                self.assertEqual(result["view"], "summary")
                self.assertNotIn("body", result["items"][0]["packet"])
                self.assertEqual(result["items"][0]["packet"]["sources"], packet["sources"])
                self.assertEqual(len(calls), 1)
        self.assert_closed(origin)

    def test_standalone_passes_k1_contract_endpoints_through(self):
        from mcp_topluluk import CommunityClient

        coverage = {"schema": "devtrellis-coverage", "schema_version": 1, "identity_version": "k0-address-v1",
                    "library_revision": 3, "generated_at": "2026-11-07T00:00:00Z", "complete": True,
                    "addresses": [], "tombstones": []}
        lookup = {"schema": "devtrellis-source-lookup", "schema_version": 1, "address_key": "a" * 64,
                  "library_revision": 3, "result": "not_found", "answer_eligible": False,
                  "version_match": "not_applicable", "cards": [], "candidates": [], "origin_fetches": 0,
                  "model_calls": 0, "tombstone": None, "freshness": {"state": "unknown", "checked_at": None}}
        seen = []

        def remote(_bridge, path, *, params=None, etag=None):
            seen.append((path, params, etag))
            if etag == '"r3"':
                return 304, None, {"etag": '"r3"'}
            if path.startswith("/api/community/sources/"):
                return 404, lookup, {}
            return 200, coverage, {"etag": '"r3"'}

        with patch("radar.topluluk_baglantisi.ToplulukBaglantisi.sozlesme_oku", autospec=True, side_effect=remote):
            with mcp_yerel.yerel_kopru(self.directory) as origin:
                client = CommunityClient(origin)
                status, body, etag = client.contract_get("/api/platform/community/coverage")
                self.assertEqual((status, body["library_revision"], etag), (200, 3, '"r3"'))
                self.assertEqual(client.contract_get("/api/platform/community/coverage", etag='"r3"')[0], 304)
                status, body, _ = client.contract_get("/api/platform/community/sources/" + "a" * 64, {"product": "widget"})
                self.assertEqual((status, body["result"]), (404, "not_found"))
                self.assertEqual(seen[-1], ("/api/community/sources/" + "a" * 64, {"product": "widget"}, None))
                self.assertEqual(self.request(origin, "/api/platform/community/sources/not-a-key")[0], 400)
                self.assertEqual({k.lower(): v for k, v in self.response_headers.items()}.get("cache-control"), "no-store")
        self.assert_closed(origin)

    def test_standalone_exact_report_read_sends_only_one_remote_get(self):
        from mcp_topluluk import CommunityClient, contribution_packet

        packet = contribution_packet(title="Versioned public report", summary="A public summary.",
            body="Full body of the requested revision.", sources=[{"title": "Original", "url": "https://example.org/docs"}],
            model="unknown", provider="unknown", license="CC-BY-4.0")
        card = {"id": "b" * 32, "status": "published", "version": 2, "published_version": 2,
                "created_at": packet["created_at"], "updated_at": packet["updated_at"], "packet": packet}
        calls = []

        def remote(method, path, **kwargs):
            calls.append((method, path, kwargs))
            self.assertEqual((method, path), ("GET", "/api/community/cards/" + card["id"]))
            self.assertEqual(kwargs.get("params"), {"version": 2})
            return card

        with patch("radar.topluluk_baglantisi.ToplulukBaglantisi.request", side_effect=remote), \
                patch("radar.topluluk_baglantisi.IsletimSistemiKasasi.get", side_effect=AssertionError("No credentials")):
            with mcp_yerel.yerel_kopru(self.directory) as origin:
                result = CommunityClient(origin).read(card["id"], 2)
                self.assertEqual(result["packet"]["body"], packet["body"])
                self.assertEqual(result["version"], 2)
                self.assertNotIn("access_counted", result)
                self.assertEqual(len(calls), 1)
        self.assert_closed(origin)

    def test_real_lifecycle_no_egress_no_models_no_background_workers(self):
        instances = []
        real_app = mcp_yerel._application
        real_connect = socket.socket.connect
        outbound = []

        def app(*args, **kwargs):
            instance = real_app(*args, **kwargs)
            instances.append(instance)
            return instance

        def loopback_only(sock, address):
            if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1"}:
                outbound.append(address)
                raise AssertionError("Standalone startup attempted nonlocal network access")
            return real_connect(sock, address)

        output = io.StringIO()
        with redirect_stdout(output), patch.object(mcp_yerel, "_application", side_effect=app), \
                patch.object(socket.socket, "connect", new=loopback_only), \
                patch("subprocess.Popen", side_effect=AssertionError("No child processes")), \
                patch("radar.zeka.kimlik_bilgisi", side_effect=AssertionError("No keyring access")):
            with mcp_yerel.yerel_kopru(self.directory) as origin:
                self.assertEqual(urlsplit(origin).hostname, "127.0.0.1")
                status, body = self.request(origin, "/api/health")
                self.assertEqual(status, 200)
                self.assertFalse(json.loads(body)["manual_ai"])
                status, body = self.request(origin, "/api/platform/community/session")
                self.assertEqual(status, 200)
                self.assertEqual(set(json.loads(body)), {"csrf_token", "destination", "search_views", "search_modes", "source_memory"})
                self.assertEqual(json.loads(body)["search_views"], ["full", "summary"])
                self.assertEqual(self.request(origin, "/static/contribution-review.html")[0], 200)
                for path in ("/static/contribution-submissions.html", "/static/contribution-submissions.js",
                             "/static/confirm-dialog.js", "/static/confirm-dialog.css"):
                    self.assertEqual(self.request(origin, path)[0], 200)
                self.assertEqual(json.loads(self.request(origin, "/api/platform/community/submissions")[1])["items"], [])
                for path in ("/", "/api/providers", "/api/state", "/api/platform", "/api/research-requests",
                             "/static/platform.html", "/static/../index.html"):
                    with self.subTest(path=path):
                        self.assertEqual(self.request(origin, path)[0], 404)
                self.assertEqual(self.request(origin, "/api/providers", "POST")[0], 404)
                self.assertEqual(self.request(origin, "/api/platform/community/session",
                                              headers={"Origin": "https://example.org"})[0], 403)
                state = instances[0].state
                for name in ("reading", "ai", "workflow", "discovery", "research_requests", "source_scout",
                             "collection", "backup"):
                    self.assertFalse(hasattr(state, name), name + " must not be constructed")
                with closing(sqlite3.connect(self.directory / "research.sqlite3")) as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM sources").fetchone()[0], 0)
                    self.assertEqual(db.execute("SELECT count(*) FROM items").fetchone()[0], 0)
            self.assert_closed(origin)
            # Reopening the same owned directory does not attach to another workspace.
            with mcp_yerel.yerel_kopru(self.directory) as reopened:
                self.assertEqual(self.request(reopened, "/api/health")[0], 200)
        self.assertEqual(outbound, [])
        self.assertEqual(output.getvalue(), "")

    def test_two_simultaneous_hosts_share_owned_data_without_lifecycle_conflicts(self):
        with mcp_yerel.yerel_kopru(self.directory) as first:
            with mcp_yerel.yerel_kopru(self.directory) as second:
                self.assertNotEqual(first, second)
                self.assertEqual(self.request(first, "/api/health")[0], 200)
                self.assertEqual(self.request(second, "/api/health")[0], 200)
                first_session = json.loads(self.request(first, "/api/platform/community/session")[1])
                second_session = json.loads(self.request(second, "/api/platform/community/session")[1])
                self.assertNotEqual(first_session["csrf_token"], second_session["csrf_token"])
            self.assert_closed(second)
            self.assertEqual(self.request(first, "/api/health")[0], 200)
        self.assert_closed(first)

    def test_local_origin_csrf_body_limit_and_response_headers(self):
        with mcp_yerel.yerel_kopru(self.directory) as origin:
            session = json.loads(self.request(origin, "/api/platform/community/session")[1])
            lowered = {key.lower(): value for key, value in self.response_headers.items()}
            self.assertEqual(lowered["cache-control"], "no-store")
            self.assertIn("frame-ancestors 'none'", lowered["content-security-policy"])
            for headers in ({"Host": "example.org"}, {"Origin": "https://example.org"},
                            {"Sec-Fetch-Site": "cross-site"}):
                self.assertEqual(self.request(origin, "/api/platform/community/session", headers=headers)[0], 403)
            path = "/api/platform/community/preview"
            self.assertEqual(self.request(origin, path, "POST", body=b"{}")[0], 403)
            valid = {"Origin": origin, "X-Radar-CSRF": session["csrf_token"], "Content-Type": "application/json"}
            self.assertEqual(self.request(origin, path, "POST", valid, body=b"{}")[0], 422)
            self.assertEqual(self.request(origin, path, "POST", {**valid, "Content-Type": "text/plain"}, b"{}")[0], 415)
            self.assertEqual(self.request(origin, path, "POST", {**valid, "Content-Length": "40000"}, b"{}")[0], 413)
            self.assertEqual(self.request(origin, "/api/platform/community/review/preview-1")[0], 403)
            for operation in ("refresh", "withdraw"):
                self.assertEqual(self.request(origin, "/api/platform/community/submissions/" + "a" * 32 + "/" + operation,
                                              "POST", {"Content-Type": "application/json"}, b"{}")[0], 403)

    def test_body_exception_closes_only_owned_listener(self):
        unrelated = socket.socket()
        self.addCleanup(unrelated.close)
        unrelated.bind(("127.0.0.1", 0))
        unrelated.listen(1)
        with self.assertRaisesRegex(ValueError, "caller failure"):
            with mcp_yerel.yerel_kopru(self.directory) as origin:
                raise ValueError("caller failure")
        self.assert_closed(origin)
        self.assertGreaterEqual(unrelated.fileno(), 0)
        with socket.create_connection(unrelated.getsockname(), timeout=2):
            connection, _ = unrelated.accept()
            connection.close()

    def test_server_start_failure_releases_socket_and_app_locks(self):
        listeners = []

        class FailedServer:
            started = False
            def __init__(self, config):
                pass
            async def serve(self, sockets):
                listeners.extend(sockets)
                raise OSError("injected startup failure")

        with patch.object(mcp_yerel.uvicorn, "Server", FailedServer):
            with self.assertRaisesRegex(RuntimeError, "Could not start") as result:
                with mcp_yerel.yerel_kopru(self.directory):
                    self.fail("A failed server must not yield an origin")
        self.assertIsInstance(result.exception.__cause__, OSError)
        self.assertEqual(listeners[0].fileno(), -1)
        with mcp_yerel.yerel_kopru(self.directory) as origin:
            self.assertEqual(self.request(origin, "/api/health")[0], 200)

    def test_config_failure_still_closes_application_lifespan(self):
        with patch.object(mcp_yerel.uvicorn, "Config", side_effect=ValueError("invalid configuration")):
            with self.assertRaisesRegex(RuntimeError, "Could not start"):
                with mcp_yerel.yerel_kopru(self.directory):
                    self.fail("Invalid configuration must not yield an origin")
        with mcp_yerel.yerel_kopru(self.directory) as origin:
            self.assertEqual(self.request(origin, "/api/health")[0], 200)

    def test_start_timeout_cancels_only_owned_task_and_releases_locks(self):
        stopped = []

        class SlowServer:
            started = False
            def __init__(self, config):
                pass
            async def serve(self, sockets):
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.append(True)

        before = time.monotonic()
        with patch.object(mcp_yerel.uvicorn, "Server", SlowServer), \
                patch.object(mcp_yerel, "START_TIMEOUT", 0.03), patch.object(mcp_yerel, "STOP_TIMEOUT", 0.03):
            with self.assertRaisesRegex(RuntimeError, "Timed out"):
                with mcp_yerel.yerel_kopru(self.directory):
                    self.fail("Unstarted server must not yield an origin")
        self.assertLess(time.monotonic() - before, 2)
        self.assertEqual(stopped, [True])
        with mcp_yerel.yerel_kopru(self.directory) as origin:
            self.assertEqual(self.request(origin, "/api/health")[0], 200)

    def test_existing_personal_directory_is_never_opened_or_modified(self):
        self.directory.mkdir()
        personal = self.directory / "research.sqlite3"
        personal.write_bytes(b"unrelated personal database")
        with self.assertRaisesRegex(ValueError, "existing personal data"):
            with mcp_yerel.yerel_kopru(self.directory):
                self.fail("An unowned database must never be opened")
        self.assertEqual(personal.read_bytes(), b"unrelated personal database")
        self.assertEqual(list(self.directory.iterdir()), [personal])

    def test_invalid_ownership_marker_is_not_accepted(self):
        self.directory.mkdir()
        marker = self.directory / mcp_yerel.OWNER_FILE
        marker.write_text('{"application":"another-app","schema":1}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "ownership record"):
            with mcp_yerel.yerel_kopru(self.directory):
                self.fail("Invalid owner must never be accepted")
        self.assertFalse((self.directory / "research.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
