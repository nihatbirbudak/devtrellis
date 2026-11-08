"""Sürüm akışı: kapsam eşleme, etiketler, günlük tek istek önbelleği, sahte yanıtlar; gerçek ağ yok."""

import importlib.util
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from radar import surum_akisi
from radar.surum_akisi import (SurumAkisi, SurumAkisiHatasi, ayarlari_dogrula, gece_isi, karsilastir, kapsam_surumu,
                               kayitlari_topla, metinden_kapsam, ornek_ayar, purl_ayristir, surum_ayristir)
from radar.topluluk_depo import ToplulukDeposu

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("surum_akisi_cli", ROOT / "araclar" / "surum-akisi.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)

GUN = 1_800_000_000  # 2027-01-15 UTC

SAHTE_YANITLAR = {
    "https://endoflife.date/api/v1/products": {"result": [{"name": "python"}, {"name": "nodejs"}, {"name": "postgresql"},
                                                          {"name": "ros"}, {"name": "garip"}, {"name": "bos"}]},
    # Gerçek `ros` biçimi (8 Kasım kabul denemesi): kod adlı döngüler, `latest` yok.
    "https://endoflife.date/api/v1/products/ros": {"result": {"name": "ros", "releases": [
        {"name": "noetic", "releaseDate": "2020-05-23", "isEol": False, "eolFrom": "2025-05-31"},
        {"name": "melodic", "releaseDate": "2018-05-23", "isEol": True, "eolFrom": "2023-05-30"},
        {"name": "lunar", "releaseDate": "2017-05-23", "isEol": True, "eolFrom": "2019-05-30"}]}},
    "https://endoflife.date/api/v1/products/garip": {"result": {"name": "garip", "releases": [
        {"name": "stable", "latest": {"name": "latest"}}, {"name": "oldstable", "latest": None}]}},
    "https://endoflife.date/api/v1/products/bos": {"result": {"name": "bos", "releases": []}},
    "https://api.deps.dev/v3/systems/npm/packages/garip-npm": {"versions": [
        {"versionKey": {"version": "latest"}, "isDefault": True}, {"versionKey": {"version": "next"}}]},
    "https://endoflife.date/api/v1/products/python": {"result": {"name": "python", "releases": [
        {"name": "3.13", "releaseDate": "2024-10-07", "isEol": False, "eolFrom": "2029-10-01",
         "latest": {"name": "3.13.2", "date": "2025-02-04"}},
        {"name": "3.12", "releaseDate": "2023-10-02", "isEol": False, "eolFrom": "2028-10-02",
         "latest": {"name": "3.12.9", "date": "2025-02-04"}},
        {"name": "3.8", "releaseDate": "2019-10-14", "isEol": True, "eolFrom": "2024-10-07",
         "latest": {"name": "3.8.20", "date": "2024-09-06"}}]}},
    "https://endoflife.date/api/v1/products/nodejs": {"result": {"name": "nodejs", "releases": [
        {"name": "22", "releaseDate": "2024-04-24", "isEol": False, "eolFrom": "2027-04-30",
         "latest": {"name": "22.14.0", "date": "2025-02-11"}}]}},
    "https://pypi.org/pypi/fastapi/json": {"info": {"version": "0.115.8"},
                                           "releases": {"0.115.8": [{"upload_time_iso_8601": "2025-01-30T12:00:00Z"}]}},
    "https://api.deps.dev/v3/systems/npm/packages/left-pad": {"versions": [
        {"versionKey": {"version": "1.2.0"}, "publishedAt": "2017-01-01T00:00:00Z", "isDefault": False},
        {"versionKey": {"version": "1.3.0"}, "publishedAt": "2018-04-01T00:00:00Z", "isDefault": True}]},
    "https://api.github.com/repos/example/widget/releases/latest": {"tag_name": "v3.1.0", "published_at": "2025-03-01T00:00:00Z"},
}


class SahteAg:
    def __init__(self, yanitlar=None, hata=None):
        self.yanitlar = yanitlar or SAHTE_YANITLAR
        self.hata = hata or {}
        self.istekler = []

    def __call__(self, url, headers):
        self.istekler.append((url, dict(headers)))
        if url in self.hata:
            return self.hata[url], b""
        if url in self.yanitlar:
            return 200, json.dumps(self.yanitlar[url]).encode("utf-8")
        return 404, b""


def paket(title, summary="Bir özet.", tags=()):
    return {"schema": "ufuk-share", "schema_version": 1, "kind": "research", "title": title, "summary": summary,
            "body": "Gövde.", "url": "", "tags": list(tags), "sources": [{"title": "Kaynak", "url": "https://example.org/doc"}],
            "author": "Test Yazar", "license": "CC0-1.0", "created_at": "2026-11-01T00:00:00+00:00",
            "updated_at": "2026-11-01T00:00:00+00:00"}


def v4_kaydi(kimlik, kapsam, icerik_kapsami=None):
    return {"schema": "devtrellis-record", "schema_version": 4, "contract": "k1-v4-r2", "kind": "research_report", "id": kimlik,
            "version": 1, "language": "en", "content": {"title": "v4 " + kimlik[:6], **({"applies_to": icerik_kapsami} if icerik_kapsami else {})},
            "claims": [{"id": "c" * 32, "text": "Claim.", "claim_type": "version_scope", "applies_to": kapsam,
                        "evidence": {"quote": "Quote.", "source_url": "https://docs.example.org/x"},
                        "quote_match": {"state": "exact"}, "claim_review": {"state": "not_reviewed"}, "evidence_state": "current"}],
            "provenance": {"origin": "human", "stages": []}, "rights": {"own_text_license": "CC0-1.0"},
            "publication": {"state": "published", "path": "machine_gate", "published_at": "2026-11-07T00:00:00+00:00"},
            "corrections": {"open": 0}, "link": "https://devtrellis.test/api/community/v4/records/" + kimlik + "/v/1"}


def k1_deposu_kur(path, kayitlar):
    with closing(sqlite3.connect(path)) as baglanti:
        baglanti.executescript("""
            CREATE TABLE v4_submissions (id TEXT PRIMARY KEY, candidate TEXT NOT NULL, note TEXT NOT NULL DEFAULT '');
            CREATE TABLE v4_records (
                id TEXT NOT NULL, version INTEGER NOT NULL, kind TEXT NOT NULL, record TEXT NOT NULL, path TEXT NOT NULL,
                visible INTEGER NOT NULL, published_at INTEGER NOT NULL, removed_at INTEGER, removal TEXT, address_key TEXT,
                PRIMARY KEY(id, version));
        """)
        for kayit, gorunur in kayitlar:
            baglanti.execute("INSERT INTO v4_submissions VALUES(?,?,?)", (kayit["id"], "PRIVATE_CANDIDATE_TEXT", "PRIVATE_NOTE"))
            baglanti.execute("INSERT INTO v4_records VALUES(?,?,?,?,?,?,?,?,?,?)",
                             (kayit["id"], kayit["version"], kayit["kind"], json.dumps(kayit), "machine_gate", int(gorunur),
                              GUN - 100, None if gorunur else GUN - 50, None if gorunur else "withdrawn", None))
        baglanti.commit()


class SurumMetniTest(unittest.TestCase):
    def test_surum_ayristir_ve_kapsam(self):
        self.assertEqual(surum_ayristir("v3.12.1"), (3, 12, 1))
        self.assertEqual(surum_ayristir("22.14.0-rc1"), (22, 14, 0))
        self.assertIsNone(surum_ayristir("latest"))
        self.assertEqual(kapsam_surumu("3.12.x"), (3, 12))
        self.assertEqual(kapsam_surumu(">=3.11,<3.13"), (3, 11))
        self.assertEqual(kapsam_surumu("2.0"), (2, 0))
        self.assertEqual(kapsam_surumu("3.12.*"), (3, 12))
        self.assertIsNone(kapsam_surumu("current"))
        self.assertIsNone(kapsam_surumu("noetic"))
        self.assertEqual(kapsam_surumu("Noetic", {"noetic": "1.15"}), (1, 15))

    def test_karsilastir_referansin_hassasiyetinde(self):
        self.assertEqual(karsilastir((3, 12), (3, 13, 2)), ("minor", False))
        self.assertEqual(karsilastir((3,), (3, 13, 2)), (None, True))
        self.assertEqual(karsilastir((3, 12), (3, 12, 9)), (None, True))
        self.assertEqual(karsilastir((3, 12, 1), (3, 12, 9)), ("patch", False))
        self.assertEqual(karsilastir((2, 0), (3, 1, 0)), ("major", False))
        self.assertEqual(karsilastir((3, 13), (3, 12, 9)), (None, True))

    def test_metinden_kapsam_yalniz_takma_adli_urun(self):
        urunler = {"python": {"aliases": ["Python"]}, "nodejs": {"aliases": ["Node.js"]}, "go": {"aliases": []}}
        self.assertEqual(metinden_kapsam("Python 3.8 ile ölçüm", urunler),
                         [{"product": "python", "versions": "3.8", "version_scheme": "exact", "derived": "text"}])
        self.assertEqual(metinden_kapsam("Python 3 genel yazı", urunler), [])
        self.assertEqual({o["product"]: o["versions"] for o in metinden_kapsam("Node.js 22.4 ve Python 3.12", urunler)},
                         {"python": "3.12", "nodejs": "22.4"})
        # Kabul bulgusu 4: aralık anmaları, ürün anahtarı taraması ve büyük/küçük harf.
        self.assertEqual(metinden_kapsam("Python 3.8+ gerekir", urunler), [])
        self.assertEqual(metinden_kapsam("Python >=3.8 gerekir", urunler), [])
        self.assertEqual(metinden_kapsam("Python 3.12.* denendi", urunler), [])
        self.assertEqual(metinden_kapsam("Python 2.7 ve Python 3.12 karşılaştırması", urunler)[0]["versions"], "3.12")
        self.assertEqual(metinden_kapsam("to go 2.5 km", urunler), [])
        self.assertEqual(metinden_kapsam("python 3.8 küçük harf", urunler), [])
        self.assertEqual(metinden_kapsam("3.8 kat hızlı; Node 20 satır", urunler), [])

    def test_purl_ve_ayar_dogrulama(self):
        self.assertEqual(purl_ayristir("pkg:npm/%40scope/name@1.0"), {"type": "npm", "name": "@scope/name"})
        self.assertEqual(purl_ayristir("pkg:maven/org.example/lib"), {"type": "maven", "name": "org.example:lib"})
        with self.assertRaises(SurumAkisiHatasi):
            purl_ayristir("pkg:unknown/x")
        ayar = ayarlari_dogrula(ornek_ayar())
        self.assertFalse(ayar["enabled"])
        self.assertFalse(ayar["sources"]["github"])
        self.assertEqual(ayar["products"]["ros"]["cycle_names"]["noetic"], "1.15")
        for bozuk in ({"schema": "x"}, {**ornek_ayar(), "extra": 1}, {**ornek_ayar(), "products": {"Bad Name": {}}},
                      {**ornek_ayar(), "v1_reports": {"abc": []}}, {**ornek_ayar(), "sources": {"other": True}},
                      {**ornek_ayar(), "products": {"ros": {"cycle_names": {"noetic": 1}}}},
                      {**ornek_ayar(), "products": {"ros": {"cycle_names": {"Noetic": "1"}}}}):
            with self.assertRaises(SurumAkisiHatasi):
                ayarlari_dogrula(bozuk)


class AkisTest(unittest.TestCase):
    def setUp(self):
        gecici = tempfile.TemporaryDirectory()
        self.addCleanup(gecici.cleanup)
        self.root = Path(gecici.name)
        self.simdi = GUN
        self.topluluk = ToplulukDeposu(self.root / "community.sqlite3", clock=lambda: self.simdi)
        self.yazar = self.topluluk.hesap_ekle("yazar", "Yazar", "contributor", "parola-parola-12")
        self.moderator = self.topluluk.hesap_ekle("mod", "Moderatör", "moderator", "parola-parola-12")
        self.raporlar = {}
        for ad, baslik, ozet in (("eski", "Python 3.8 ile ölçüm", "Python 3.8 üzerinde yapıldı."),
                                 ("eslenmis", "Tip denetimi notları", "Sürüm metinde yok."),
                                 ("bilinmez", "Genel yazı", "Sürüm anmıyor.")):
            kart = self.topluluk.gonder(self.yazar, {"packet": paket(baslik, ozet), "expected_version": 0,
                                                     "idempotency_key": "key-" + ad})["card"]
            self.topluluk.karar(self.moderator, kart["id"], {"decision": "publish", "expected_version": kart["version"]})
            self.raporlar[ad] = kart["id"]
        self.k1 = self.root / "k1.sqlite3"
        k1_deposu_kur(self.k1, [
            (v4_kaydi("a" * 32, [{"product": "fastapi", "versions": "0.100", "version_scheme": "pep440"}]), True),
            (v4_kaydi("b" * 32, [{"product": "widget", "versions": "2.0", "version_scheme": "exact"}]), True),
            (v4_kaydi("c" * 32, [{"product": "left-pad", "versions": "1.3.0", "version_scheme": "semver"}]), True),
            (v4_kaydi("d" * 32, [{"product": "nodejs", "versions": "22", "version_scheme": "semver"}]), True),
            (v4_kaydi("e" * 32, [{"product": "python", "versions": "2.7", "version_scheme": "pep440"}]), False),
        ])
        self.ayar = ayarlari_dogrula({**ornek_ayar(), "enabled": True, "request_interval_seconds": 0,
                                      "v1_reports": {self.raporlar["eslenmis"]: [
                                          {"product": "python", "versions": "3.12", "version_scheme": "pep440"}]}})
        self.ag = SahteAg()
        self.uykular = []

    def akis(self, ayar=None):
        return SurumAkisi(self.root / "feed.sqlite3", ayar or self.ayar, fetcher=self.ag, clock=lambda: self.simdi,
                          sleeper=self.uykular.append)

    def etiket(self, sonuc, ref_onek):
        return next(s for s in sonuc["labels"] if s["ref"].startswith(ref_onek))

    def test_kayitlar_toplanir_v1_ve_v4(self):
        kayitlar = kayitlari_topla(self.topluluk.path, self.ayar, self.k1)
        self.assertEqual(len(kayitlar), 3 + 4)  # görünmeyen v4 kaydı okunmaz
        eski = next(k for k in kayitlar if k["id"] == self.raporlar["eski"])
        self.assertEqual(eski["scope_source"], "text")
        self.assertEqual(eski["applies_to"][0]["versions"], "3.8")
        eslenmis = next(k for k in kayitlar if k["id"] == self.raporlar["eslenmis"])
        self.assertEqual(eslenmis["scope_source"], "config")
        bilinmez = next(k for k in kayitlar if k["id"] == self.raporlar["bilinmez"])
        self.assertIsNone(bilinmez["scope_source"])
        self.assertEqual(bilinmez["applies_to"], [])

    def test_etiketler_tetikleyen_surum_ve_tarihle(self):
        sonuc = self.akis().calistir(kayitlari_topla(self.topluluk.path, self.ayar, self.k1))
        self.assertEqual(sonuc["model_calls"], 0)
        eski = self.etiket(sonuc, "v1:" + self.raporlar["eski"])
        self.assertEqual(eski["label"], "maybe_outdated")
        self.assertEqual(eski["label_tr"], "eskimiş olabilir")
        self.assertEqual(eski["products"][0]["trigger"], "support_ended")
        self.assertEqual(eski["products"][0]["eol_date"], "2024-10-07")
        self.assertEqual(eski["products"][0]["latest_version"], "3.13.2")
        self.assertEqual(eski["products"][0]["latest_date"], "2025-02-04")
        eslenmis = self.etiket(sonuc, "v1:" + self.raporlar["eslenmis"])
        self.assertEqual(eslenmis["label"], "newer_version")
        self.assertEqual(eslenmis["products"][0]["trigger"], "new_minor_version")
        self.assertEqual(eslenmis["products"][0]["source_url"], "https://endoflife.date/api/v1/products/python")
        bilinmez = self.etiket(sonuc, "v1:" + self.raporlar["bilinmez"])
        self.assertEqual(bilinmez["label"], "unknown")
        self.assertEqual(bilinmez["products"][0]["reason"], "no_applies_to")
        fastapi = self.etiket(sonuc, "v4:" + "a" * 32)
        self.assertEqual(fastapi["label"], "newer_version")
        self.assertEqual(fastapi["products"][0]["source"], "pypi")
        self.assertEqual(fastapi["products"][0]["latest_version"], "0.115.8")
        widget = self.etiket(sonuc, "v4:" + "b" * 32)
        self.assertEqual(widget["label"], "unknown")
        self.assertEqual(widget["products"][0]["reason"], "product_not_mapped")
        leftpad = self.etiket(sonuc, "v4:" + "c" * 32)
        self.assertEqual(leftpad["label"], "current")
        self.assertEqual(leftpad["products"][0]["source"], "depsdev")
        nodejs = self.etiket(sonuc, "v4:" + "d" * 32)
        self.assertEqual(nodejs["label"], "current")  # tek parça referans: yalnız büyük sürüm sayılır
        self.assertEqual(nodejs["products"][0]["cycle"], "22")
        self.assertEqual(sonuc["counts"], {"current": 2, "newer_version": 2, "maybe_outdated": 1, "unknown": 2})
        # GitHub ayarda kapalı: hiç istek yok.
        self.assertFalse(any("github.com" in url for url, _ in self.ag.istekler))
        for url, basliklar in self.ag.istekler:
            self.assertTrue(url.startswith("https://"))
            self.assertNotIn("Authorization", basliklar)

    def test_gunde_bir_istek_ve_ertesi_gun_yenilenir(self):
        kayitlar = kayitlari_topla(self.topluluk.path, self.ayar, self.k1)
        akis = self.akis()
        ilk = akis.calistir(kayitlar)
        # ürün listesi, python, nodejs, fastapi, left-pad
        self.assertEqual(ilk["requests"], 5)
        ikinci = self.akis().calistir(kayitlar)
        self.assertEqual(ikinci["requests"], 0)
        self.assertEqual(len(self.ag.istekler), 5)
        self.assertEqual(ikinci["counts"], ilk["counts"])
        self.simdi += 86400
        ucuncu = self.akis().calistir(kayitlar)
        self.assertEqual(ucuncu["requests"], 5)

    def test_kibar_aralik_ve_429_gunluk_tek_deneme(self):
        self.ag = SahteAg(hata={"https://pypi.org/pypi/fastapi/json": 429})
        ayar = ayarlari_dogrula({**ornek_ayar(), "enabled": True, "request_interval_seconds": 2})
        kayitlar = kayitlari_topla(self.topluluk.path, ayar, self.k1)
        sonuc = self.akis(ayar).calistir(kayitlar)
        fastapi = self.etiket(sonuc, "v4:" + "a" * 32)
        self.assertEqual(fastapi["label"], "unknown")
        self.assertEqual(fastapi["products"][0]["reason"], "source_unavailable")
        self.assertEqual(fastapi["products"][0]["source_status"], "rate_limited")
        self.assertEqual(self.uykular, [2] * (sonuc["requests"] - 1))
        tekrar = self.akis(ayar).calistir(kayitlar)
        self.assertEqual(tekrar["requests"], 0)
        self.assertEqual(self.etiket(tekrar, "v4:" + "a" * 32)["products"][0]["source_status"], "rate_limited")

    def test_kod_adli_dongu_turu_cokertmez_ve_eslemeyle_etiketlenir(self):
        """Kabul bulgusu 1: endoflife `ros` (kod adlı döngüler), boş yanıt, deps.dev sayısal olmayan sürümler."""
        k1 = self.root / "k1-garip.sqlite3"
        k1_deposu_kur(k1, [
            (v4_kaydi("1" * 32, [{"product": "ros", "versions": "melodic", "version_scheme": "exact"}]), True),
            (v4_kaydi("2" * 32, [{"product": "garip", "versions": "1.0", "version_scheme": "semver"}]), True),
            (v4_kaydi("3" * 32, [{"product": "bos", "versions": "1.0", "version_scheme": "semver"}]), True),
            (v4_kaydi("4" * 32, [{"product": "garip-npm", "versions": "1.0", "version_scheme": "semver"}]), True),
            (v4_kaydi("5" * 32, [{"product": "left-pad", "versions": "1.3.0", "version_scheme": "semver"}]), True),
        ])
        # Eşlemesiz: ros kod adları sayısal değil → tur çökmez, ürün "bilinmiyor", ötekiler etiketlenir.
        ayar = ayarlari_dogrula({**ornek_ayar(), "enabled": True, "request_interval_seconds": 0,
                                 "products": {"left-pad": {"purl": "pkg:npm/left-pad"}, "garip-npm": {"purl": "pkg:npm/garip-npm"}}})
        kayitlar = kayitlari_topla(self.topluluk.path, ayar, k1)
        sonuc = self.akis(ayar).calistir(kayitlar)
        ros = self.etiket(sonuc, "v4:" + "1" * 32)["products"][0]
        self.assertEqual((ros["label"], ros["reason"], ros["source_status"]), ("unknown", "version_names_not_numeric", "unparsed"))
        self.assertIn("sayısal değil", ros["reason_tr"])
        garip = self.etiket(sonuc, "v4:" + "2" * 32)["products"][0]
        self.assertEqual((garip["reason"], garip["source_status"]), ("version_names_not_numeric", "unparsed"))
        bos = self.etiket(sonuc, "v4:" + "3" * 32)["products"][0]
        self.assertEqual((bos["reason"], bos["source_status"]), ("source_payload_unparsed", "unparsed"))
        npm = self.etiket(sonuc, "v4:" + "4" * 32)["products"][0]
        self.assertEqual((npm["source"], npm["reason"]), ("depsdev", "version_names_not_numeric"))
        self.assertEqual(self.etiket(sonuc, "v4:" + "5" * 32)["label"], "current")
        self.assertEqual(sonuc["adapter_errors"], 0)
        self.assertEqual(len(self.akis(ayar).etiketleri_oku(kind="v4")), 5)
        with closing(sqlite3.connect(self.root / "feed.sqlite3")) as baglanti:
            durumlar = dict(baglanti.execute("SELECT key,status FROM feed_cache WHERE source='endoflife'").fetchall())
        self.assertEqual(durumlar["product:ros"], "unparsed")
        self.assertEqual(durumlar["product:bos"], "unparsed")
        self.assertEqual(durumlar["products"], "ok")
        # Aynı gün: ayrıştırılamayan yanıt için yeniden istek yok, neden korunur.
        istek_sayisi = len(self.ag.istekler)
        tekrar = self.akis(ayar).calistir(kayitlar)
        self.assertEqual(tekrar["requests"], 0)
        self.assertEqual(len(self.ag.istekler), istek_sayisi)
        self.assertEqual(self.etiket(tekrar, "v4:" + "1" * 32)["products"][0]["reason"], "version_names_not_numeric")
        # Kod adı eşlemesiyle (ertesi gün): melodic → 1.14, en yeni noetic → 1.15; melodic döngüsü destek sonunu geçmiş.
        self.simdi += 86400
        esli = ayarlari_dogrula({**ornek_ayar(), "enabled": True, "request_interval_seconds": 0})
        kayitlar = kayitlari_topla(self.topluluk.path, esli, k1)
        sonuc = self.akis(esli).calistir(kayitlar)
        ros = self.etiket(sonuc, "v4:" + "1" * 32)["products"][0]
        self.assertEqual(ros["label"], "maybe_outdated")
        self.assertEqual((ros["reference_version"], ros["latest_version"], ros["latest_cycle"]), ("1.14", "1.15", "noetic"))
        self.assertEqual((ros["cycle"], ros["eol_date"], ros["trigger"]), ("melodic", "2023-05-30", "support_ended"))

    def test_tek_urun_hatasi_oteki_kayitlari_etkilemez(self):
        akis = self.akis()

        def patlayan(ad):
            raise RuntimeError("PRIVATE_ERROR_DETAIL")

        akis.pypi = patlayan
        sonuc = akis.calistir(kayitlari_topla(self.topluluk.path, self.ayar, self.k1))
        fastapi = self.etiket(sonuc, "v4:" + "a" * 32)["products"][0]
        self.assertEqual((fastapi["label"], fastapi["reason"], fastapi["error_type"]), ("unknown", "adapter_error", "RuntimeError"))
        self.assertNotIn("PRIVATE_ERROR_DETAIL", json.dumps(sonuc))
        self.assertEqual(sonuc["adapter_errors"], 1)
        self.assertEqual(self.etiket(sonuc, "v1:" + self.raporlar["eski"])["label"], "maybe_outdated")
        self.assertEqual(self.etiket(sonuc, "v4:" + "c" * 32)["label"], "current")
        self.assertEqual(len(akis.etiketleri_oku()), 7)

    def test_eslenmemis_pep440_pypi_de_aranmaz(self):
        k1 = self.root / "k1-pep.sqlite3"
        k1_deposu_kur(k1, [(v4_kaydi("7" * 32, [{"product": "requests", "versions": "2.31", "version_scheme": "pep440"}]), True)])
        sonuc = self.akis().calistir(kayitlari_topla(self.topluluk.path, self.ayar, k1))
        urun = self.etiket(sonuc, "v4:" + "7" * 32)["products"][0]
        self.assertEqual((urun["label"], urun["reason"]), ("unknown", "product_not_mapped"))
        self.assertFalse(any("pypi.org/pypi/requests" in url for url, _ in self.ag.istekler))

    def test_github_yalniz_acik_eslemede(self):
        ayar = ayarlari_dogrula({**ornek_ayar(), "enabled": True, "request_interval_seconds": 0,
                                 "sources": {"github": True, "endoflife": False, "depsdev": False, "pypi": False},
                                 "products": {"widget": {"github": "example/widget"}}})
        sonuc = self.akis(ayar).calistir(kayitlari_topla(self.topluluk.path, ayar, self.k1))
        widget = self.etiket(sonuc, "v4:" + "b" * 32)
        self.assertEqual(widget["label"], "maybe_outdated")
        self.assertEqual(widget["products"][0]["trigger"], "new_major_version")
        self.assertEqual(widget["products"][0]["latest_version"], "3.1.0")
        self.assertEqual([u for u, _ in self.ag.istekler], ["https://api.github.com/repos/example/widget/releases/latest"])
        self.assertEqual(self.ag.istekler[0][1]["Accept"], "application/vnd.github+json")

    def test_etiketler_okunur_ve_kayit_gizlenmez(self):
        akis = self.akis()
        akis.calistir(kayitlari_topla(self.topluluk.path, self.ayar, self.k1))
        okunan = akis.etiketleri_oku("v1", self.raporlar["eski"])
        self.assertEqual(len(okunan), 1)
        self.assertEqual(okunan[0]["label_tr"], "eskimiş olabilir")
        self.assertEqual(okunan[0]["detail"]["latest_version"], "3.13.2")
        self.assertEqual(len(akis.etiketleri_oku()), 7 + 0)
        with closing(sqlite3.connect(self.topluluk.path)) as baglanti:
            self.assertEqual(baglanti.execute("SELECT COUNT(*) FROM community_entries WHERE published_revision IS NOT NULL").fetchone()[0], 3)

    def test_gece_isi_kapaliyken_hic_bir_sey_yapmaz(self):
        ayar_yolu = self.root / "ayar.json"
        ayar_yolu.write_text(json.dumps(ornek_ayar()), encoding="utf-8")
        sonuc = gece_isi(ayar_yolu, self.root / "feed.sqlite3", self.root / "yok.sqlite3", fetcher=self.ag)
        self.assertEqual(sonuc["status"], "disabled")
        self.assertEqual(self.ag.istekler, [])
        self.assertFalse((self.root / "feed.sqlite3").exists())
        acik = {**ornek_ayar(), "enabled": True, "request_interval_seconds": 0}
        ayar_yolu.write_text(json.dumps(acik), encoding="utf-8")
        sonuc = gece_isi(ayar_yolu, self.root / "feed.sqlite3", self.topluluk.path, k1_db=self.k1, fetcher=self.ag,
                         clock=lambda: self.simdi, sleeper=self.uykular.append)
        self.assertEqual(sonuc["status"], "ok")
        self.assertEqual(sonuc["records"], 7)

    def test_varsayilan_getirici_yalniz_izinli_alanlar(self):
        with self.assertRaises(SurumAkisiHatasi):
            surum_akisi.varsayilan_getirici("http://endoflife.date/api/v1/products", {})
        with self.assertRaises(SurumAkisiHatasi):
            surum_akisi.varsayilan_getirici("https://example.com/x", {})

    def test_komut_satiri(self):
        ayar_yolu = self.root / "ayar.json"
        ayar_yolu.write_text(json.dumps(ornek_ayar()), encoding="utf-8")
        cikti = self.root / "etiketler.json"
        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["run", "--config", str(ayar_yolu), "--db", str(self.root / "cli.sqlite3"),
                                       "--community", str(self.topluluk.path)]), 2)
            self.assertFalse((self.root / "cli.sqlite3").exists())
            self.assertEqual(cli.main(["example-config"]), 0)
            ayar_yolu.write_text(json.dumps({**ornek_ayar(), "enabled": True, "sources": {
                "endoflife": False, "depsdev": False, "pypi": False, "github": False}}), encoding="utf-8")
            self.assertEqual(cli.main(["run", "--config", str(ayar_yolu), "--db", str(self.root / "cli.sqlite3"),
                                       "--community", str(self.topluluk.path), "--k1-db", str(self.k1), "--out", str(cikti)]), 0)
            self.assertEqual(cli.main(["run", "--config", str(ayar_yolu), "--db", str(self.root / "cli.sqlite3"),
                                       "--community", str(self.topluluk.path), "--out", str(cikti)]), 1)
            self.assertEqual(cli.main(["labels", "--db", str(self.root / "cli.sqlite3"), "--kind", "v4"]), 0)
            with patch.object(cli, "gece_isi", side_effect=RuntimeError("PRIVATE_ERROR_DETAIL")), \
                    redirect_stderr(io.StringIO()) as hata:
                self.assertEqual(cli.main(["run", "--config", str(ayar_yolu), "--db", str(self.root / "cli2.sqlite3"),
                                           "--community", str(self.topluluk.path)]), 1)
            self.assertIn("RuntimeError", hata.getvalue())
            self.assertNotIn("PRIVATE_ERROR_DETAIL", hata.getvalue())
            self.assertNotIn("Traceback", hata.getvalue())
        yazilan = json.loads(cikti.read_text(encoding="utf-8"))
        self.assertEqual(yazilan["requests"], 0)
        self.assertEqual(yazilan["counts"]["unknown"], 7)
        self.assertIn('"status": "disabled"', stdout.getvalue())
        self.assertEqual(stdout.getvalue().count('"ref": "v4:'), 4)  # labels --kind v4: dört görünür v4 kaydı


if __name__ == "__main__":
    unittest.main()
