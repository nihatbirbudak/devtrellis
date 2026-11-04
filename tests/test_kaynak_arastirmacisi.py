"""Kaynak keşfinin kalıcı bütçesini, güvenli deneme sürecini ve bağımsız kapsam örneklemesini sınar."""

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from radar.depo import Depo
from radar.kaynak_arastirmacisi import (
    KaynakArastirmacisi, KaynakArastirmaciSiniri, KaynakArastirmaciMesgul, KaynakAdayCatismasi,
)
from radar.kaynak_denetcisi import KaynakDenetcisi, kamusal_adres, yayin_kimlikleri
from radar.kaynak_yonetimi import KaynakYonetimi
from radar.konular import KONULAR
from radar.toplayici import CollectionError, FetchResult


NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


def feed(url, link=None):
    project = urlsplit(url).path.rsplit("/", 1)[0]
    link = link or "https://github.com" + project + "/releases/tag/v1"
    body = (f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><id>{url}</id>'
            '<title>Research releases</title><updated>2026-10-25T00:00:00Z</updated>'
            f'<entry><id>{link}</id><title>New research release</title><link href="{link}"/>'
            '<updated>2026-10-25T00:00:00Z</updated><summary>Public research release</summary></entry></feed>').encode()
    return FetchResult(200, body, {}, url)


def work(number=1, **fields):
    return {"id": f"https://openalex.org/W{number}", "doi": f"https://doi.org/10.1234/{number}",
            "title": f"Research evaluation publication {number}", "publication_date": "2026-10-25",
            "primary_location": {"landing_page_url": f"https://journal.example.org/article/{number}"}, **fields}


def repository(name="owner/project", **fields):
    return {"full_name": name, "html_url": "https://github.com/" + name, "archived": False, "fork": False, "disabled": False,
            "stargazers_count": 50, "size": 1000, "pushed_at": NOW.isoformat(),
            "created_at": (NOW - timedelta(days=400)).isoformat(), "description": "Research agent framework", "topics": [], **fields}


class Ogrenme:
    def __init__(self):
        self.sources, self.enabled, self.evaluations = [], True, 0

    def gorunum(self):
        return {"sources": self.sources, "settings": {"enabled": self.enabled}}

    def kaynak_denemeye_al(self, source_id, provenance=None):
        if not any(source["source_id"] == source_id for source in self.sources):
            self.sources.append({"source_id": source_id, "lifecycle": "trial", "enabled": True, "provenance": provenance})

    def degerlendir(self):
        self.evaluations += 1


class KaynakArastirmacisiTestleri(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.depo = Depo(Path(self.temp.name) / "radar.sqlite3", [])
        self.ogrenme = Ogrenme()
        self.now = NOW
        self.calls = []
        self.github_count = 0

    def fetch(self, url, hosts, **kwargs):
        self.calls.append((url, hosts, kwargs))
        if "api.openalex.org" in url:
            return FetchResult(200, json.dumps({"results": [work()]}).encode(), {}, url)
        if "api.github.com/search" in url:
            self.github_count += 1
            name = f"research/project{self.github_count}"
            query = parse_qs(urlsplit(url).query)["q"][0]
            description = ("Robot navigation library" if "robot" in query else "Greenhouse irrigation controls" if any(token in query for token in ("greenhouse", "irrigation"))
                           else "Video editing speech synthesis" if any(token in query for token in ("video", "text-to-speech"))
                           else "Smart home voice assistant" if any(token in query for token in ("voice-assistant", "home-automation"))
                           else "Secure application security tools" if any(token in query for token in ("application-security", "security-tools"))
                           else "Data engineering database library" if any(token in query for token in ("data-engineering", "database"))
                           else "Software developer testing framework" if any(token in query for token in ("developer-tools", "software-engineering"))
                           else "Research agent framework")
            return FetchResult(200, json.dumps({"items": [repository(name, description=description)]}).encode(), {}, url)
        if "releases.atom" in url:
            return feed(url)
        return feed(url, "https://" + urlsplit(url).hostname + "/news/1")

    def service(self, fetch=None, curated=False, learning=None):
        context = patch.object(KaynakArastirmacisi, "_havuzu_yukle") if not curated else None
        if context:
            context.start()
        try:
            service = KaynakArastirmacisi(self.depo, learning or self.ogrenme, fetcher=fetch or self.fetch, clock=lambda: self.now)
        finally:
            if context:
                context.stop()
        self.addCleanup(service.kapan)
        return service

    def run_github(self, service):
        # Bu prova yalnız depo beslemeleriyle sınırlı deneme takibini sınar.
        with patch("radar.kaynak_arastirmacisi.KaynakDenetcisi.calistir", return_value={
                "samples": [], "topics_checked": ["ai", "jarvis", "robotik", "tarim", "medya"], "errors": [], "counts": {}}):
            return service.calistir()

    def test_baslangic_ag_ve_model_kullanmadan_gorunum_saglar(self):
        service = self.service(curated=True)
        view = service.gorunum()
        self.assertEqual(self.calls, [])
        self.assertGreater(len(view["candidates"]), 0)
        self.assertEqual(view["budget"]["remaining_requests"], 20)
        self.assertIn("bütün internet", view["coverage"]["note"])
        self.assertEqual(self.depo.kaynaklar(), [])

    def test_gercek_akis_bes_konuyu_denetler_ve_en_cok_uc_yeni_kaynak_ekler(self):
        service = self.service()
        result = service.calistir()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["coverage"]["topics_checked"]), 5)
        self.assertEqual(result["coverage"]["sample_count"], 5)
        self.assertEqual(len(result["searches"]), 5)
        self.assertEqual(len(result["probed"]), 5)
        self.assertEqual(len(result["added_sources"]), 3)
        self.assertEqual(result["budget_after"]["probes"], 5)
        self.assertLessEqual(result["budget_after"]["requests"], 20)
        self.assertEqual(len(self.depo.kaynaklar()), 3)
        self.assertEqual({source["interval_hours"] for source in self.depo.kaynaklar()}, {72})
        with self.depo.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM source_coverage_samples").fetchone()[0], 5)
        self.assertTrue(all(call[2]["deadline"] for call in self.calls))
        self.assertTrue(all("stop_event" in call[2] for call in self.calls))

    def test_tekrar_ve_yeniden_acilis_haftalik_sinirlari_sifirlamaz(self):
        service = self.service()
        service.calistir()
        reopened = self.service()
        reopened.calistir()
        self.assertEqual(reopened.gorunum()["budget"]["requests"], 20)
        self.assertEqual(reopened.gorunum()["budget"]["probes"], 5)
        calls = len(self.calls)
        with self.assertRaises(KaynakArastirmaciSiniri):
            reopened.calistir()
        self.assertEqual(len(self.calls), calls)
        self.now += timedelta(days=7)
        self.assertEqual(reopened.gorunum()["budget"]["requests"], 0)

    def test_tum_erisim_hatalari_basarili_bos_sonuc_olmaz(self):
        def denied(url, hosts, **kwargs):
            return FetchResult(429, b'{}', {}, url)
        result = self.service(denied).calistir()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["coverage"]["status"], "failed")
        self.assertEqual(len(result["coverage"]["errors"]), 5)
        self.assertEqual(result["budget_after"]["requests"], 10)
        self.assertEqual(self.depo.kaynaklar(), [])

    def test_yonlendirme_ve_yerel_adres_reddedilir(self):
        service = self.service(lambda url, hosts, **kwargs: FetchResult(200, b'{}', {}, "https://other.example.org/feed"))
        with self.assertRaises(CollectionError):
            service._oku("https://example.org/feed", ["example.org"], __import__('time').monotonic() + 20)
        for url in ("http://example.org/rss", "https://127.0.0.1/feed", "https://local.internal/rss",
                    "https://example.org:444/feed", "https://user:pass@example.org/feed",
                    "https://example.org/feed?%61pi_key=hidden", "https://example.org/rss#token"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                kamusal_adres(url)

    def test_sayfada_ilan_edilen_ayni_alan_beslemesi_okunur(self):
        def read(url, hosts, **kwargs):
            self.calls.append(url)
            if url.endswith("/news"):
                return FetchResult(200, b'<html><link rel="alternate" type="application/rss+xml" href="/feed"/></html>', {}, url)
            return feed(url, "https://new.example.org/story/1")
        service = self.service(read)
        identifier = service._aday_ekle(name="New publisher", url="https://new.example.org/news", topic="ai", discovered_from="https://new.example.org/")
        with self.depo.baglanti() as connection:
            candidate = dict(connection.execute("SELECT * FROM source_scout_candidates WHERE id=?", (identifier,)).fetchone())
        service._besleme_dene(candidate, __import__('time').monotonic() + 20)
        verified = service.gorunum()["candidates"][0]
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(verified["feed_url"], "https://new.example.org/feed")
        self.assertEqual(len(self.calls), 2)
        added = service.aday_karari(identifier, "trial", verified["version"])
        self.assertEqual(added["status"], "trial")
        self.assertEqual(len(self.depo.kaynaklar()), 1)

    def test_baska_alan_beslemesi_ve_kotu_xml_takibe_girmez(self):
        for body in (b'<link rel="alternate" type="application/rss+xml" href="https://evil.example.org/rss">', b'<rss><channel>broken',
                     b'<html>not a feed</html>'):
            with self.subTest(body=body):
                service = self.service(lambda url, hosts, **kwargs: FetchResult(200, body, {}, url))
                candidate = {"id": "missing", "url": "https://good.example.org/news", "kind": "page"}
                with self.assertRaises(CollectionError):
                    service._besleme_dene(candidate, __import__('time').monotonic() + 20)
        self.assertEqual(len(self.depo.kaynaklar()), 0)

    def test_github_beslemesi_baska_deponun_surumu_ile_dogrulanmaz(self):
        service = self.service(lambda url, hosts, **kwargs: feed(url, "https://github.com/other/repo/releases/tag/v1"))
        with self.assertRaises(CollectionError):
            service._besleme_dene({"id": "none", "url": "https://github.com/good/repo", "kind": "github"}, __import__('time').monotonic() + 20)

    def test_aday_karari_surum_ve_dogrulama_kontrolu_yapar(self):
        service = self.service(curated=True)
        candidate = service.gorunum()["candidates"][0]
        with self.assertRaises(ValueError):
            service.aday_karari(candidate["id"], "trial", candidate["version"])
        dismissed = service.aday_karari(candidate["id"], "dismiss", candidate["version"])
        self.assertEqual(dismissed["status"], "dismissed")
        with self.assertRaises(KaynakAdayCatismasi):
            service.aday_karari(candidate["id"], "dismiss", candidate["version"])
        with self.assertRaises(KeyError):
            service.aday_karari("absent", "dismiss", 0)

    def test_kullanici_reddi_gelecek_aramayla_sifirlanmaz(self):
        service = self.service()
        identifier = service._aday_ekle(name="Candidate", url="https://candidate.example.org/feed", topic="ai", discovered_from="https://candidate.example.org/")
        service.aday_karari(identifier, "dismiss", 0)
        self.assertEqual(service._aday_ekle(name="Changed", url="https://candidate.example.org/feed", topic="jarvis", discovered_from="https://candidate.example.org/"), identifier)
        candidate = service.gorunum()["candidates"][0]
        self.assertEqual(candidate["status"], "dismissed")
        self.assertEqual(candidate["topic"], "ai")

    def test_aktif_deneme_siniri_kapali_kaynagi_saymaz(self):
        service = self.service()
        self.ogrenme.sources = [{"lifecycle": "trial", "enabled": True}, {"lifecycle": "trial", "enabled": False}, {"lifecycle": "regular", "enabled": True}]
        self.assertEqual(service._aktif_deneme_sayisi(), 1)

    def test_eski_dogrulama_manuel_takibe_alinamaz(self):
        service = self.service()
        result = self.run_github(service)
        verified = next(candidate for candidate in service.gorunum()["candidates"] if candidate["status"] == "verified")
        self.ogrenme.sources = []
        self.now += timedelta(days=8)
        with self.assertRaises(ValueError):
            service.aday_karari(verified["id"], "trial", verified["version"])
        self.assertEqual(len(self.depo.kaynaklar()), 3)

    def test_bekleyen_github_adayi_bir_hafta_sonra_yine_besleme_uzerinden_dogrulanir(self):
        service = self.service()
        self.run_github(service)
        candidate = next(candidate for candidate in service.gorunum()["candidates"] if candidate["status"] == "verified")
        with self.depo.baglanti() as connection:
            stored = dict(connection.execute("SELECT * FROM source_scout_candidates WHERE id=?", (candidate["id"],)).fetchone())
        self.now += timedelta(days=8)
        self.calls.clear()
        service._besleme_dene(stored, __import__('time').monotonic() + 20)
        updated = next(row for row in service.gorunum()["candidates"] if row["id"] == candidate["id"])
        self.assertEqual(updated["status"], "verified")
        self.assertEqual(updated["probe"]["original_kind"], "github")
        self.assertTrue(self.calls[0][0].endswith("/releases.atom"))

    def test_bekleyen_sayfa_adayi_tekrar_kayitli_beslemeyi_dogrular(self):
        service = self.service()
        identifier = service._aday_ekle(name="Publisher", url="https://publisher.example.org/news", topic="ai", discovered_from="https://publisher.example.org/")
        service._aday_guncelle(identifier, status="verified", kind="atom", feed_url="https://publisher.example.org/feed",
                               checked_at=self.now.isoformat(), probe=json.dumps({"validated": True, "original_kind": "page"}))
        with self.depo.baglanti() as connection:
            candidate = dict(connection.execute("SELECT * FROM source_scout_candidates WHERE id=?", (identifier,)).fetchone())
        self.now += timedelta(days=8)
        service._besleme_dene(candidate, __import__('time').monotonic() + 20)
        self.assertEqual(self.calls[0][0], "https://publisher.example.org/feed")
        updated = next(row for row in service.gorunum()["candidates"] if row["id"] == identifier)
        self.assertEqual(updated["probe"]["original_kind"], "page")

    def test_kaynak_eklendikten_sonra_cokme_yeniden_acilista_ikinci_kaynak_uretmez(self):
        service = self.service()
        original = self.ogrenme.kaynak_denemeye_al
        with patch.object(self.ogrenme, "kaynak_denemeye_al", side_effect=RuntimeError("interrupted")):
            self.run_github(service)
        before = len(self.depo.kaynaklar())
        self.assertGreater(before, 0)
        reopened = self.service()
        self.assertEqual(len(self.depo.kaynaklar()), before)
        self.assertTrue(all(candidate["status"] != "activating" for candidate in reopened.gorunum()["candidates"]))
        self.assertEqual(len(self.ogrenme.sources), before)

    def test_gercek_ogrenme_isaretlemesi_cokerse_uc_rezerve_kaynak_siniri_korunur(self):
        from radar.kaynak_ogrenme import KaynakOgrenme
        learning = KaynakOgrenme(self.depo, clock=lambda: self.now)
        service = self.service(learning=learning)
        with patch.object(learning, "kaynak_denemeye_al", side_effect=RuntimeError("interrupted")):
            self.run_github(service)
        self.assertEqual(len(self.depo.kaynaklar()), 3)
        self.assertEqual(service._aktif_deneme_sayisi(), 3)
        reopened = self.service(learning=learning)
        self.assertEqual(len(self.depo.kaynaklar()), 3)
        self.assertEqual(reopened._aktif_deneme_sayisi(), 3)

    def test_kaynak_tercihleri_varolan_kaynak_icin_degismez(self):
        service = self.service()
        existing = KaynakYonetimi(self.depo).ekle(name="Existing", publisher="Publisher", kind="atom", topic="ai", branches=["Existing"],
                                                fetch_url="https://github.com/research/project1/releases.atom", interval_hours=168)
        KaynakYonetimi(self.depo).kaydet(existing["id"], enabled=False, interval_hours=720, expected_version=0)
        self.run_github(service)
        row = KaynakYonetimi(self.depo).oku(existing["id"])
        self.assertFalse(row["enabled"])
        self.assertEqual(row["interval_hours"], 720)
        self.assertFalse(any(source["source_id"] == existing["id"] for source in self.ogrenme.sources))

    def test_konulara_gore_arama_yalniz_bilinen_kamusal_terimi_gonderir(self):
        service = self.service()
        source = KaynakYonetimi(self.depo).ekle(name="Public", publisher="Publisher", kind="rss", topic="ai", branches=["AI research"], fetch_url="https://example.org/rss")
        for number in (1, 2):
            self.depo.kaydet(source["id"], [{"url": f"https://example.org/{number}", "title": "Memory evidence research", "excerpt": "Example public information"}])
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE items SET feedback='yararli'")
            item = connection.execute("SELECT id FROM items LIMIT 1").fetchone()[0]
            connection.execute("INSERT INTO feedback_events(item_id,value,note,created_at) VALUES(?,'yararli','PRIVATE REQUIREMENT',?)", (item, NOW.isoformat()))
        query = service._sorgu("ai", 0)
        self.assertIn("memory", query)
        self.assertNotIn("PRIVATE", query)

    def test_tek_haberde_es_anlamlilar_iki_ayri_ilgi_kaniti_sayilmaz(self):
        service = self.service()
        source = KaynakYonetimi(self.depo).ekle(name="Public", publisher="Publisher", kind="rss", topic="ai", branches=["AI research"], fetch_url="https://example.org/rss")
        self.depo.kaydet(source["id"], [{"url": "https://example.org/1", "title": "Memory hafıza agents", "excerpt": "Public research"}])
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE items SET feedback='yararli'")
        self.assertNotIn("memory", service._sorgu("ai", 0))

    def test_aylar_sonra_aday_birikimi_yeni_kesfi_tamamen_durdurmaz(self):
        service = self.service()
        with self.depo.baglanti(write=True) as connection:
            for number in range(500):
                connection.execute("INSERT INTO source_scout_candidates(id,name,url,topic,kind,status,reason,discovered_from,created_at) VALUES(?,?,?,'ai','page','candidate','Pending',?,?)",
                                   (f"old_{number}", "Old candidate", f"https://old.example.org/{number}", "https://old.example.org/", NOW.isoformat()))
        identifier = service._aday_ekle(name="New source", url="https://new.example.org/rss", topic="ai", discovered_from="https://new.example.org/")
        self.assertIsNotNone(identifier)
        with self.depo.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM source_scout_candidates WHERE status='retired'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM source_scout_candidates").fetchone()[0], 501)

    def test_baska_isci_ve_karar_suren_is_sirasinda_engellenir(self):
        entered, release = threading.Event(), threading.Event()
        def blocking(url, hosts, **kwargs):
            entered.set()
            release.wait(5)
            return self.fetch(url, hosts, **kwargs)
        service = self.service(blocking)
        self.addCleanup(release.set)
        service.baslat()
        self.assertTrue(entered.wait(3))
        with self.assertRaises(KaynakArastirmaciMesgul):
            service.baslat()
        with self.assertRaises(KaynakArastirmaciMesgul):
            service.aday_karari("none", "dismiss", 0)
        release.set()
        service.thread.join(10)
        self.assertFalse(service.gorunum()["running"])

    def test_ogrenme_kapaliysa_zamanli_scout_calismaz(self):
        service = self.service()
        self.ogrenme.enabled = False
        with patch.object(service.stop, "wait", return_value=True):
            service.zamanli_calistir()
        self.assertEqual(self.ogrenme.evaluations, 1)
        self.assertEqual(self.calls, [])

    def test_repo_sitesinden_yeni_web_kaynagi_adayi_bulunur(self):
        def read(url, hosts, **kwargs):
            return FetchResult(200, json.dumps({"items": [
                repository(homepage="https://project.example.org/news"),
                repository("owner/another", homepage="https://second.example.org/news")]}).encode(), {}, url)
        service = self.service(read)
        service._github_ara("ai", 0, {"searches": []}, __import__('time').monotonic() + 20)
        pages = [candidate for candidate in service.gorunum()["candidates"] if candidate["kind"] == "page"]
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["url"], "https://project.example.org/news")
        self.assertEqual(pages[0]["discovered_from"], "https://github.com/owner/project")

    def test_ayni_beslemeyi_bulan_baska_sayfa_mevcut_kaynagi_trial_yapmaz(self):
        service = self.service()
        existing = KaynakYonetimi(self.depo).ekle(name="Existing", publisher="Publisher", kind="atom", topic="ai", branches=["Existing"],
                                                fetch_url="https://publisher.example.org/feed", interval_hours=168)
        identifier = service._aday_ekle(name="Other page", url="https://publisher.example.org/news", topic="ai", discovered_from="https://publisher.example.org/")
        service._aday_guncelle(identifier, status="verified", kind="atom", feed_url="https://publisher.example.org/feed",
                               checked_at=self.now.isoformat(), probe=json.dumps({"validated": True}))
        candidate = next(candidate for candidate in service.gorunum()["candidates"] if candidate["id"] == identifier)
        result = service.aday_karari(identifier, "trial", candidate["version"])
        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(result["source_id"], existing["id"])
        self.assertEqual(self.ogrenme.sources, [])
        self.assertEqual(service.gorunum()["budget"]["trials"], 0)
        self.assertEqual(len(self.depo.kaynaklar()), 1)

    def test_gercek_ogrenme_modulu_ile_trial_idempotent(self):
        from radar.kaynak_ogrenme import KaynakOgrenme
        learning = KaynakOgrenme(self.depo, clock=lambda: self.now)
        service = self.service(learning=learning)
        result = self.run_github(service)
        self.assertEqual(len(result["added_sources"]), 3)
        sources = learning.gorunum()["sources"]
        self.assertEqual(sum(source["lifecycle"] == "trial" for source in sources), 3)

    def test_depo_niteligi_konu_etkinlik_ve_spami_ayri_kontrol_eder(self):
        service = self.service()
        good, reason = service._depo_niteligi(repository("owner/voice-tool", description="Local voice assistant"), "jarvis")
        self.assertTrue(good["validated"])
        self.assertIsNone(reason)
        cases = [repository("someone/Streamauthor-Full-Version", description="Video editing", stargazers_count=1000),
                 repository(description="Research agent", stargazers_count=1),
                 repository(description="Research agent", pushed_at=(NOW - timedelta(days=731)).isoformat()),
                 repository(description="Research agent", created_at=(NOW - timedelta(days=1)).isoformat()),
                 repository(description="Research agent", archived=True),
                 repository("someone/leaf-study", description="Plant taxonomy reference"),
                 repository("someone/someone", description="Research agent profile"),
                 repository(description="Research agent", size=0)]
        for row in cases:
            with self.subTest(row=row):
                self.assertIsNone(service._depo_niteligi(row, "ai")[0])

    def test_github_sorgusu_yakin_etkin_konu_depolarini_yildiz_sirasiyla_ister(self):
        service = self.service()
        record = {"searches": []}
        service._github_ara("medya", 0, record, __import__('time').monotonic() + 20)
        params = parse_qs(urlsplit(self.calls[0][0]).query)
        self.assertEqual(params["sort"], ["stars"])
        self.assertIn("topic:video-editing", params["q"][0])
        self.assertIn("stars:>=10", params["q"][0])
        self.assertIn("pushed:>=", params["q"][0])
        self.assertEqual(params["per_page"], ["10"])

    def test_ayni_kosuda_uc_depo_iki_sayfa_denemesi_yapilir(self):
        service = self.service()
        for topic in KONULAR:
            service._aday_ekle(name="Publisher " + topic, url=f"https://{topic}.example.org/news", topic=topic, discovered_from=f"https://{topic}.example.org/")
        result = self.run_github(service)
        candidates = {candidate["id"]: candidate for candidate in service.gorunum()["candidates"]}
        kinds = [candidates[identifier]["probe"]["original_kind"] for identifier in result["probed"]]
        self.assertEqual(kinds.count("github"), 3)
        self.assertEqual(kinds.count("page"), 2)
        self.assertEqual(result["budget_after"]["probes"], 5)
        self.assertLessEqual(result["budget_after"]["requests"], 20)
        self.assertEqual(len(result["added_sources"]), 3)


class KaynakDenetcisiTestleri(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.depo = Depo(Path(self.temp.name) / "radar.sqlite3", [])
        self.source = KaynakYonetimi(self.depo).ekle(name="Example journal", publisher="Publisher", kind="rss", topic="ai", branches=["Research"],
                                                    fetch_url="https://journal.example.org/feed")
        self.service = KaynakDenetcisi(self.depo)

    def add(self, url, feedback="yok"):
        self.depo.kaydet(self.source["id"], [{"url": url, "title": "Public scientific evidence", "excerpt": "Public research"}])
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE items SET feedback=? WHERE url=?", (feedback, url))
            return connection.execute("SELECT id FROM items WHERE url=?", (url,)).fetchone()[0]

    def test_doi_buyuk_kucuk_harf_ve_arxiv_surumu_eslesir(self):
        self.assertTrue(yayin_kimlikleri("https://doi.org/10.1234/ABC") & yayin_kimlikleri("https://doi.org/10.1234/abc"))
        self.assertTrue(yayin_kimlikleri("https://arxiv.org/pdf/2601.12345v3.pdf") & yayin_kimlikleri("https://doi.org/10.48550/arxiv.2601.12345"))

    def test_yeni_kaynak_toplanmamis_incelenmemis_ve_reddedilmis_ayrilir(self):
        item_id = self.add("https://doi.org/10.1234/1")
        self.add("https://doi.org/10.1234/2", "gereksiz")
        self.add("https://doi.org/10.1234/3", "yararli")
        snapshot = self.service.anlik_havuz()
        self.assertEqual(self.service.siniflandir({"url": "https://doi.org/10.1234/1"}, snapshot)[:2], ("not_examined", item_id))
        self.assertEqual(self.service.siniflandir({"url": "https://doi.org/10.1234/2"}, snapshot)[0], "rejected")
        self.assertEqual(self.service.siniflandir({"url": "https://doi.org/10.1234/3"}, snapshot)[0], "available")
        self.assertEqual(self.service.siniflandir({"url": "https://journal.example.org/unseen"}, snapshot)[0], "not_collected")
        self.assertEqual(self.service.siniflandir({"url": "https://unknown.example.org/unseen"}, snapshot)[0], "missing_source")

    def test_karsilastirma_sorgu_sirasinda_eklenen_kaydi_bulduk_saymaz(self):
        def read(url, hosts):
            self.add("https://doi.org/10.1234/1")
            return FetchResult(200, json.dumps({"results": [work()]}).encode(), {}, url)
        result = self.service.calistir(run_id="run", fetcher=read, now=NOW, topics=["ai"])
        self.assertEqual(result["samples"][0]["stage"], "not_collected")
        self.assertIsNone(result["samples"][0]["item_id"])
        self.assertEqual(result["samples"][0]["relevance"], "unconfirmed")

    def test_ayni_yayin_ayni_konuda_tek_ornek_sayilir(self):
        def read(url, hosts):
            return FetchResult(200, json.dumps({"results": [work(), work()]}).encode(), {}, url)
        result = self.service.calistir(run_id="run", fetcher=read, now=NOW, topics=["ai"])
        self.assertEqual(result["sample_count"], 1)
        with self.depo.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0], 0)

    def test_eriisim_hatasi_ayri_gorunur_ve_sahte_sifir_basari_vermez(self):
        def read(url, hosts):
            raise CollectionError("Robots erişim engeli")
        result = self.service.calistir(run_id="run", fetcher=read, now=NOW, topics=["ai"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["topics_checked"], [])

    def test_denetcinin_sorgusu_uretim_kesif_sorgusuyla_ayni_degil(self):
        from radar.kesif import SORGULAR
        def read(url, hosts):
            return FetchResult(200, b'{"results":[]}', {}, url)
        result = self.service.calistir(run_id="run", fetcher=read, now=NOW, topics=list(SORGULAR))
        self.assertTrue(all(row["query"] != SORGULAR[row["topic"]] for row in result["queries"]))

    def test_kapsam_sorgusu_guncellik_filtresiyle_ilgi_sirasini_korur(self):
        calls = []
        def read(url, hosts):
            calls.append(url)
            return FetchResult(200, b'{"results":[]}', {}, url)
        result = self.service.calistir(run_id="focused", fetcher=read, now=NOW, topics=["robotik", "tarim"])
        for url in calls:
            params = parse_qs(urlsplit(url).query)
            self.assertNotIn("sort", params)
            self.assertIn("from_publication_date:", params["filter"][0])
        self.assertEqual(result["queries"][0]["query"], '"robot manipulation"')
        self.assertEqual(result["queries"][1]["query"], 'greenhouse AND irrigation')
        self.assertTrue(all(query["order"] == "relevance" for query in result["queries"]))


if __name__ == "__main__":
    unittest.main()
