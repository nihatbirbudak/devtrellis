"""Ortak kaynak hafızasının talep, sürüm, gizlilik, eşzamanlılık ve güvenli ağ sınırlarını sınar."""

import http.client
import io
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from radar import toplayici
from radar.kaynak_hafizasi import (ERROR_BACKOFF_SECONDS, EXTRACTOR_VERSION, LEASE_SECONDS,
                                  KaynakHafizasi, KaynakTanimi, kaynak_metnini_ayristir)


URL = "https://blog.youtube/news-and-events/source-memory-pilot/"
TEXT = ("This public technology article describes a bounded source comparison. "
        "A stable page can share its already reviewed findings across readers. "
        "The publisher explains that unchanged content does not prove that every claim is correct. "
        "Further evidence can still require a separate investigation beyond this document.")


def response(*, status=200, text=TEXT, title="Public source comparison and limitations", headers=None, url=URL):
    body = f"<article><h1>{title}</h1><p>{text}</p></article>".encode() if status == 200 else b""
    return toplayici.FetchResult(status, body, {"content-type": "text/html; charset=utf-8", **(headers or {})}, url)


def summary(**changes):
    return {"summary": "Unchanged public pages can reuse a reviewed summary.",
            "findings": ["The control applies to the checked representation only."],
            "scope": "This public document and its version.", "limitations": "Claims are not independently verified.",
            "sections": ["Source comparison"], "model": "unknown", "generated_at": "2026-11-05T20:00:00Z",
            "license": "CC-BY-4.0", **changes}


class KaynakHafizasiTestleri(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "memory.sqlite3"
        self.now = [100000.0]
        self.source = KaynakTanimi("pilot", URL)
        self.fetch = Mock(return_value=response(headers={"etag": '"one"'}))
        self.memory = KaynakHafizasi(self.path, [self.source], fetcher=self.fetch, clock=lambda: self.now[0])

    def publish(self, memory=None):
        memory = memory or self.memory
        current = memory.oku("pilot")
        return memory.ozet_kaydet("pilot", expected_revision=current["source_revision"],
                                 expected_hash=current["content_hash"], summary=summary(), approved_public=True)

    def test_constructor_read_and_optional_check_never_fetch(self):
        self.assertEqual(self.memory.oku("pilot")["state"], "missing")
        result = self.memory.kontrol_et("pilot", freshness_required=False)
        self.assertFalse(result["check_started"])
        self.fetch.assert_not_called()

    def test_near_success_reuses_checked_result_without_another_request(self):
        initial = self.memory.kontrol_et("pilot")
        self.publish()
        self.now[0] += 2
        again = self.memory.kontrol_et("pilot")
        self.assertEqual(initial["source_revision"], 1)
        self.assertTrue(again["summary_reusable"])
        self.assertFalse(again["check_started"])
        self.assertEqual(again["model_calls"], 0)
        self.assertFalse(again["claims_verified"])
        self.assertEqual(self.fetch.call_count, 1)

    def test_conditional_304_reuses_hash_and_summary_but_not_claim_verification(self):
        initial = self.memory.kontrol_et("pilot")
        self.publish()
        self.now[0] += 4000
        self.fetch.return_value = response(status=304)
        result = self.memory.kontrol_et("pilot")
        self.assertEqual(self.fetch.call_args.kwargs, {"etag": '"one"', "modified": None})
        self.assertEqual(result["content_hash"], initial["content_hash"])
        self.assertEqual(result["source_revision"], 1)
        self.assertEqual(result["fetched_at"], initial["fetched_at"])
        self.assertEqual(result["checked_at"], self.now[0])
        self.assertTrue(result["summary_reusable"])
        self.assertEqual(result["method"], "conditional_304")

    def test_validator_absent_same_text_and_html_noise_do_not_invalidate_summary(self):
        self.fetch.return_value = response()
        self.memory.kontrol_et("pilot")
        self.publish()
        self.now[0] += 4000
        original = response()
        self.fetch.return_value = toplayici.FetchResult(200, b"<nav>changed navigation</nav>" + original.body,
                                                       original.headers, URL)
        result = self.memory.kontrol_et("pilot")
        self.assertEqual(self.fetch.call_args.kwargs, {"etag": None, "modified": None})
        self.assertTrue(result["summary_reusable"])
        self.assertEqual(result["source_revision"], 1)
        self.assertEqual(result["fetched_at"], self.now[0])

    def test_generic_html_code_spacing_changes_source_hash_and_invalidates_summary(self):
        for wrapper in ("article", "main"):
            for template in ("<pre>SELECT 'a{space}b';</pre>", "<p>Example <code>'a{space}b'</code> literal.</p>",
                             "<pre><code>SELECT 'a{space}b';</code></pre>"):
                with self.subTest(wrapper=wrapper, template=template):
                    first = response(text=TEXT + template.format(space="  "))
                    second = response(text=TEXT + template.format(space=" "))
                    def wrapped(candidate):
                        return toplayici.FetchResult(candidate.status,
                            candidate.body.replace(b"article>", wrapper.encode() + b">"), candidate.headers, candidate.url)
                    first, second = wrapped(first), wrapped(second)
                    self.assertEqual(kaynak_metnini_ayristir(first)["body"], kaynak_metnini_ayristir(second)["body"])
                    self.fetch.return_value = first
                    self.now[0] += 4000
                    initial = self.memory.kontrol_et("pilot")
                    self.publish()
                    self.fetch.return_value = second
                    self.now[0] += 4000
                    changed = self.memory.kontrol_et("pilot")
                    self.assertNotEqual(initial["content_hash"], changed["content_hash"])
                    self.assertEqual(changed["source_revision"], initial["source_revision"] + 1)
                    self.assertEqual(changed["summary_status"], "stale")
                    self.assertFalse(changed["summary_reusable"])

    def test_changed_text_and_title_invalidate_old_summary_without_erasing_it(self):
        first = self.memory.kontrol_et("pilot")
        self.publish()
        self.now[0] += 4000
        self.fetch.return_value = response(text=TEXT + " A changed finding is now documented.")
        changed = self.memory.kontrol_et("pilot")
        self.assertEqual(changed["source_revision"], 2)
        self.assertEqual(changed["summary_status"], "stale")
        self.assertIsNone(changed["summary"])
        with self.assertRaises(ValueError):
            self.memory.ozet_kaydet("pilot", expected_revision=1, expected_hash=first["content_hash"],
                                   summary=summary(), approved_public=True)
        self.publish()
        self.now[0] += 4000
        self.fetch.return_value = response(text=TEXT + " A changed finding is now documented.", title="Changed public article heading")
        self.assertEqual(self.memory.kontrol_et("pilot")["source_revision"], 3)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM source_memory_summaries").fetchone()[0], 2)

    def test_summary_correction_preserves_source_revision_and_previous_summary(self):
        self.memory.kontrol_et("pilot")
        self.publish()
        corrected = self.publish()
        self.assertEqual(corrected["source_revision"], 1)
        self.assertEqual(corrected["summary"]["version"], 2)
        self.assertEqual(self.fetch.call_count, 1)

    def test_failure_preserves_success_time_blocks_reuse_and_backs_off(self):
        initial = self.memory.kontrol_et("pilot")
        self.publish()
        self.now[0] += 4000
        self.fetch.side_effect = RuntimeError("private upstream details and secret")
        result = self.memory.kontrol_et("pilot")
        self.assertEqual(result["state"], "error")
        self.assertEqual(result["checked_at"], initial["checked_at"])
        self.assertFalse(result["summary_reusable"])
        self.assertNotIn("private upstream", json.dumps(result))
        self.assertFalse(self.memory.kontrol_et("pilot")["check_started"])
        self.assertEqual(self.fetch.call_count, 2)
        self.now[0] += ERROR_BACKOFF_SECONDS + 1
        self.fetch.side_effect = None
        self.fetch.return_value = response(status=304)
        self.assertTrue(self.memory.kontrol_et("pilot")["fresh"])

    def test_distinct_instances_share_single_active_check(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(source, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return response()
        self.memory.fetcher = blocked
        peer_fetch = Mock(return_value=response())
        peer = KaynakHafizasi(self.path, [self.source], fetcher=peer_fetch, clock=lambda: self.now[0])
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.memory.kontrol_et, "pilot")
            try:
                self.assertTrue(entered.wait(5))
                result = peer.kontrol_et("pilot")
                self.assertEqual(result["state"], "checking")
                self.assertFalse(result["check_started"])
                peer_fetch.assert_not_called()
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5)["state"], "fresh")
        self.assertTrue(peer.kontrol_et("pilot")["fresh"])
        peer_fetch.assert_not_called()

    def test_expired_job_cannot_overwrite_new_owner_result(self):
        peer = KaynakHafizasi(self.path, [self.source], fetcher=Mock(return_value=response(text=TEXT + " New owner content.")),
                             clock=lambda: self.now[0])
        def late(source, **kwargs):
            self.now[0] += LEASE_SECONDS + 1
            self.assertTrue(peer.kontrol_et("pilot")["fresh"])
            return response(text=TEXT + " Obsolete result.")
        self.memory.fetcher = late
        result = self.memory.kontrol_et("pilot")
        self.assertTrue(result["ownership_lost"])
        expected = KaynakHafizasi.yaniti_hazirla(self.source, None, response(text=TEXT + " New owner content."))
        self.assertEqual(result["content_hash"], expected["content_hash"])

    def test_job_past_lease_does_not_refresh_previous_success(self):
        initial = self.memory.kontrol_et("pilot")
        self.now[0] += 10
        def late(source, **kwargs):
            self.now[0] += LEASE_SECONDS + 1
            return response(status=304)
        self.memory.fetcher = late
        result = self.memory.kontrol_et("pilot", max_age_seconds=0)
        self.assertTrue(result["ownership_lost"])
        self.assertFalse(result["fresh"])
        self.assertEqual(result["checked_at"], initial["checked_at"])
        self.assertEqual(result["state"], "error")

    def test_unexpected_304_and_changed_validator_fail_closed(self):
        self.fetch.return_value = response(status=304)
        self.assertEqual(self.memory.kontrol_et("pilot")["state"], "error")
        self.now[0] += 61
        self.fetch.return_value = response(headers={"etag": '"one"'})
        good = self.memory.kontrol_et("pilot")
        self.now[0] += 4000
        self.fetch.return_value = response(status=304, headers={"etag": '"different"'})
        failed = self.memory.kontrol_et("pilot")
        self.assertEqual(failed["checked_at"], good["checked_at"])
        self.assertFalse(failed["fresh"])

    def test_private_variable_redirect_and_partial_representations_are_not_shared(self):
        bad = [response(headers={"cache-control": "private"}), response(headers={"cache-control": "no-store"}),
               response(headers={"set-cookie": "session=synthetic"}), response(headers={"vary": "Cookie"}),
               response(headers={"vary": "*"}), response(headers={"content-language": "tr"}),
               response(url=URL + "different"), response(status=302), response(status=403),
               response(text=TEXT * 150), response(headers={"etag": '"one"\r\nX-Injected: yes'})]
        for candidate in bad:
            with self.subTest(headers=candidate.headers, status=candidate.status):
                self.fetch.return_value = candidate
                self.assertFalse(self.memory.kontrol_et("pilot")["fresh"])
                self.assertEqual(self.memory.oku("pilot")["source_revision"], 0)
                self.now[0] += 61

    def test_cache_control_age_and_no_cache_require_revalidation(self):
        self.fetch.return_value = response(headers={"cache-control": "max-age=10", "age": "9"})
        self.assertTrue(self.memory.kontrol_et("pilot")["fresh"])
        self.now[0] += 2
        self.assertTrue(self.memory.kontrol_et("pilot")["check_started"])
        self.fetch.return_value = response(headers={"cache-control": "no-cache", "etag": '"a"'})
        self.now[0] += 20
        self.assertFalse(self.memory.kontrol_et("pilot")["fresh"])
        self.assertTrue(self.memory.kontrol_et("pilot")["check_started"])

    def test_shared_cache_age_overrides_regular_age_and_subtracts_age_header(self):
        self.fetch.return_value = response(headers={"cache-control": "max-age=3600, s-maxage=0"})
        self.assertFalse(self.memory.kontrol_et("pilot")["fresh"])
        self.assertTrue(self.memory.kontrol_et("pilot")["check_started"])
        self.fetch.return_value = response(headers={"cache-control": "max-age=0, s-maxage=10", "age": "9"})
        self.assertTrue(self.memory.kontrol_et("pilot")["fresh"])
        self.now[0] += 2
        self.assertTrue(self.memory.kontrol_et("pilot")["check_started"])

    def test_language_and_version_queries_use_distinct_keys(self):
        tr = KaynakTanimi("turkish", URL, language="tr")
        version1 = KaynakTanimi("v1", URL + "?version=1")
        version2 = KaynakTanimi("v2", URL + "?version=2")
        self.assertEqual(len({self.source.key, tr.key, version1.key, version2.key}), 4)
        memory = KaynakHafizasi(self.path, [self.source, tr, version1, version2],
                               fetcher=lambda source, **kwargs: response(url=source.url, headers={"content-language": source.language}),
                               clock=lambda: self.now[0])
        memory.kontrol_et("pilot")
        self.assertEqual(memory.oku("turkish")["state"], "missing")
        self.assertEqual(memory.oku("v1")["state"], "missing")
        self.assertTrue(memory.kontrol_et("v1")["fresh"])
        self.assertEqual(memory.oku("v2")["state"], "missing")
        with self.assertRaises(ValueError):
            KaynakTanimi("unknown", URL, extractor_version=EXTRACTOR_VERSION + "-other")

    def test_private_and_unconfigured_urls_rejected_before_network(self):
        for url in ("http://blog.youtube/article", "https://127.0.0.1/article", "https://test.local/article",
                    "https://name:pass@blog.youtube/article", URL + "?token=secret", URL + "?q=private",
                    URL + "#private", URL + "?version=1&version=2", URL + "?version=one%0d%0a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                KaynakTanimi("bad", url)
        with self.assertRaises(ValueError):
            self.memory.kontrol_et("unconfigured")
        self.fetch.assert_not_called()

    def test_only_approved_public_summary_and_no_raw_body_or_identity_storage(self):
        current = self.memory.kontrol_et("pilot")
        kwargs = {"expected_revision": current["source_revision"], "expected_hash": current["content_hash"], "summary": summary()}
        with self.assertRaises(ValueError):
            self.memory.ozet_kaydet("pilot", **kwargs)
        for bad in (summary(summary="Contact private.person@example.com"), summary(conversation="private context"),
                    summary(model="sk-" + "".join(("synthetic", "Private", "Value", "9999"))), summary(generated_at="2026-11-05T20:00:00")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.memory.ozet_kaydet("pilot", **{**kwargs, "summary": bad}, approved_public=True)
        self.publish()
        with closing(sqlite3.connect(self.path)) as connection:
            dump = "\n".join(connection.iterdump())
        self.assertNotIn(TEXT, dump)
        self.assertNotIn("private.person", dump)
        self.assertNotIn("conversation", dump)
        self.assertNotIn("etag", json.dumps(self.memory.oku("pilot")))

    def test_daily_budget_persists_across_instances(self):
        limited = KaynakHafizasi(self.path, [self.source], fetcher=self.fetch, clock=lambda: self.now[0], daily_limit=1)
        limited.kontrol_et("pilot")
        self.now[0] += 4000
        restarted = KaynakHafizasi(self.path, [self.source], fetcher=self.fetch, clock=lambda: self.now[0], daily_limit=1)
        result = restarted.kontrol_et("pilot")
        self.assertTrue(result["budget_exhausted"])
        self.assertFalse(result["check_started"])
        self.assertEqual(self.fetch.call_count, 1)


class PostgreSQLDocBookTestleri(unittest.TestCase):
    url = "https://www.postgresql.org/docs/18/textsearch-indexes.html"

    def docbook(self, *, text=TEXT, title="12.9. Preferred index types for text search", extra="", chrome="Navigation text"):
        section = ('<div class="sect1" id="TEXTSEARCH-INDEXES"><div class="titlepage"><div><div>'
                   '<h2 class="title">' + title + '</h2></div></div></div><p>' + text + '</p>' + extra + '</div>')
        return '<div id="docContent"><div class="navheader">' + chrome + '</div>' + section + '<div class="navfooter">Footer navigation</div></div>'

    def candidate(self, html, *, url=None):
        return toplayici.FetchResult(200, html.encode("utf-8"), {"content-type": "text/html"}, url or self.url)

    def prepared(self, html):
        return KaynakHafizasi.yaniti_hazirla(KaynakTanimi("postgresql18", self.url), None, self.candidate(html))

    def test_single_docbook_section_is_complete_without_navigation(self):
        result = kaynak_metnini_ayristir(self.candidate(self.docbook()))
        self.assertEqual(result["body"], TEXT)
        self.assertEqual(result["title"], "12.9. Preferred index types for text search")
        self.assertNotIn("Navigation", result["body"])
        self.assertEqual(self.prepared(self.docbook())["content_hash"],
                         self.prepared(self.docbook(chrome="Entirely different navigation"))["content_hash"])
        self.assertEqual(EXTRACTOR_VERSION, "article-v4-docbook-definitions")

    def test_subsection_heading_and_text_are_preserved_and_hashed(self):
        extra = ('<div class="sect2"><div class="titlepage"><h3 class="title">Index update costs</h3></div>'
                 '<p>Index maintenance depends on the supported operator class.</p>'
                 '<h4>Additional scope</h4><p>Only this documented version is covered.</p></div>')
        result = kaynak_metnini_ayristir(self.candidate(self.docbook(extra=extra)))
        self.assertIn("Index update costs", result["body"])
        self.assertIn("Index maintenance", result["body"])
        self.assertIn("Only this documented version", result["body"])
        changed = extra.replace("supported operator class", "documented release")
        self.assertNotEqual(self.prepared(self.docbook(extra=extra))["content_hash"],
                            self.prepared(self.docbook(extra=changed))["content_hash"])

    def test_code_literal_spacing_changes_hash_even_when_readable_body_is_equal(self):
        for template in ("<pre>SELECT 'a{space}b';</pre>", "<p>Example <code>'a{space}b'</code> literal.</p>"):
            with self.subTest(template=template):
                first = self.docbook(extra=template.format(space="  "))
                second = self.docbook(extra=template.format(space=" "))
                self.assertEqual(kaynak_metnini_ayristir(self.candidate(first))["body"],
                                 kaynak_metnini_ayristir(self.candidate(second))["body"])
                self.assertNotEqual(self.prepared(first)["content_hash"], self.prepared(second)["content_hash"])

    def test_definition_pairs_preserve_order_and_each_full_description(self):
        extra = ('<div class="variablelist"><dl><dt><span><code>GIN</code></span></dt>'
                 '<dd><p>First description with <em>recheck</em> and <code>query</code>.</p>'
                 '<p>Every additional paragraph remains included.</p></dd><dt><span>GiST</span></dt>'
                 '<dd>Second description with <a href="https://www.postgresql.org/docs/18/index.html">a reference</a>.</dd>'
                 '</dl></div>')
        article = kaynak_metnini_ayristir(self.candidate(self.docbook(extra=extra)))
        self.assertIn("First description with recheck and query", article["body"])
        self.assertIn("Every additional paragraph remains included.", article["body"])
        self.assertIn("Second description with a reference", article["body"])
        self.assertLess(article["body"].index("GIN"), article["body"].index("GiST"))
        changed = extra.replace("<code>query</code>", "<code>query  argument</code>")
        self.assertNotEqual(self.prepared(self.docbook(extra=extra))["content_hash"],
                            self.prepared(self.docbook(extra=changed))["content_hash"])

    def test_definition_term_or_description_change_invalidates_summary(self):
        original = '<dl><dt>Index operator</dt><dd><p>Search returns matching entries.</p></dd></dl>'
        with tempfile.TemporaryDirectory() as directory:
            now = [100000.0]
            fetch = Mock()
            memory = KaynakHafizasi(Path(directory) / "definitions.sqlite3", [KaynakTanimi("postgresql18", self.url)],
                                    fetcher=fetch, clock=lambda: now[0])
            for changed in (original.replace("Index operator", "Changed operator"),
                            original.replace("matching entries", "verified results")):
                with self.subTest(changed=changed):
                    now[0] += 4000
                    fetch.return_value = self.candidate(self.docbook(extra=original))
                    first = memory.kontrol_et("postgresql18")
                    memory.ozet_kaydet("postgresql18", expected_revision=first["source_revision"],
                                      expected_hash=first["content_hash"], summary=summary(), approved_public=True)
                    now[0] += 4000
                    fetch.return_value = self.candidate(self.docbook(extra=changed))
                    second = memory.kontrol_et("postgresql18")
                    self.assertTrue(second["check_succeeded"])
                    self.assertEqual(second["source_revision"], first["source_revision"] + 1)
                    self.assertNotEqual(second["content_hash"], first["content_hash"])
                    self.assertEqual(second["summary_status"], "stale")
                    self.assertFalse(second["summary_reusable"])

    def test_malformed_empty_nested_or_unsupported_definition_lists_are_rejected(self):
        invalid = ('<dl></dl>', '<dl><dt>Only term</dt></dl>', '<dl><dd>Orphan explanation</dd></dl>',
                   '<dl><dd>Wrong order</dd><dt>Term</dt></dl>', '<dl><dt>A</dt><dt>B</dt><dd>C</dd></dl>',
                   '<dl><dt> </dt><dd>Description</dd></dl>', '<dl><dt>Term</dt><dd> </dd></dl>',
                   '<dl>Unpaired text<dt>Term</dt><dd>Description</dd></dl>',
                   '<dl><div><dt>Wrapped term</dt><dd>Description</dd></div></dl>',
                   '<dl><dt>Term</dt><dd><dl><dt>Nested term</dt><dd>Nested description</dd></dl></dd></dl>',
                   '<dl><dt><p>Block term</p></dt><dd>Description</dd></dl>',
                   '<dl><dt>Term</dt><dd><p><p>Nested paragraph</p></p></dd></dl>',
                   '<dl><dt>Term</dt><dd><ul><li>Unsupported list</li></ul></dd></dl>',
                   '<dl><dt>Term</dt><dd><script>unsupported()</script>Description</dd></dl>')
        for extra in invalid:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                kaynak_metnini_ayristir(self.candidate(self.docbook(extra=extra)))

    def test_title_and_body_changes_each_change_source_hash(self):
        original = self.prepared(self.docbook())["content_hash"]
        title = self.prepared(self.docbook(title="12.9. Updated index types for text search"))["content_hash"]
        body = self.prepared(self.docbook(text=TEXT + " A changed supported index is documented."))["content_hash"]
        self.assertEqual(len({original, title, body}), 3)

    def test_ambiguous_containers_main_sections_and_listing_are_rejected(self):
        good = self.docbook()
        examples = [good + good, good.replace('<div class="navfooter">', '<div class="sect1"><p>Other section</p></div><div class="navfooter">'),
                    good.replace('<div class="sect1"', '<div><div class="sect1"').replace('<div class="navfooter">', '</div><div class="navfooter">'),
                    self.docbook(extra='<h2 class="title">Another main heading</h2>'),
                    '<div id="docContent"><div class="toc"><p>Documentation index links</p></div></div>']
        for html in examples:
            with self.subTest(html=html[:80]), self.assertRaises(ValueError):
                kaynak_metnini_ayristir(self.candidate(html))

    def test_docbook_support_does_not_expand_to_other_hosts_or_paths(self):
        for url in ("https://evil.example.org/docs/18/textsearch-indexes.html",
                    "https://www.postgresql.org/about/textsearch-indexes.html",
                    "https://www.postgresql.org/docs/current/textsearch-indexes.html",
                    "https://www.postgresql.org/docs/18/nested/textsearch-indexes.html",
                    "https://www.postgresql.org/docs/18/index.xml"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                kaynak_metnini_ayristir(self.candidate(self.docbook(), url=url))

    def test_normalization_cannot_turn_non_html_or_failed_response_into_html_success(self):
        body = self.docbook().encode("utf-8")
        for status, kind, data in ((403, "text/html", body), (200, "application/json", body),
                                   (200, "application/pdf", body), (200, "text/html", b"%PDF-" + body)):
            with self.subTest(status=status, kind=kind), self.assertRaises(ValueError):
                kaynak_metnini_ayristir(toplayici.FetchResult(status, data, {"content-type": kind}, self.url))

    def test_uncovered_table_heading_and_plain_text_are_rejected_instead_of_ignored(self):
        extras = ['<table><tr><th>Value</th><td>100</td></tr></table>', '<h5>Important changed scope</h5>',
                  '<h6>Important changed scope</h6>', '<div>Visible unsupported evidence</div>',
                  '<dl><dt>Operator</dt><dd><table><tr><td>Important constraint</td></tr></table></dd></dl>']
        for extra in extras:
            for changed in (extra, extra.replace("100", "200").replace("scope", "limitation")):
                with self.subTest(extra=changed), self.assertRaises(ValueError):
                    kaynak_metnini_ayristir(self.candidate(self.docbook(extra=changed)))


class TekrarliHTTPBasligiTestleri(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = KaynakHafizasi(Path(self.temp.name) / "headers.sqlite3", [KaynakTanimi("pilot", URL)])

    def connection(self, headers):
        body = response().body
        raw = ("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: " + str(len(body)) + "\r\n"
               + "".join(name + ": " + value + "\r\n" for name, value in headers) + "\r\n").encode("latin-1") + body
        response_socket = MagicMock()
        response_socket.makefile.return_value = io.BytesIO(raw)
        parsed = http.client.HTTPResponse(response_socket)
        parsed.begin()
        connection = MagicMock()
        connection.getresponse.return_value = parsed
        return connection

    def check(self, headers):
        connection = self.connection(headers)
        with patch.object(toplayici, "_robots_allowed", return_value=True), \
             patch.object(toplayici, "_resolve_public_host", return_value=(2, ("8.8.8.8", 443))), \
             patch.object(toplayici, "_PinnedHTTPSConnection", return_value=connection):
            result = self.memory.kontrol_et("pilot")
        connection.close.assert_called_once()
        return result

    def test_repeated_cache_control_cannot_hide_private_or_no_store(self):
        result = self.check([("Cache-Control", "private, no-store"), ("cache-control", "max-age=3600")])
        self.assertEqual(result["state"], "error")
        self.assertFalse(result["fresh"])
        self.assertIsNone(result["content_hash"])
        self.assertIsNone(result["checked_at"])

    def test_repeated_vary_cannot_hide_cookie_representation(self):
        result = self.check([("Vary", "Cookie"), ("vary", "Accept-Encoding")])
        self.assertEqual(result["state"], "error")
        self.assertFalse(result["fresh"])
        self.assertIsNone(result["content_hash"])

    def test_compatible_repeated_lists_remain_usable(self):
        result = self.check([("Cache-Control", "public"), ("Cache-Control", "max-age=3600"),
                             ("Vary", "Accept-Language"), ("Vary", "Accept-Encoding"),
                             ("ETag", '"same"'), ("ETag", '"same"')])
        self.assertTrue(result["fresh"])
        self.assertTrue(result["check_succeeded"])

    def test_conflicting_single_value_validators_fail_only_conditional_read(self):
        for name, first, second in (("ETag", '"one"', '"two"'),
                                    ("Last-Modified", "Wed, 05 Nov 2025 10:00:00 GMT", "Wed, 05 Nov 2025 11:00:00 GMT")):
            with self.subTest(name=name):
                connection = self.connection([(name, first), (name, second)])
                with patch.object(toplayici, "_robots_allowed", return_value=True), \
                     patch.object(toplayici, "_resolve_public_host", return_value=(2, ("8.8.8.8", 443))), \
                     patch.object(toplayici, "_PinnedHTTPSConnection", return_value=connection), \
                     self.assertRaises(toplayici.CollectionError):
                    toplayici.kosullu_sayfa_oku(URL, {"item_hosts": ["blog.youtube"]})
                connection.close.assert_called_once()


class KosulluAgSiniriTestleri(unittest.TestCase):
    def setUp(self):
        self.source = {"item_hosts": ["blog.youtube"]}

    def test_only_bounded_public_headers_and_same_host_200_304(self):
        with patch.object(toplayici, "_robots_allowed", return_value=True), \
             patch.object(toplayici, "_request_once", return_value=response(status=304)) as request:
            result = toplayici.kosullu_sayfa_oku(URL, self.source, etag='"one"', language="tr")
        self.assertEqual(result.status, 304)
        self.assertEqual(request.call_args.args[1], {"blog.youtube"})
        self.assertEqual(request.call_args.args[2], {"Accept-Language": "tr", "If-None-Match": '"one"'})

    def test_redirect_is_not_followed_and_invalid_validator_never_reaches_network(self):
        with patch.object(toplayici, "_robots_allowed", return_value=True), \
             patch.object(toplayici, "_request_once", return_value=response(status=302)) as request:
            with self.assertRaises(toplayici.CollectionError):
                toplayici.kosullu_sayfa_oku(URL, self.source)
            self.assertEqual(request.call_count, 1)
        for kwargs in ({"etag": "x\r\nCookie: private"}, {"modified": "x\x7f"}, {"language": "tr\r\n"}, {"etag": "x" * 513}):
            with self.subTest(kwargs=kwargs), patch.object(toplayici, "_request_once") as request, \
                 self.assertRaises(toplayici.CollectionError):
                toplayici.kosullu_sayfa_oku(URL, self.source, **kwargs)
            request.assert_not_called()

    def test_private_query_is_blocked_but_version_is_kept_exactly(self):
        with patch.object(toplayici, "_robots_allowed") as robots, self.assertRaises(toplayici.CollectionError):
            toplayici.kosullu_sayfa_oku(URL + "?token=private", self.source)
        robots.assert_not_called()
        versioned = URL + "?version=2&lang=en"
        with patch.object(toplayici, "_robots_allowed", return_value=True), \
             patch.object(toplayici, "_request_once", return_value=response(url=versioned)) as request:
            toplayici.kosullu_sayfa_oku(versioned, self.source)
        self.assertEqual(request.call_args.args[0], versioned)

    def test_robots_allowlist_and_public_dns_protection_remain_active(self):
        with patch.object(toplayici, "_robots_allowed", return_value=False), \
             patch.object(toplayici, "_request_once") as request, self.assertRaises(toplayici.CollectionError):
            toplayici.kosullu_sayfa_oku(URL, self.source)
        request.assert_not_called()
        with patch.object(toplayici, "_robots_allowed") as robots, self.assertRaises(toplayici.CollectionError):
            toplayici.kosullu_sayfa_oku("https://unknown.example/article", {"item_hosts": ["unknown.example"]})
        robots.assert_not_called()
        with patch.object(toplayici, "_robots_allowed", return_value=True), \
             patch.object(toplayici.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]), \
             patch.object(toplayici, "_PinnedHTTPSConnection") as connection, self.assertRaises(toplayici.CollectionError):
            toplayici.kosullu_sayfa_oku(URL, self.source)
        connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
