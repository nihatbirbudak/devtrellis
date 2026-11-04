"""Komut satırının kalıcı özel kaynakları aynı alan adı ve robots sınırlarıyla topladığını doğrular."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".deps"))

import calistir
from radar import toplayici
from radar.depo import Depo
from radar.kaynak_havuzu import kaynaklari_oku
from radar.kaynak_yonetimi import KaynakYonetimi


class KomutSatiriTestleri(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "cli.sqlite3"
        self.depo = Depo(self.path, kaynaklari_oku())
        self.manager = KaynakYonetimi(self.depo)
        self.source = self.manager.ekle(name="CLI test kaynağı", fetch_url="https://news.cli-example.org/feed.xml",
                                       kind="rss", topic="robotik", publisher="Test kurumu", branches=["Robot kontrolü"])
        self.feed = ("<rss version='2.0'><channel><title>Test</title><item><guid>one</guid>"
                     "<title>Public robot control research</title><link>https://news.cli-example.org/article</link>"
                     "<description>A public robotics research announcement.</description></item></channel></rss>").encode()

    def run_cli(self, *, robots=b"User-agent: *\nAllow: /\n"):
        requests = []

        def response(url, allowed_hosts, headers, deadline, max_bytes, *, stop_event=None):
            requests.append(url)
            if stop_event is not None:
                self.assertFalse(stop_event.is_set())
            body = robots if url.endswith("/robots.txt") else self.feed
            return toplayici.FetchResult(200, body, {}, url)

        output = io.StringIO()
        with patch.object(toplayici, "APPROVED_FETCH_HOSTS", frozenset()), \
                patch.object(toplayici, "_ROBOTS_CACHE", {}), \
                patch.object(toplayici, "_request_once", side_effect=response), \
                patch.object(toplayici.socket, "getaddrinfo", side_effect=AssertionError("Gerçek ağ açılmamalı")), \
                patch.object(sys, "argv", ["calistir.py", "topla", "--db", str(self.path), "--kaynak", self.source["id"]]), \
                redirect_stdout(output):
            calistir.main()
            self.assertIn("news.cli-example.org", toplayici.APPROVED_FETCH_HOSTS)
            self.assertNotIn("other.cli-example.org", toplayici.APPROVED_FETCH_HOSTS)
        return json.loads(output.getvalue()), requests

    def test_yeni_cli_baslangici_ozel_kaynagi_yetkilendirir_ve_gercek_ayristiriciyla_toplar(self):
        result, requests = self.run_cli()
        self.assertFalse(result["running"])
        self.assertEqual(requests, ["https://news.cli-example.org/robots.txt", self.source["fetch_url"]])
        source = self.manager.oku(self.source["id"])
        self.assertIsNone(source["last_error"])
        items = [item for item in self.depo.gorunum()["items"] if item["source_id"] == source["id"]]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Public robot control research")
        self.assertEqual(self.run_cli()[1], [])

    def test_ozel_kaynak_robots_yasagini_ve_kullanici_kapatmasini_asamaz(self):
        result, requests = self.run_cli(robots=b"User-agent: *\nDisallow: /feed.xml\n")
        self.assertEqual(requests, ["https://news.cli-example.org/robots.txt"])
        self.assertIn("robots", self.manager.oku(self.source["id"])["last_error"])
        self.assertFalse(result["running"])
        self.manager.kaydet(self.source["id"], enabled=False, interval_hours=48, expected_version=0)
        self.assertEqual(self.run_cli()[1], [])


if __name__ == "__main__":
    unittest.main()
