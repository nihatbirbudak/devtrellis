"""Kalıcı rapor yollarını, güvenli önbelleği ve vekil adresi sınırını sentetik hizmette sınar."""

import os
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from radar.topluluk_api import IstekSiniri, uygulama
from radar.topluluk_depo import ToplulukHatasi


ORIGIN = "https://community.example.org"
IDENTIFIER = "a" * 32
CARD = {"id": IDENTIFIER, "version": 4, "published_version": 4, "status": "published",
        "updated_at": "2026-11-06T09:00:00+00:00",
        "packet": {"kind": "research", "title": "Test research", "summary": "A short description",
                   "body": "# Section\n- [source](https://example.org)\n<script>alert(1)</script>",
                   "author": "Tester", "license": "CC-BY-4.0",
                   "sources": [{"title": "Primary", "url": "https://example.org"}]}}


class PublicRoutes(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.app = uygulama(self.root / "community.sqlite3", public_url=ORIGIN,
                            feed_db_path=self.root / "feed.sqlite3", sources=[])
        self.client = TestClient(self.app, base_url=ORIGIN)
        self.addCleanup(self.client.close)

    def test_report_and_machine_outputs_are_read_only(self):
        with patch.object(self.app.state.community, "yayini_oku", return_value=CARD), \
             patch.object(self.app.state.community, "listele", return_value={"items": [CARD], "total": 1}):
            report = self.client.get(f"/r/{IDENTIFIER}/v/4?lang=tr")
            self.assertEqual(report.status_code, 200)
            self.assertIn('<html lang="tr">', report.text)
            self.assertIn('&lt;script&gt;', report.text)
            self.assertIn('TechArticle', report.text)
            self.assertNotIn("set-cookie", report.headers)
            self.assertEqual(report.headers["cache-control"], "public, max-age=0, must-revalidate")
            self.assertEqual(self.client.get(f"/r/{IDENTIFIER}/test-research").status_code, 200)
            self.assertIn("Version: 4", self.client.get(f"/r/{IDENTIFIER}.md").text)
            for path, expected in (("/sitemap.xml", "test-research"), ("/feed.atom", "/v/4"), ("/llms.txt", "/v/4")):
                self.assertIn(expected, self.client.get(path).text)
            self.assertNotIn("set-cookie", self.client.get("/").headers)
            self.assertEqual(self.client.get("/api/community/session").json()["csrf_token"], None)
            self.assertEqual(self.app.state.community.oturum_oku("nonexistent"), None)

    def test_report_guide_has_saved_language_hook_and_scriptless_choices(self):
        import re
        for language, title in (("tr", "Rapor nasıl okunur"), ("en", "How to read a report")):
            page = self.client.get("/report-guide?lang=" + language)
            self.assertEqual(page.status_code, 200)
            self.assertIn('<html lang="' + language + '">', page.text)
            self.assertIn("<h1>" + title + "</h1>", page.text)
            self.assertNotIn("set-cookie", page.headers)
            script = re.search(r'/assets/[a-f0-9]{16}/report-language.js', page.text)
            self.assertIsNotNone(script)
            self.assertEqual(self.client.get(script.group()).status_code, 200)
            for choice in ("tr", "en"):
                self.assertIn('href="/report-guide?lang=' + choice + '"', page.text)
                self.assertIn('data-report-language="' + choice + '"', page.text)
        self.assertEqual(self.client.get("/guide").status_code, 404)

    def test_markdown_query_reads_the_selected_version_after_new_publication(self):
        old = {**CARD, "version": 3, "packet": {**CARD["packet"], "body": "EARLIER_VERSION_BODY"}}
        new = {**CARD, "packet": {**CARD["packet"], "body": "LATEST_VERSION_BODY"}}
        def read(entry_id, observer=None, version=None):
            return old if version == 3 else new
        with patch.object(self.app.state.community, "yayini_oku", side_effect=read):
            page = self.client.get(f"/r/{IDENTIFIER}/v/3")
            self.assertIn(f"/r/{IDENTIFIER}.md?version=3", page.text)
            previous = self.client.get(f"/r/{IDENTIFIER}.md?version=3")
            latest = self.client.get(f"/r/{IDENTIFIER}.md")
            self.assertIn("EARLIER_VERSION_BODY", previous.text)
            self.assertNotIn("LATEST_VERSION_BODY", previous.text)
            self.assertIn("LATEST_VERSION_BODY", latest.text)

    def test_hidden_report_is_unavailable_on_every_version(self):
        with patch.object(self.app.state.community, "yayini_oku", side_effect=ToplulukHatasi("hidden", 404)):
            for path in (f"/r/{IDENTIFIER}/v/4", f"/r/{IDENTIFIER}/test-research", f"/r/{IDENTIFIER}.md"):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_hashed_asset_and_fake_forwarding(self):
        page = self.client.get("/")
        import re
        path = re.search(r'/assets/[a-f0-9]{16}/community.js', page.text).group()
        self.assertEqual(self.client.get(path).headers["cache-control"], "public, max-age=31536000, immutable")
        forged = self.client.get("/", headers={"X-Forwarded-For": "8.8.8.8"})
        self.assertEqual(forged.status_code, 200)
        self.assertEqual(forged.headers["cache-control"], "no-store")
        self.assertEqual(self.client.get("/", headers={"X-DevTrellis-Client-IP": "8.8.8.8"}).status_code, 200)

    def test_rate_counter_survives_new_instance_without_plain_address(self):
        path = self.root / "private-rate" / "limits.sqlite3"
        clock = lambda: 1793900000
        first = IstekSiniri(clock, db_path=path, key=b"k" * 32)
        self.assertTrue(first.izin("203.0.113.4", "write", 1))
        second = IstekSiniri(clock, db_path=path, key=b"k" * 32)
        self.assertFalse(second.izin("203.0.113.4", "write", 1))
        self.assertNotIn(b"203.0.113.4", path.read_bytes())

    def test_production_proxy_requires_loopback_token_and_exact_https_header(self):
        app = uygulama(self.root / "proxy-community.sqlite3", public_url=ORIGIN,
                       feed_db_path=self.root / "proxy-feed.sqlite3", sources=[],
                       trusted_proxy_token="p" * 32, rate_db_path=self.root / "rate" / "limits.sqlite3",
                       rate_key=b"k" * 32)
        headers = {"X-DevTrellis-Proxy-Token": "p" * 32,
                   "X-DevTrellis-Client-IP": "203.0.113.4", "X-Forwarded-Proto": "https"}
        with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 12345)) as proxy:
            self.assertEqual(proxy.get("/", headers=headers).status_code, 200)
            self.assertEqual(proxy.get("/", headers={**headers, "X-Forwarded-For": "8.8.8.8"}).status_code, 200)
            self.assertEqual(proxy.get("/", headers={**headers, "X-DevTrellis-Client-IP": "1.1.1.1, 8.8.8.8"}).status_code, 403)
            self.assertEqual(proxy.get("/", headers={**headers, "X-DevTrellis-Proxy-Token": "wrong"}).status_code, 403)

    def test_public_cache_varies_by_language_and_never_stores_cookie_response(self):
        def view(entry_id, observer=None, version=None):
            return {**CARD, **({"moderation_version": 7} if observer else {})}
        with patch.object(self.app.state.community, "yayini_oku", side_effect=view):
            path = f"/r/{IDENTIFIER}/v/4"
            english = self.client.get(path, headers={"Accept-Language": "en"})
            turkish = self.client.get(path, headers={"Accept-Language": "tr"})
            self.assertIn('<html lang="en">', english.text)
            self.assertIn('<html lang="tr">', turkish.text)
            self.assertIn("accept-language", english.headers["vary"].lower())
            public = self.client.get(f"/api/community/cards/{IDENTIFIER}")
            self.assertNotIn("moderation_version", public.json())
            self.assertEqual(public.headers["cache-control"], "public, max-age=0, must-revalidate")
            with patch.object(self.app.state.community, "oturum_oku", return_value={"user": {"role": "moderator"}}):
                private = self.client.get(f"/api/community/cards/{IDENTIFIER}", headers={"Cookie": "devtrellis_session=test"})
            self.assertEqual(private.json()["moderation_version"], 7)
            self.assertEqual(private.headers["cache-control"], "no-store")
            self.assertIn("cookie", private.headers["vary"].lower())
            invalid_cookie = self.client.get(path, headers={"Cookie": "unrelated=1"})
            self.assertEqual(invalid_cookie.headers["cache-control"], "no-store")
            self.assertEqual(self.client.get(path, headers={"Authorization": "Bearer synthetic"}).headers["cache-control"], "no-store")

    def test_session_creation_is_explicit_same_origin_and_get_remains_read_only(self):
        store = self.app.state.community
        with patch.object(store, "oturum_ac", wraps=store.oturum_ac) as create:
            for _ in range(3):
                response = self.client.get("/api/community/session")
                self.assertIsNone(response.json()["csrf_token"])
                self.assertNotIn("set-cookie", response.headers)
            for headers in ({}, {"Origin": "https://foreign.example.org"}):
                self.assertEqual(self.client.post("/api/community/session", json={}, headers=headers).status_code, 403)
            create.assert_not_called()
            response = self.client.post("/api/community/session", json={}, headers={"Origin": ORIGIN})
            self.assertEqual(response.status_code, 200)
            self.assertIsNotNone(response.json()["csrf_token"])
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(create.call_count, 1)
            self.assertEqual(self.client.get("/api/community/session").json(), response.json())
            self.assertEqual(create.call_count, 1)

    def test_rate_rejects_shared_directory_and_foreign_database_without_changes(self):
        for shared in (True, False):
            directory = self.root / ("shared" if shared else "foreign")
            directory.mkdir()
            path = directory / "limits.sqlite3"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("CREATE TABLE foreign_data (value TEXT)")
                connection.execute("INSERT INTO foreign_data VALUES ('keep me')")
            if shared:
                (directory / "unrelated.txt").write_text("leave untouched")
            before = path.read_bytes()
            with patch("radar.topluluk_api.ozel_dosya_izni") as permissions:
                with self.assertRaises(ValueError):
                    IstekSiniri(db_path=path, key=b"k" * 32)
                permissions.assert_not_called()
            self.assertEqual(path.read_bytes(), before)

    def test_rate_rejects_hardlink_and_parent_link(self):
        source = self.root / "source.sqlite3"
        source.write_bytes(b"leave untouched")
        directory = self.root / "hardlinked"
        directory.mkdir()
        target = directory / "limits.sqlite3"
        try:
            os.link(source, target)
        except OSError:
            self.skipTest("Hardlinks unavailable")
        with self.assertRaises(ValueError):
            IstekSiniri(db_path=target, key=b"k" * 32)
        self.assertEqual(source.read_bytes(), b"leave untouched")
        linked = self.root / "linked"
        try:
            linked.symlink_to(directory, target_is_directory=True)
        except OSError:
            return  # Windows symlink privilege is optional; hardlink boundary was exercised.
        with self.assertRaises(ValueError):
            IstekSiniri(db_path=linked / "nested" / "limits.sqlite3", key=b"k" * 32)
        self.assertFalse((directory / "nested").exists())

    def test_rate_expiry_removes_old_digest_without_resetting_live_budget(self):
        now = [180]
        path = self.root / "expiring-rate" / "limits.sqlite3"
        limiter = IstekSiniri(lambda: now[0], db_path=path, key=b"k" * 32)
        self.assertTrue(limiter.izin("203.0.113.4", "write", 1))
        self.assertFalse(limiter.izin("203.0.113.4", "write", 1))
        now[0] = 240
        self.assertTrue(limiter.izin("203.0.113.5", "write", 1))
        with closing(sqlite3.connect(path)) as connection, connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM request_limits").fetchone()[0], 1)
        self.assertTrue(limiter.izin("203.0.113.4", "write", 1))

    def test_etag_head_and_withdrawn_versions_revalidate_live_state(self):
        with patch.object(self.app.state.community, "yayini_oku", return_value=CARD):
            for path in (f"/r/{IDENTIFIER}/v/4", f"/r/{IDENTIFIER}.md", f"/api/community/cards/{IDENTIFIER}"):
                first = self.client.get(path, headers={"Accept-Language": "en"})
                self.assertEqual(first.status_code, 200)
                tag = first.headers["etag"]
                head = self.client.head(path, headers={"Accept-Language": "en"})
                self.assertEqual(head.status_code, 200)
                self.assertEqual(head.content, b"")
                self.assertEqual(head.headers["etag"], tag)
                self.assertEqual(head.headers["content-length"], first.headers["content-length"])
                unchanged = self.client.get(path, headers={"If-None-Match": tag, "Accept-Language": "en"})
                self.assertEqual(unchanged.status_code, 304)
                self.assertEqual(unchanged.content, b"")
                self.assertNotIn("set-cookie", unchanged.headers)
                self.assertIn("accept-language", unchanged.headers["vary"].lower())
                self.assertEqual(self.client.head(path, headers={"If-None-Match": tag, "Accept-Language": "en"}).status_code, 304)
                private = self.client.get(path, headers={"Cookie": "unrelated=1", "If-None-Match": tag})
                self.assertEqual(private.status_code, 200)
                self.assertNotIn("etag", private.headers)
            path = f"/r/{IDENTIFIER}/v/4"
            english = self.client.get(path, headers={"Accept-Language": "en"})
            turkish = self.client.get(path, headers={"Accept-Language": "tr", "If-None-Match": english.headers["etag"]})
            self.assertEqual(turkish.status_code, 200)
            self.assertNotEqual(turkish.headers["etag"], english.headers["etag"])
        changed = {**CARD, "packet": {**CARD["packet"], "body": "New content at the same public path"}}
        with patch.object(self.app.state.community, "yayini_oku", return_value=changed):
            self.assertEqual(self.client.get(path, headers={"If-None-Match": english.headers["etag"]}).status_code, 200)
        with patch.object(self.app.state.community, "yayini_oku", side_effect=ToplulukHatasi("hidden", 404)):
            hidden = self.client.get(path, headers={"If-None-Match": english.headers["etag"]})
            self.assertEqual(hidden.status_code, 404)
            self.assertEqual(hidden.headers["cache-control"], "no-store")

    def test_forwarding_without_token_cannot_change_peer_or_create_https(self):
        forged = {"X-Forwarded-For": "8.8.8.8", "CF-Connecting-IP": "8.8.4.4",
                  "X-DevTrellis-Client-IP": "1.1.1.1", "X-Forwarded-Proto": "https"}
        with patch.object(IstekSiniri, "izin", autospec=True, return_value=True) as limit:
            self.assertEqual(self.client.get("/api/health", headers=forged).status_code, 200)
            self.assertEqual(limit.call_args.args[1], "testclient")
        with TestClient(self.app, base_url="http://community.example.org", client=("127.0.0.1", 1234)) as plain:
            self.assertEqual(plain.get("/api/health", headers=forged).status_code, 400)

    def test_csrf_neutral_alias_and_legacy_share_validation(self):
        for name in ("X-DevTrellis-CSRF", "X-Ufuk-CSRF"):
            initial = self.client.post("/api/community/session", json={}, headers={"Origin": ORIGIN}).json()
            token = initial["csrf_token"]
            invalid = self.client.post("/api/community/logout", json={}, headers={"Origin": ORIGIN,
                "X-DevTrellis-CSRF": token, "X-Ufuk-CSRF": "contradictory"})
            self.assertEqual(invalid.status_code, 403)
            duplicate = self.client.post("/api/community/logout", json={}, headers=[("Origin", ORIGIN), (name, token), (name, token)])
            self.assertEqual(duplicate.status_code, 403)
            valid = self.client.post("/api/community/logout", json={}, headers={"Origin": ORIGIN, name: token})
            self.assertEqual(valid.status_code, 200)

    def test_objection_form_and_queue_only_when_separate_store_enabled(self):
        path = f"/objections/new/{IDENTIFIER}/4?lang=tr"
        self.assertEqual(self.client.get(path).status_code, 404)
        app = uygulama(self.root / "second.sqlite3", public_url=ORIGIN,
                        feed_db_path=self.root / "second-feed.sqlite3", sources=[],
                        objections_db_path=self.root / "private-objections" / "objections.sqlite3")
        with patch.object(app.state.community, "yayini_oku", return_value=CARD), TestClient(app, base_url=ORIGIN) as client:
            form = client.get(path)
            self.assertEqual(form.status_code, 200)
            self.assertIn('name="claim"', form.text)
            self.assertIn('name="reason"', form.text)
            self.assertIn('name="consent"', form.text)
            self.assertNotIn("set-cookie", form.headers)
            self.assertEqual(client.get("/objections/review").status_code, 200)

    def test_confirmation_dialog_assets_load_before_page_scripts(self):
        # Gömülü tarayıcı window.confirm'i bastırabildiği için onay sayfa içi bileşenle sorulur; her sayfa onu yüklemeli.
        import re
        app = uygulama(self.root / "third.sqlite3", public_url=ORIGIN,
                        feed_db_path=self.root / "third-feed.sqlite3", sources=[],
                        objections_db_path=self.root / "private-objections-3" / "objections.sqlite3")
        with patch.object(app.state.community, "yayini_oku", return_value=CARD), TestClient(app, base_url=ORIGIN) as client:
            for path, script in (("/", "community.js"), (f"/objections/new/{IDENTIFIER}/4?lang=tr", "objection-form.js"),
                                 ("/objections/review", "objection-review.js")):
                with self.subTest(path=path):
                    page = client.get(path)
                    self.assertEqual(page.status_code, 200)
                    dialog = re.search(r'/assets/[a-f0-9]{16}/confirm-dialog\.js', page.text)
                    styles = re.search(r'/assets/[a-f0-9]{16}/confirm-dialog\.css', page.text)
                    target = re.search(r'/assets/[a-f0-9]{16}/' + re.escape(script), page.text)
                    self.assertIsNotNone(dialog)
                    self.assertIsNotNone(styles)
                    self.assertLess(dialog.start(), target.start())
                    self.assertNotIn("<script>", page.text)
                    for asset, media in ((dialog.group(), "text/javascript"), (styles.group(), "text/css")):
                        response = client.get(asset)
                        self.assertEqual(response.status_code, 200)
                        self.assertTrue(response.headers["content-type"].startswith(media))
                        self.assertIn("script-src 'self'", response.headers["content-security-policy"])


if __name__ == "__main__":
    unittest.main()
