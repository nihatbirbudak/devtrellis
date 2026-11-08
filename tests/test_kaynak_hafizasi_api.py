"""Kaynak hafızasının kapalı varsayılanını, CLI doğrulamasını ve kamu HTTP güvenlik sınırlarını sınar."""

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import topluluk
from radar.kaynak_hafizasi import KaynakHafizasi, KaynakTanimi
from radar.kaynak_hafizasi_api import CONFIG_LIMIT, PREFIX, kaynak_hafizasini_hazirla, yapilandirmayi_oku
from radar.toplayici import FetchResult
from radar.topluluk_api import uygulama


ORIGIN = "https://community.example.org"
URL = "https://blog.youtube/news-and-events/source-memory-pilot/"
TEXT = ("This public technology article describes a source comparison with bounded requests. "
        "The page explains how the checked representation can be used by multiple readers. "
        "An unchanged page does not establish that every claim is current or independently verified. "
        "Further research beyond this source may still be necessary.")


def public_response(source, **kwargs):
    return FetchResult(200, f"<article><h1>Public source comparison and limitations</h1><p>{TEXT}</p></article>".encode(),
                       {"content-type": "text/html"}, source.url)


def configuration(**changes):
    return {"schema_version": 1, "daily_limit": 100,
            "sources": [{"id": "pilot", "url": URL, "language": "en"}], **changes}


class KaynakHafizasiAPITestleri(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = [100000.0]
        self.fetch = Mock(side_effect=public_response)
        self.memory = KaynakHafizasi(self.root / "memory.sqlite3", [KaynakTanimi("pilot", URL)],
                                    fetcher=self.fetch, clock=lambda: self.now[0], daily_limit=2)
        self.app = uygulama(self.root / "community.sqlite3", public_url=ORIGIN,
                            sources=[], source_memory=self.memory, clock=lambda: self.now[0])
        self.client = TestClient(self.app, base_url=ORIGIN)
        self.addCleanup(self.client.close)

    def headers(self):
        token = self.client.post("/api/community/session", json={}, headers={"Origin": ORIGIN}).json()["csrf_token"]
        return {"Origin": ORIGIN, "X-Ufuk-CSRF": token}

    def post(self, payload=None, *, headers=None, suffix="/pilot/check"):
        return self.client.post(PREFIX + suffix, json={} if payload is None else payload,
                                headers=self.headers() if headers is None else headers)

    def test_catalog_and_read_are_anonymous_network_free_and_small(self):
        catalog = self.client.get(PREFIX)
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(catalog.json()["sources"][0]["url"], URL)
        self.assertNotIn("set-cookie", catalog.headers)
        detail = self.client.get(PREFIX + "/pilot")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["state"], "missing")
        self.assertNotIn("body", detail.json())
        self.assertNotIn("etag", detail.json())
        self.fetch.assert_not_called()

    def test_check_uses_configured_source_reuses_recent_result_and_tracks_daily_budget(self):
        first = self.post()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue(first.json()["check_succeeded"])
        self.assertFalse(first.json()["claims_verified"])
        self.assertEqual(self.fetch.call_args.args[0].url, URL)
        self.assertFalse(self.post().json()["check_started"])
        self.assertEqual(self.fetch.call_count, 1)
        self.now[0] += 4000
        self.assertTrue(self.post().json()["check_started"])
        self.now[0] += 4000
        final = self.post().json()
        self.assertFalse(final["check_started"])
        self.assertTrue(final["budget_exhausted"])
        self.assertEqual(self.fetch.call_count, 2)

    def test_no_freshness_requirement_does_not_start_work(self):
        result = self.post({"freshness_required": False})
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()["check_started"])
        self.fetch.assert_not_called()

    def test_same_origin_anonymous_session_and_csrf_are_required(self):
        self.assertEqual(self.post(headers={"Origin": ORIGIN}).status_code, 401)
        correct = self.headers()
        for headers in ({"Origin": ORIGIN}, {"X-Ufuk-CSRF": correct["X-Ufuk-CSRF"]},
                        {**correct, "Origin": "https://elsewhere.example.org"},
                        {**correct, "Sec-Fetch-Site": "cross-site"}, {"X-DevTrellis-Client": "1"},
                        {**correct, "Host": "untrusted.example.org"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.post(headers=headers).status_code, 403)
        self.fetch.assert_not_called()

    def test_unknown_private_or_coerced_inputs_never_echo_or_fetch(self):
        private = "private.person@example.com private conversation detail"
        payloads = [{"question": private}, {"url": "https://127.0.0.1/private"}, [private],
                    {"max_age_seconds": 0}, {"max_age_seconds": True}, {"max_age_seconds": "60"},
                    {"max_age_seconds": 59}, {"max_age_seconds": 86401},
                    {"freshness_required": "false"}, {"freshness_required": 0}]
        for payload in payloads:
            with self.subTest(payload=payload):
                result = self.post(payload)
                self.assertEqual(result.status_code, 422, result.text)
                self.assertNotIn(private, result.text)
                self.assertNotIn("127.0.0.1", result.text)
        malformed = self.client.post(PREFIX + "/pilot/check", content='{"private":"' + private,
                                     headers={**self.headers(), "Content-Type": "application/json"})
        self.assertEqual(malformed.status_code, 422)
        self.assertNotIn(private, malformed.text)
        self.fetch.assert_not_called()

    def test_arbitrary_source_queries_and_summary_writes_are_not_routes(self):
        self.assertEqual(self.post(suffix="/unknown/check").status_code, 404)
        self.assertEqual(self.client.get(PREFIX + "/unknown").status_code, 404)
        for suffix in ("", "/pilot"):
            result = self.client.get(PREFIX + suffix, params={"question": "private test query"})
            self.assertEqual(result.status_code, 422)
            self.assertNotIn("private test query", result.text)
        self.assertEqual(self.post({"summary": "Unrequested publication"}, suffix="/pilot/summary").status_code, 404)
        self.assertEqual(self.client.get(PREFIX + "/pilot/check").status_code, 405)
        self.fetch.assert_not_called()

    def test_json_security_headers_limits_and_no_new_client_exemption(self):
        result = self.client.post(PREFIX + "/pilot/check", content="{}", headers={**self.headers(), "Content-Type": "text/plain"})
        self.assertEqual(result.status_code, 415)
        self.assertEqual(self.post({"freshness_required": False}).headers["cache-control"], "no-store")
        for _ in range(5):
            self.assertEqual(self.post({"freshness_required": False}).status_code, 200)
        limited = self.post({"freshness_required": False})
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.headers["retry-after"], "60")
        self.fetch.assert_not_called()

    def test_no_http_read_or_post_can_supply_unapproved_source_or_change_summary(self):
        for field in ("source_id", "sources", "source", "approved_public", "summary", "model"):
            with self.subTest(field=field):
                self.assertEqual(self.post({field: "private data"}).status_code, 422)
        with closing(sqlite3.connect(self.memory.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM source_memory_summaries").fetchone()[0], 0)
        self.fetch.assert_not_called()


class KaynakHafizasiYapilandirmaTestleri(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "sources.json"
        self.memory_path = self.root / "memory.sqlite3"
        self.community = self.root / "community.sqlite3"
        self.feed = self.root / "feed.sqlite3"

    def save(self, value):
        self.config.write_text(json.dumps(value), encoding="utf-8")

    def prepare(self):
        return kaynak_hafizasini_hazirla(self.config, self.memory_path, self.community, self.feed)

    def cli(self, *extra):
        with patch.dict("os.environ", {"DEVTRELLIS_PROXY_TOKEN": "p" * 32, "DEVTRELLIS_RATE_KEY": "ab" * 32}):
            return topluluk.main(["--db", str(self.community), "serve", "--public-url", ORIGIN,
                                  "--feed-db", str(self.feed), "--rate-db", str(self.root / "private-rate" / "limits.sqlite3"),
                                  *map(str, extra)])

    def test_default_disabled_creates_no_memory_database_or_routes_or_request(self):
        self.assertIsNone(kaynak_hafizasini_hazirla(None, None, self.community, self.feed))
        with patch("radar.kaynak_hafizasi.kosullu_oku") as network, \
             patch("radar.kaynak_hafizasi_api.KaynakHafizasi") as constructor:
            app = uygulama(self.community, public_url=ORIGIN, feed_db_path=self.feed, sources=[])
            with TestClient(app, base_url=ORIGIN) as client:
                self.assertEqual(client.get(PREFIX).status_code, 404)
                self.assertEqual(client.get(PREFIX + "/pilot").status_code, 404)
                token = client.post("/api/community/session", json={}, headers={"Origin": ORIGIN}).json()["csrf_token"]
                result = client.post(PREFIX + "/pilot/check", json={}, headers={"Origin": ORIGIN, "X-Ufuk-CSRF": token})
                self.assertEqual(result.status_code, 404)
            constructor.assert_not_called()
            network.assert_not_called()
        self.assertFalse(hasattr(app.state, "source_memory"))
        self.assertFalse(self.memory_path.exists())
        with closing(sqlite3.connect(self.community)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'source_memory%'").fetchone()[0], 0)

    def test_configuration_accepts_only_explicit_approved_sources_and_preserves_version_query(self):
        self.save(configuration(sources=[{"id": "pilot", "url": URL + "?version=2", "language": "en"}]))
        sources, limit = yapilandirmayi_oku(self.config)
        self.assertEqual(sources[0].url, URL + "?version=2")
        self.assertEqual(limit, 100)
        self.assertFalse(self.memory_path.exists())
        with patch("radar.kaynak_hafizasi.kosullu_oku") as network:
            self.assertEqual(len(self.prepare().sources), 1)
            network.assert_not_called()

    def test_invalid_config_and_private_values_fail_without_any_database_or_echo(self):
        private = "private.person@example.com"
        bad = [configuration(schema_version=True), configuration(daily_limit=True), configuration(sources=[]),
               configuration(sources=[{"id": "x", "url": "https://not-approved.example.org/article", "language": "en"}]),
               configuration(sources=[{"id": "pilot", "url": URL + "?token=" + private, "language": "en"}]),
               configuration(sources=configuration()["sources"] * 21), configuration(question=private),
               configuration(sources=[{**configuration()["sources"][0], "cookie": private}])]
        for value in bad:
            self.save(value)
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                self.prepare()
            self.assertNotIn(private, str(caught.exception))
            self.assertFalse(self.memory_path.exists())
            self.assertFalse(self.community.exists())
            self.assertFalse(self.feed.exists())
        for raw in ('{"schema_version":1,"schema_version":1}', "x" * (CONFIG_LIMIT + 1)):
            self.config.write_text(raw, encoding="utf-8")
            with self.assertRaises(ValueError):
                self.prepare()
        self.assertFalse(self.memory_path.exists())

    def test_options_must_be_paired_and_databases_separate_before_creating_files(self):
        self.save(configuration())
        for config, database in ((None, self.memory_path), (self.config, None), (self.config, self.community), (self.config, self.feed)):
            with self.subTest(config=config, database=database), self.assertRaises(ValueError):
                kaynak_hafizasini_hazirla(config, database, self.community, self.feed)
            self.assertFalse(self.memory_path.exists())
            self.assertFalse(self.community.exists())
            self.assertFalse(self.feed.exists())

    def test_cli_default_disabled_does_not_construct_memory(self):
        with patch("radar.kaynak_hafizasi_api.KaynakHafizasi") as constructor, \
             patch("uvicorn.run") as serve, redirect_stdout(io.StringIO()):
            self.assertEqual(self.cli(), 0)
        constructor.assert_not_called()
        self.assertFalse(hasattr(serve.call_args.args[0].state, "source_memory"))
        self.assertFalse(self.memory_path.exists())

    def test_cli_validates_config_before_core_databases_and_does_not_start_server_on_error(self):
        self.save(configuration(sources=[{"id": "pilot", "url": "https://unknown.example.org/article", "language": "en"}]))
        error = io.StringIO()
        with patch("uvicorn.run") as serve, redirect_stderr(error), self.assertRaises(SystemExit):
            self.cli("--source-memory-config", self.config, "--source-memory-db", self.memory_path)
        serve.assert_not_called()
        self.assertNotIn("unknown.example.org", error.getvalue())
        for path in (self.community, self.feed, self.memory_path):
            self.assertFalse(path.exists())

    def test_cli_opt_in_creates_separate_database_but_never_starts_a_source_check(self):
        self.save(configuration())
        with patch("radar.kaynak_hafizasi.kosullu_oku") as network, patch("uvicorn.run") as serve, redirect_stdout(io.StringIO()):
            self.assertEqual(self.cli("--source-memory-config", self.config, "--source-memory-db", self.memory_path), 0)
        network.assert_not_called()
        self.assertEqual(serve.call_args.args[0].state.source_memory.path, self.memory_path)
        self.assertTrue(all(path.is_file() for path in (self.community, self.feed, self.memory_path)))

    def test_programmatic_conflicting_database_is_rejected_before_core_schema(self):
        memory = KaynakHafizasi(self.memory_path, [KaynakTanimi("pilot", URL)], fetcher=Mock())
        with self.assertRaises(ValueError):
            uygulama(self.memory_path, public_url=ORIGIN, feed_db_path=self.feed, sources=[], source_memory=memory)
        with closing(sqlite3.connect(self.memory_path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='community_accounts'").fetchone()[0], 0)
        self.assertFalse(self.feed.exists())


if __name__ == "__main__":
    unittest.main()
