"""K0 ikinci dilim: erişim hatalarının adres adres sabit nedenle sınıflanmasını sınar (robots, ret, yönlendirme, ağ)."""

from contextlib import closing
from email.utils import formatdate
import http.client
from pathlib import Path
import sqlite3
import ssl
import tempfile
import time
import unittest
from unittest.mock import patch

from radar import toplayici
from radar.kaynak_http import HTTPPolicyError, request_variant
from radar.kaynak_izleme import SourceTracker, TrackingSource, error_class, error_code
from radar.toplayici import CollectionError, FetchResult

URL = "https://esphome.io/components/wifi.html"
PAGE = b'<main><h1>Title</h1><p>Public text.</p></main>'


class StatusCodeTests(unittest.TestCase):
    def read(self, status, location=None):
        headers = {"content-type": "text/html"}
        if location is not None:
            headers["location"] = location
        result = FetchResult(status, b"", headers, URL)
        with patch.object(toplayici, "_robots_allowed", return_value=True), \
                patch.object(toplayici, "_request_once", return_value=result), self.assertRaises(CollectionError) as caught:
            toplayici.anonim_belge_oku(URL, request_variant("en"))
        return caught.exception.code

    def test_redirects_are_classified_by_target_relation_without_following(self):
        for location, code in (("/components/wifi/", "redirect_html_suffix"), ("https://esphome.io/components/wifi.html/", "redirect_trailing_slash"),
                               ("/docs/wifi", "redirect_same_host"), ("https://other.example.org/wifi", "redirect_other_host"),
                               (None, "redirect_no_location")):
            with self.subTest(location=location):
                self.assertEqual(self.read(301, location), code)
        self.assertEqual(self.read(308, "/components/wifi/"), "redirect_html_suffix")

    def test_explicit_refusals_and_server_errors(self):
        for status, code in ((401, "http_forbidden"), (403, "http_forbidden"), (451, "http_forbidden"), (404, "http_not_found"),
                             (410, "http_not_found"), (429, "http_rate_limited"), (500, "http_server_error"), (503, "http_server_error"),
                             (204, "http_status_other")):
            with self.subTest(status=status):
                self.assertEqual(self.read(status), code)

    def test_robots_refusal_and_robots_failures_are_prefixed(self):
        with patch.object(toplayici, "_robots_allowed", return_value=False), patch.object(toplayici, "_request_once") as request, \
                self.assertRaises(CollectionError) as caught:
            toplayici.anonim_belge_oku(URL, request_variant("en"))
        self.assertEqual(caught.exception.code, "robots_disallowed")
        request.assert_not_called()
        for raised, code in ((CollectionError("x", code="connection_closed"), "robots_connection_closed"),
                             (CollectionError("x", code="unavailable"), "robots_unavailable"),
                             (CollectionError("x"), "robots_unavailable")):
            with self.subTest(code=code), patch.object(toplayici, "_robots_allowed", side_effect=raised), \
                    patch.object(toplayici, "_request_once") as request, self.assertRaises(CollectionError) as caught:
                toplayici.anonim_belge_oku(URL, request_variant("en"))
            self.assertEqual(caught.exception.code, code)
            request.assert_not_called()

    def test_robots_http_status_maps_to_refusal_or_unavailable(self):
        toplayici._ROBOTS_CACHE.clear()
        self.addCleanup(toplayici._ROBOTS_CACHE.clear)
        for status, expected in ((403, "robots_disallowed"), (503, "robots_unavailable")):
            toplayici._ROBOTS_CACHE.clear()
            robots = FetchResult(status, b"", {}, "https://esphome.io/robots.txt")
            with self.subTest(status=status), patch.object(toplayici, "_fetch", return_value=robots), \
                    patch.object(toplayici, "_request_once") as request, self.assertRaises(CollectionError) as caught:
                toplayici.anonim_belge_oku(URL, request_variant("en"))
            self.assertEqual(caught.exception.code, expected)
            request.assert_not_called()

    def test_message_text_is_unchanged(self):
        error = CollectionError("Kaynak bağlantısı kurulamadı.", code="timeout")
        self.assertEqual(str(error), "Kaynak bağlantısı kurulamadı.")
        self.assertIsNone(CollectionError("eski kullanım").code)


class ConnectionCodeTests(unittest.TestCase):
    def test_low_level_failures_map_to_fixed_codes(self):
        future = time.monotonic() + 60
        for error, code in ((ssl.SSLEOFError(8, "EOF occurred"), "connection_closed"),
                            (http.client.RemoteDisconnected("closed"), "connection_closed"),
                            (ConnectionResetError(), "connection_closed"), (ConnectionRefusedError(), "connection_refused"),
                            (TimeoutError(), "timeout"), (ssl.SSLError(1, "handshake failure"), "tls_error"),
                            (http.client.IncompleteRead(b""), "http_protocol_error"), (OSError(), "connection_failed")):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(toplayici._connection_code(error, future), code)
        self.assertEqual(toplayici._connection_code(OSError(), time.monotonic() - 1), "timeout")

    def test_request_once_carries_the_code_and_keeps_the_cause(self):
        class Connection:
            def __init__(self, *args, **kwargs):
                pass

            def connect(self):
                raise ssl.SSLEOFError(8, "EOF occurred in violation of protocol")

            def close(self):
                pass

        with patch.object(toplayici, "_resolve_public_host", return_value=(2, ("192.0.2.1", 443))), \
                patch.object(toplayici, "_PinnedHTTPSConnection", Connection), \
                patch.object(toplayici, "APPROVED_FETCH_HOSTS", frozenset({"esphome.io"})), self.assertRaises(CollectionError) as caught:
            toplayici._request_once(URL, {"esphome.io"}, {}, time.monotonic() + 30)
        self.assertEqual(caught.exception.code, "connection_closed")
        self.assertIsInstance(caught.exception.__cause__, ssl.SSLEOFError)

    def test_certificate_failure_is_a_security_block(self):
        class Connection:
            def __init__(self, *args, **kwargs):
                pass

            def connect(self):
                raise ssl.SSLCertVerificationError("certificate verify failed")

            def close(self):
                pass

        with patch.object(toplayici, "_resolve_public_host", return_value=(2, ("192.0.2.1", 443))), \
                patch.object(toplayici, "_PinnedHTTPSConnection", Connection), \
                patch.object(toplayici, "APPROVED_FETCH_HOSTS", frozenset({"esphome.io"})), self.assertRaises(CollectionError) as caught:
            toplayici._request_once(URL, {"esphome.io"}, {}, time.monotonic() + 30)
        self.assertEqual(caught.exception.code, "tls_certificate")
        self.assertEqual(error_class("tls_certificate", "fetch"), "security_block")


class TrackerErrorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "tracker.sqlite3"
        self.now = 1793970000.0
        self.source = TrackingSource("document", "https://www.postgresql.org/docs/18/example.html")
        self.failure = None
        self.body = PAGE
        self.headers = {}

    def fetch(self, source, *, etag=None, modified=None):
        if self.failure is not None:
            raise self.failure
        headers = {"content-type": "text/html; charset=utf-8", "cache-control": "public, max-age=600",
                   "date": formatdate(self.now, usegmt=True), **self.headers}
        return FetchResult(200, self.body, headers, source["url"]), {
            "sent_etag": etag, "sent_modified": modified, "request_variant": request_variant(source["language"]),
            "request_started_at": self.now, "response_received_at": self.now}

    def tracker(self):
        return SourceTracker(self.path, [self.source], fetcher=self.fetch, clock=lambda: self.now)

    def check(self, tracker):
        self.now += 120  # Hata beklemesi (60 sn) geçsin.
        return tracker.check("document", max_age_seconds=0)

    def test_each_failure_stage_is_recorded_with_code_and_class_without_message(self):
        tracker = self.tracker()
        cases = ((CollectionError("SECRET message", code="robots_disallowed"), ("robots_disallowed", "fetch", "source_refused")),
                 (CollectionError("SECRET", code="redirect_html_suffix"), ("redirect_html_suffix", "fetch", "address_changed")),
                 (CollectionError("SECRET", code="connection_closed"), ("connection_closed", "fetch", "network_failure")),
                 (CollectionError("SECRET", code="robots_timeout"), ("robots_timeout", "fetch", "network_failure")),
                 (CollectionError("SECRET", code="http_rate_limited"), ("http_rate_limited", "fetch", "rate_limited")),
                 (CollectionError("SECRET"), ("unclassified_error", "fetch", "unclassified")),
                 (HTTPPolicyError("private_cache_policy"), ("private_cache_policy", "policy", "policy_refused")),
                 (ValueError("SECRET no code"), ("unclassified_error", "fetch", "unclassified")))
        for failure, expected in cases:
            with self.subTest(code=expected[0]):
                self.failure = failure
                result = self.check(tracker)
                self.assertFalse(result["check_succeeded"])
                self.assertEqual((result["error_code"], result["error_stage"], result["error_class"]), expected)
                self.assertNotIn("SECRET", repr(result))
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertNotIn("SECRET", "\n".join(connection.iterdump()))

    def test_extraction_failure_and_recovery_clear_the_code(self):
        tracker = self.tracker()
        self.body = b"<div>no root</div>"
        failed = self.check(tracker)
        self.assertEqual((failed["error_code"], failed["error_stage"], failed["error_class"]),
                         ("ambiguous_document_root", "extract", "unsupported_document"))
        self.body = PAGE
        recovered = self.check(tracker)
        self.assertTrue(recovered["check_succeeded"])
        self.assertEqual((recovered["error_code"], recovered["error_stage"], recovered["error_class"]), (None, None, None))

    def test_policy_refusal_during_fetch_is_reported_as_policy(self):
        tracker = self.tracker()
        self.headers = {"cache-control": "private"}
        result = self.check(tracker)
        self.assertEqual((result["error_code"], result["error_stage"]), ("private_cache_policy", "policy"))

    def test_existing_first_slice_database_gets_empty_code_columns(self):
        tracker = self.tracker()
        self.check(tracker)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("ALTER TABLE k0_sources DROP COLUMN error_code")
            connection.execute("ALTER TABLE k0_sources DROP COLUMN error_stage")
        reopened = self.tracker()
        state = reopened.read("document", max_age_seconds=0)
        self.assertIsNone(state["error_code"])
        with closing(sqlite3.connect(self.path)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(k0_sources)")}
        self.assertTrue({"error_code", "error_stage"} <= columns)

    def test_error_code_helper_rejects_free_text(self):
        self.assertEqual(error_code(CollectionError("x", code="Not A Code!")), "unclassified_error")
        self.assertEqual(error_code(HTTPPolicyError("invalid_etag")), "invalid_etag")
        self.assertEqual(error_class("robots_disallowed", "fetch"), "source_refused")
        self.assertEqual(error_class("robots_unavailable", "fetch"), "server_error")
        self.assertEqual(error_class("something_new", "fetch"), "unclassified")


if __name__ == "__main__":
    unittest.main()
