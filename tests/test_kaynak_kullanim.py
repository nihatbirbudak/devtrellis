"""K0 üçüncü dilim: kaynak kullanım sinyalleri (H7), adres kanonikleştirmesi ve istek süresi sınırını sınar."""

from contextlib import closing
from email.utils import formatdate
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from radar import kaynak_http, toplayici
from radar.kaynak_http import request_variant
from radar.kaynak_izleme import SourceTracker, TrackingSource
from radar.kaynak_kimligi import KaynakKimligiHatasi, address_key, canonical_source_url
from radar.kaynak_kullanim import meta_robots, parse_content_signal, parse_directives, summarize
from radar.paylasim_gizliligi import sorguyu_dogrula
from radar.toplayici import FetchResult

URL = "https://www.postgresql.org/docs/18/example.html"
PAGE = b'<html><head><meta name="robots" content="noarchive"></head><body><main><h1>Title</h1><p>Public text.</p></main></body></html>'


class ContentSignalTests(unittest.TestCase):
    def test_robots_lines_are_chosen_by_agent_group_like_robotparser(self):
        text = "\n".join(["Content-Signal: search=yes", "User-agent: Googlebot", "Content-Signal: ai-train=yes",
                          "Disallow: /x", "", "User-agent: *", "Content-Signal: search=yes, ai-input=no, ai-train=no",
                          "Allow: /"])
        self.assertEqual(toplayici._content_signals(text), ("search=yes", "search=yes, ai-input=no, ai-train=no"))
        own = text + "\nUser-agent: UfukRadar\nContent-Signal: ai-input=yes # bize özel\n"
        self.assertEqual(toplayici._content_signals(own), ("search=yes", "ai-input=yes"))
        self.assertEqual(toplayici._content_signals("User-agent: *\nDisallow:\n"), ())

    def test_content_signal_values_and_unknown_are_ambiguous_never_permission(self):
        self.assertIsNone(parse_content_signal(None))
        parsed = parse_content_signal(["search=yes, ai-input=no, ai-train=no"])
        self.assertEqual((parsed["search"], parsed["ai_input"], parsed["ai_train"], parsed["unrecognized"]), ("yes", "no", "no", False))
        odd = parse_content_signal(["ai-input=maybe, future-use=no"])
        self.assertEqual((odd["ai_input"], odd["unrecognized"]), ("unknown", True))
        conflict = parse_content_signal(["ai-input=yes", "ai-input=no"])
        self.assertEqual(conflict["ai_input"], "unknown")
        absent = parse_content_signal([])
        self.assertEqual((absent["present"], absent["ai_input"]), (False, None))

    def test_robots_cache_carries_signals_without_extra_request(self):
        toplayici._ROBOTS_CACHE.clear()
        self.addCleanup(toplayici._ROBOTS_CACHE.clear)
        robots = FetchResult(200, b"User-agent: *\nContent-Signal: ai-input=no\nAllow: /\n", {}, "https://www.postgresql.org/robots.txt")
        page = FetchResult(200, PAGE, {"content-type": "text/html", "x-robots-tag": "googlebot: noindex, ufukradar: nosnippet"}, URL)
        with patch.object(toplayici, "_fetch", return_value=robots) as fetch, patch.object(toplayici, "_request_once", return_value=page):
            response, _ = kaynak_http.fetch_document(TrackingSource("document", URL).http_source())
        fetch.assert_called_once()
        self.assertEqual(toplayici.robots_content_signals(URL), ("ai-input=no",))
        self.assertEqual(response.usage["robots_content_signal"]["ai_input"], "no")
        self.assertEqual(response.usage["x_robots_tag"]["directives"], ["nosnippet"])
        self.assertIsNone(toplayici.robots_content_signals("https://esphome.io/x"))

    def test_robots_404_means_checked_without_signal(self):
        toplayici._ROBOTS_CACHE.clear()
        self.addCleanup(toplayici._ROBOTS_CACHE.clear)
        with patch.object(toplayici, "_fetch", return_value=FetchResult(404, b"", {}, "https://www.postgresql.org/robots.txt")), \
                patch.object(toplayici, "_request_once", return_value=FetchResult(200, PAGE, {"content-type": "text/html"}, URL)):
            response, _ = kaynak_http.fetch_document(TrackingSource("document", URL).http_source())
        self.assertEqual(response.usage["robots_content_signal"]["present"], False)


class DirectiveTests(unittest.TestCase):
    def test_header_scoping_valued_directives_and_unknown_tokens(self):
        parsed = parse_directives(["googlebot: noindex, nofollow", "noarchive, max-snippet:0, unavailable_after: 2030-01-01"], scoped=True)
        self.assertEqual(parsed["directives"], ["max-snippet:0", "noarchive"])
        self.assertFalse(parsed["unrecognized"])
        self.assertTrue(parse_directives(["noai, futuretoken"], scoped=True)["unrecognized"])
        self.assertEqual(parse_directives(["UfukRadar: none"], scoped=True)["directives"], ["none"])
        self.assertEqual(parse_directives([], scoped=True)["present"], False)

    def test_meta_robots_reads_only_head_robots_and_own_agent(self):
        body = (b'<html><head><meta name="ROBOTS" content="noindex"><meta name="googlebot" content="noarchive">'
                b'<meta name="ufukradar" content="nosnippet"></head><body><meta name="robots" content="none"></body></html>')
        self.assertEqual(meta_robots(body)["directives"], ["noindex", "nosnippet"])
        self.assertEqual(meta_robots(b"<html><head></head><body>x</body></html>")["present"], False)

    def test_summary_restricts_on_any_source_and_marks_unknown_ambiguous(self):
        signal = parse_content_signal(["search=yes, ai-input=no, ai-train=no"])
        result = summarize(signal, parse_directives([], scoped=True), meta_robots(b"<head></head>"), checked_at=1.0)
        self.assertEqual((result["model_input"], result["model_training"], result["quotation"], result["search_index"]),
                         ("restricted", "restricted", "not_restricted", "not_restricted"))
        archive = summarize(parse_content_signal([]), parse_directives(["noarchive"], scoped=True), None, checked_at=1.0)
        self.assertEqual(archive["model_input"], "restricted")
        unknown = summarize(parse_content_signal(["ai-input=perhaps"]), parse_directives([], scoped=True), None, checked_at=1.0)
        self.assertEqual(unknown["model_input"], "ambiguous")
        self.assertEqual(summarize(None, None, None, checked_at=1.0)["model_input"], "not_checked")
        nocache = summarize(parse_content_signal([]), parse_directives(["nocache"], scoped=True), None, checked_at=1.0)
        self.assertEqual(nocache["model_input"], "restricted")
        snippet = summarize(parse_content_signal([]), parse_directives(["nosnippet"], scoped=True), None, checked_at=1.0)
        self.assertEqual(snippet["quotation"], "restricted")


class TrackerUsageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "tracker.sqlite3"
        self.now = 1793970000.0
        self.status = 200
        self.tag = "noai"

    def fetch(self, source, *, etag=None, modified=None):
        headers = {"content-type": "text/html; charset=utf-8", "cache-control": "public, max-age=0",
                   "date": formatdate(self.now, usegmt=True), "etag": '"v1"'}
        response = kaynak_http.DocumentResponse(self.status, PAGE if self.status == 200 else b"", headers, source["url"], False,
                                                {"robots_content_signal": parse_content_signal(["ai-train=no"]),
                                                 "x_robots_tag": parse_directives([self.tag] if self.tag else [], scoped=True)})
        return response, {"sent_etag": etag, "sent_modified": modified, "request_variant": request_variant(source["language"]),
                          "request_started_at": self.now, "response_received_at": self.now}

    def test_signals_are_recorded_carried_over_304_and_hold_no_raw_text(self):
        tracker = SourceTracker(self.path, [TrackingSource("document", URL)], fetcher=self.fetch, clock=lambda: self.now)
        first = tracker.check("document", max_age_seconds=0)
        usage = first["usage_signals"]
        self.assertEqual(usage["meta_robots"]["directives"], ["noarchive"])
        self.assertEqual(usage["x_robots_tag"]["directives"], ["noai"])
        self.assertEqual((usage["model_input"], usage["model_training"]), ("restricted", "restricted"))
        self.now += 10
        self.status, self.tag = 304, None
        second = tracker.check("document", max_age_seconds=0)
        self.assertEqual(second["method"], "conditional_304")
        self.assertEqual(second["usage_signals"]["meta_robots"]["directives"], ["noarchive"])
        self.assertEqual(second["usage_signals"]["x_robots_tag"]["directives"], ["noai"])
        with closing(sqlite3.connect(self.path)) as connection:
            dump = "\n".join(connection.iterdump())
        self.assertNotIn("Public text", dump)
        self.assertNotIn("<meta", dump)

    def test_plain_fetch_result_without_signals_is_not_permission(self):
        def fetch(source, *, etag=None, modified=None):
            headers = {"content-type": "text/html", "date": formatdate(self.now, usegmt=True)}
            return FetchResult(200, b"<main><h1>T</h1><p>x</p></main>", headers, source["url"]), {
                "sent_etag": etag, "sent_modified": modified, "request_variant": request_variant(source["language"]),
                "request_started_at": self.now, "response_received_at": self.now}
        tracker = SourceTracker(self.path, [TrackingSource("document", URL)], fetcher=fetch, clock=lambda: self.now)
        usage = tracker.check("document", max_age_seconds=0)["usage_signals"]
        self.assertFalse(usage["checked"]["robots_content_signal"])
        self.assertTrue(usage["checked"]["meta_robots"])
        self.assertEqual(usage["model_input"], "not_restricted")


class CanonicalAddressTests(unittest.TestCase):
    def test_version_path_segment_with_at_sign_is_accepted(self):
        for url in ("https://docs.github.com/en/enterprise-server@3.14/admin/overview",
                    "https://docs.example.org/pkg/react@18.2.0/api"):
            with self.subTest(url=url):
                self.assertEqual(canonical_source_url(url), url)

    def test_mailbox_like_segments_are_still_rejected(self):
        for url in ("https://docs.example.org/x/user@example.com/y", "https://docs.example.org/x/me@10.0.0.1/y",
                    "https://user@docs.example.org/x"):
            with self.subTest(url=url), self.assertRaises(KaynakKimligiHatasi):
                canonical_source_url(url)
        for text in ("contact user@example.com", "a@b.co", "admin@10.0.0.1"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                sorguyu_dogrula(text)
        self.assertEqual(sorguyu_dogrula("enterprise-server@3.14"), "enterprise-server@3.14")

    def test_email_with_numeric_first_label_is_still_private(self):
        # Kabulün yeniden sınamasındaki sekiz vaka: sürüm yolları geçer, ilk etiketi rakam olan gerçek alanlar geçmez.
        for text in ("enterprise-server@3.14", "/en/enterprise-server@3.14/admin", "/pkg/react@18.2.0/api", "react@18.2.0"):
            with self.subTest(text=text):
                self.assertEqual(sorguyu_dogrula(text), text)
        for text in ("ali@3.14.com", "ali@123.example.com", "ali@12.ab", "ali@2024.org"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                sorguyu_dogrula(text)
        for url in ("https://docs.example.org/x/ali@3.14.com/y", "https://docs.example.org/x/ali@2024.org"):
            with self.subTest(url=url), self.assertRaises(KaynakKimligiHatasi):
                canonical_source_url(url)

    def test_dot_segments_resolve_to_one_key_and_encoded_or_escaping_dots_fail(self):
        base = "https://docs.example.org/a/b"
        for variant in ("https://docs.example.org/a/./b", "https://docs.example.org/a/c/../b", "https://docs.example.org/./a/b"):
            with self.subTest(url=variant):
                self.assertEqual(canonical_source_url(variant), base)
                self.assertEqual(address_key(variant, "en"), address_key(base, "en"))
        self.assertEqual(canonical_source_url("https://docs.example.org/a/."), "https://docs.example.org/a/")
        for url in ("https://docs.example.org/a/%2e/b", "https://docs.example.org/a/%2E%2E/b", "https://docs.example.org/../a"):
            with self.subTest(url=url), self.assertRaises(KaynakKimligiHatasi):
                canonical_source_url(url)

    def test_catalog_must_use_the_resolved_form(self):
        with self.assertRaises(ValueError):
            TrackingSource("document", "https://www.postgresql.org/docs/18/./example.html")

    def test_query_rule_is_unchanged(self):
        self.assertEqual(canonical_source_url("https://docs.example.org/a?version=2"), "https://docs.example.org/a?version=2")
        with self.assertRaises(KaynakKimligiHatasi):
            canonical_source_url("https://docs.example.org/a?page=2")


class TimeoutTests(unittest.TestCase):
    def test_request_timeout_is_twenty_seconds_within_the_source_deadline(self):
        self.assertEqual(toplayici.REQUEST_TIMEOUT, 20.0)
        self.assertGreater(toplayici.SOURCE_TIMEOUT, toplayici.REQUEST_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
