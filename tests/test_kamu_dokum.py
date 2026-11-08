"""Kamu dökümü: yalnız yayımlanmış kayıtlar, gizlilik denetimi, tarihli dosya, geri okuma ve sayım, isteğe bağlı git."""

import hashlib
import importlib.util
import io
import json
import sqlite3
import subprocess
import tempfile
import unittest
import uuid
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path

from radar.kamu_dokum import (DokumHatasi, LISANS_NOTU, dokum_olustur, dokum_yaz, dokumu_dogrula, dokumu_oku, dokumu_yukle,
                              gece_dokumu)
from radar.topluluk_depo import ToplulukDeposu
from radar.topluluk_talepleri import TalepDeposu

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("kamu_dokum_cli", ROOT / "araclar" / "kamu-dokum.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)

SIMDI = 1_800_000_000
GIZLI = {"aday": "PRIVATE_CANDIDATE_TEXT_7f3a", "not": "PRIVATE_MODERATOR_NOTE_9c1b", "talep": "PRIVATE_REQUEST_BODY_2d4e",
         "eposta": "private.person@example.net", "bekleyen": "PENDING_UNPUBLISHED_TITLE_5e6f", "gizli": "HIDDEN_AFTER_REMOVAL_1a2b",
         "parola": "cok-gizli-parola-123456", "olay": "PRIVATE_EVENT_NOTE_3b3c", "ip": "203.0.113.77"}


def paket(title, kind="research", **ekstra):
    temel = {"schema": "ufuk-share", "schema_version": 1, "kind": kind, "title": title, "summary": "Özet.", "body": "Gövde.",
             "url": "https://example.org/project" if kind == "project" else "", "tags": ["etiket"],
             "sources": [{"title": "Kaynak", "url": "https://example.org/doc"}] if kind == "research" else [],
             "author": "Test Yazar", "license": "CC-BY-4.0", "created_at": "2026-11-01T00:00:00+00:00",
             "updated_at": "2026-11-01T00:00:00+00:00"}
    return {**temel, **ekstra}


def v4_kaydi(kimlik, surum=1):
    return {"schema": "devtrellis-record", "schema_version": 4, "contract": "k1-v4-r2", "kind": "source_card", "id": kimlik,
            "version": surum, "language": "en", "content": {"applies_to": [{"product": "widget", "versions": "2.0", "version_scheme": "exact"}]},
            "claims": [{"id": "c" * 32, "text": "Widget 2.0 stores projects locally.", "claim_type": "source_statement",
                        "applies_to": [{"product": "widget", "versions": "2.0", "version_scheme": "exact"}],
                        "evidence": {"quote": "Widget 2.0 stores projects locally.", "source_url": "https://docs.example.org/widget/2.0/reference.html",
                                     "address_key": "a" * 64, "quote_hash": "b" * 64, "checked_on": "2026-11-07"},
                        "quote_match": {"state": "exact"}, "claim_review": {"state": "not_reviewed"}, "evidence_state": "current"}],
            "provenance": {"origin": "human", "stages": []}, "rights": {"own_text_license": "CC0-1.0", "quotes": {"basis": "source_linked_short_quote", "max_chars": 300}},
            "publication": {"state": "published", "path": "machine_gate", "published_at": "2026-11-07T00:00:00+00:00", "label": {"tr": "İnsan incelemedi", "en": "Not human reviewed"}},
            "corrections": {"open": 0, "latest_public_version": None, "public_note": None},
            "link": f"https://devtrellis.test/api/community/v4/records/{kimlik}/v/{surum}"}


class DokumTest(unittest.TestCase):
    def setUp(self):
        gecici = tempfile.TemporaryDirectory()
        self.addCleanup(gecici.cleanup)
        self.root = Path(gecici.name)
        self.saat = SIMDI
        self.topluluk = ToplulukDeposu(self.root / "community.sqlite3", clock=lambda: self.saat)
        yazar = self.topluluk.hesap_ekle("yazar", "Yazar", "contributor", GIZLI["parola"])
        moderator = self.topluluk.hesap_ekle("mod", "Moderatör", "moderator", "parola-parola-12")
        self.topluluk.oturum_ac(yazar["id"])
        # Yayımlanmış araştırma: iki sürüm, ikisi de görünür. Yayın kararı gönderimden 1000 sn sonra.
        kart = self.gonder(yazar, paket("Yayındaki araştırma"), "key-1")
        self.saat = SIMDI + 1000
        self.topluluk.karar(moderator, kart["id"], {"decision": "publish", "expected_version": kart["version"]})
        self.saat = SIMDI
        kart = self.topluluk.gonder(yazar, {"packet": paket("Yayındaki araştırma, güncellenmiş"), "expected_version": kart["version"] + 1,
                                            "idempotency_key": "dump-key-1b"}, kart["id"])["card"]
        self.topluluk.karar(moderator, kart["id"], {"decision": "publish", "expected_version": kart["version"]})
        self.yayinda = kart["id"]
        # Yayımlanmış proje kartı.
        proje = self.gonder(yazar, paket("Kamu projesi", kind="project"), "key-2")
        self.topluluk.karar(moderator, proje["id"], {"decision": "publish", "expected_version": proje["version"]})
        # İncelemede bekleyen (yayımlanmamış) ve moderatör notuyla reddedilen.
        self.gonder(yazar, paket(GIZLI["bekleyen"]), "key-3")
        ret = self.gonder(yazar, paket("Reddedilen araştırma"), "key-4")
        self.topluluk.karar(moderator, ret["id"], {"decision": "reject", "expected_version": ret["version"], "note": GIZLI["not"]})
        # Yayımlanıp sonra kaldırılan.
        kaldirilan = self.gonder(yazar, paket(GIZLI["gizli"]), "key-5")
        self.topluluk.karar(moderator, kaldirilan["id"], {"decision": "publish", "expected_version": kaldirilan["version"]})
        self.topluluk.yayindan_kaldir(moderator, kaldirilan["id"], {"expected_version": kaldirilan["version"] + 1, "note": GIZLI["not"]})
        # Hesapsız kutu gönderimi (yönetim anahtarı özeti) — yayımlanmaz.
        self.topluluk.inbox_gonder("A" * 43, {"packet": paket(GIZLI["aday"]), "consent": True, "idempotency_key": "inbox-key-1"})
        # Özel talep kutusu aynı veritabanında.
        TalepDeposu(self.topluluk.path, clock=lambda: SIMDI).gonder({
            "kind": "support", "category": "privacy", "subject": "Konu", "message": GIZLI["talep"], "name": "Ad",
            "email": GIZLI["eposta"], "language": "tr", "consent": True, "idempotency_key": str(uuid.uuid4())})
        self.k1 = self.root / "k1.sqlite3"
        self.k1_kur()

    def gonder(self, user, packet, key):
        return self.topluluk.gonder(user, {"packet": packet, "expected_version": 0, "idempotency_key": "dump-" + key})["card"]

    def k1_kur(self):
        with closing(sqlite3.connect(self.k1)) as baglanti:
            baglanti.executescript("""
                CREATE TABLE v4_submissions (id TEXT PRIMARY KEY, capability_hash TEXT NOT NULL, candidate TEXT NOT NULL,
                    status TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', consent_at INTEGER NOT NULL);
                CREATE TABLE v4_records (id TEXT NOT NULL, version INTEGER NOT NULL, kind TEXT NOT NULL, record TEXT NOT NULL,
                    path TEXT NOT NULL, visible INTEGER NOT NULL, published_at INTEGER NOT NULL, removed_at INTEGER,
                    removal TEXT, address_key TEXT, PRIMARY KEY(id, version));
                CREATE TABLE v4_events (id INTEGER PRIMARY KEY, submission_id TEXT, actor TEXT NOT NULL, event TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL);
                CREATE TABLE v4_state (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            for kimlik, gorunur, kaldirma in (("1" * 32, 1, None), ("2" * 32, 0, "withdrawn"), ("3" * 32, 0, "removed")):
                baglanti.execute("INSERT INTO v4_submissions VALUES(?,?,?,?,?,?)",
                                 (kimlik, "f" * 64, json.dumps({"title": GIZLI["aday"]}), "published", GIZLI["not"], SIMDI))
                kayit = v4_kaydi(kimlik) if kimlik != "3" * 32 else {}
                baglanti.execute("INSERT INTO v4_records VALUES(?,?,?,?,?,?,?,?,?,?)",
                                 (kimlik, 1, "source_card", json.dumps(kayit), "machine_gate", gorunur, SIMDI - 100,
                                  None if gorunur else SIMDI - 10, kaldirma, "a" * 64))
                baglanti.execute("INSERT INTO v4_events(submission_id,actor,event,note,created_at) VALUES(?,?,?,?,?)",
                                 (kimlik, "moderator", "decision", GIZLI["olay"], SIMDI))
            baglanti.execute("INSERT INTO v4_submissions VALUES(?,?,?,?,?,?)",
                             ("9" * 32, "e" * 64, json.dumps({"title": GIZLI["aday"], "ip": GIZLI["ip"]}), "queued", "", SIMDI))
            baglanti.commit()

    def test_dokum_yalniz_yayimlanmis_kayitlar_ve_gizlilik(self):
        dokum = dokum_olustur(community_db=self.topluluk.path, k1_db=self.k1, public_url="https://devtrellis.test/", now=SIMDI)
        self.assertEqual(dokum["counts"], {"v1_records": 3, "v4_records": 1, "v4_tombstones": 2})
        basliklar = [k["packet"]["title"] for k in dokum["records"]["v1"]]
        self.assertEqual(sorted(basliklar), ["Kamu projesi", "Yayındaki araştırma", "Yayındaki araştırma, güncellenmiş"])
        surumler = [k["version"] for k in dokum["records"]["v1"] if k["id"] == self.yayinda]
        self.assertEqual(surumler, [1, 3])  # yayın sürüm numaraları kartın sürüm sayacını izler (1, sonra 3)
        ilk = next(k for k in dokum["records"]["v1"] if k["id"] == self.yayinda and k["version"] == 1)
        self.assertEqual(ilk["submitted_at"], "2027-01-15T08:00:00+00:00")
        self.assertEqual(ilk["published_at"], "2027-01-15T08:16:40+00:00")  # moderatör kararının zamanı, gönderim değil
        self.assertEqual(dokum["records"]["v1"][0]["link"], f"https://devtrellis.test/r/{dokum['records']['v1'][0]['id']}/v/{dokum['records']['v1'][0]['version']}")
        self.assertEqual(dokum["records"]["v4"][0]["id"], "1" * 32)
        self.assertEqual([iz["state"] for iz in dokum["tombstones"]["v4"]], ["withdrawn", "removed"])
        self.assertEqual(dokum["tombstones"]["v4"][0]["contract"], "k1-v4-r2")
        self.assertIsNone(dokum["tombstones"]["v4"][1]["contract"])
        self.assertEqual(dokum["site"]["sitemap"], "https://devtrellis.test/sitemap.xml")
        self.assertEqual(dokum["site"]["llms_txt"], "https://devtrellis.test/llms.txt")
        self.assertEqual(dokum["license"], LISANS_NOTU)
        self.assertIn("CC0-1.0", dokum["license"]["dump_structure"])
        metin = json.dumps(dokum, ensure_ascii=False)
        for ad, deger in GIZLI.items():
            self.assertNotIn(deger, metin, ad)
        for anahtar in ("password_hash", "token_hash", "capability_hash", "moderation_note", "consent_at", "candidate",
                        "key_hash", "receipt_hash", "engagement", "owner", "username"):
            self.assertNotIn('"' + anahtar + '"', metin, anahtar)
        with closing(sqlite3.connect(self.topluluk.path)) as baglanti:
            self.assertEqual(baglanti.execute("SELECT COUNT(*) FROM community_sessions").fetchone()[0], 1)
            self.assertEqual(baglanti.execute("SELECT COUNT(*) FROM community_requests").fetchone()[0], 1)

    def test_bilinmeyen_alan_dokumu_durdurur(self):
        with closing(sqlite3.connect(self.k1)) as baglanti:
            kayit = {**v4_kaydi("4" * 32), "moderator_note": GIZLI["not"]}
            baglanti.execute("INSERT INTO v4_records VALUES(?,?,?,?,?,?,?,?,?,?)",
                             ("4" * 32, 1, "source_card", json.dumps(kayit), "moderator", 1, SIMDI, None, None, None))
            baglanti.commit()
        with self.assertRaises(DokumHatasi):
            dokum_olustur(community_db=self.topluluk.path, k1_db=self.k1, public_url="https://devtrellis.test", now=SIMDI)
        dokum = dokum_olustur(community_db=self.topluluk.path, public_url="https://devtrellis.test", now=SIMDI)
        dokum["records"]["v1"][0]["owner"] = "x"
        with self.assertRaises(DokumHatasi):
            dokumu_dogrula(dokum)
        with self.assertRaises(DokumHatasi):
            dokum_olustur(community_db=self.topluluk.path, public_url="http://devtrellis.test", now=SIMDI)

    def test_ic_ice_bilinmeyen_alan_dokumu_durdurur(self):
        """Kabul bulgusu 2: iç içe alanlar da kapalı; nöbetçi hiçbir çıktıya ulaşmaz."""
        ic_ice = (
            lambda k: k["publication"].__setitem__("moderator_private_note", GIZLI["not"]),
            lambda k: k["content"].__setitem__("reviewer", GIZLI["eposta"]),
            lambda k: k["claims"][0]["evidence"].__setitem__("fetched_body", GIZLI["aday"]),
            lambda k: k["claims"][0].__setitem__("internal_score", 3),
            lambda k: k["rights"]["quotes"].__setitem__("note", GIZLI["not"]),
            lambda k: k["corrections"].__setitem__("moderator", GIZLI["not"]),
            lambda k: k["provenance"].__setitem__("session_id", GIZLI["ip"]),
            lambda k: k["content"]["applies_to"][0].__setitem__("internal", 1),
            lambda k: k["publication"]["label"].__setitem__("de", "x"),
        )
        for sira, boz in enumerate(ic_ice):
            kayit = v4_kaydi(f"{sira:02d}" * 16)
            boz(kayit)
            with closing(sqlite3.connect(self.k1)) as baglanti:
                baglanti.execute("INSERT INTO v4_records VALUES(?,?,?,?,?,?,?,?,?,?)",
                                 (kayit["id"], 1, "source_card", json.dumps(kayit), "machine_gate", 1, SIMDI, None, None, None))
                baglanti.commit()
            with self.assertRaises(DokumHatasi, msg=str(sira)) as hata:
                dokum_olustur(community_db=self.topluluk.path, k1_db=self.k1, public_url="https://devtrellis.test", now=SIMDI)
            for deger in GIZLI.values():
                self.assertNotIn(deger, str(hata.exception))
            with closing(sqlite3.connect(self.k1)) as baglanti:
                baglanti.execute("DELETE FROM v4_records WHERE id=?", (kayit["id"],))
                baglanti.commit()
        # v1 paketinin iç nesneleri: kaynak, köken, gelişim.
        for boz in (lambda p: p["sources"][0].__setitem__("reviewer_email", GIZLI["eposta"]),
                    lambda p: p.__setitem__("provenance", {"model": "m", "provider": "claude", "note": GIZLI["not"]}),
                    lambda p: p.__setitem__("lineage", {"parents": [{"research_id": "a" * 32, "version": 1, "owner": GIZLI["eposta"]}],
                                                        "change_summary": "x"})):
            with closing(sqlite3.connect(self.topluluk.path)) as baglanti:
                satir = baglanti.execute("SELECT packet FROM community_revisions WHERE entry_id=? AND version=1", (self.yayinda,)).fetchone()
                paket = json.loads(satir[0])
                boz(paket)
                baglanti.execute("UPDATE community_revisions SET packet=? WHERE entry_id=? AND version=1", (json.dumps(paket), self.yayinda))
                baglanti.commit()
            with self.assertRaises(DokumHatasi):
                dokum_olustur(community_db=self.topluluk.path, public_url="https://devtrellis.test", now=SIMDI)
            with closing(sqlite3.connect(self.topluluk.path)) as baglanti:
                baglanti.execute("UPDATE community_revisions SET packet=? WHERE entry_id=? AND version=1", (satir[0], self.yayinda))
                baglanti.commit()
        dokum = dokum_olustur(community_db=self.topluluk.path, k1_db=self.k1, public_url="https://devtrellis.test", now=SIMDI)
        self.assertEqual(dokum["counts"]["v4_records"], 1)

    def test_tarihli_dosya_manifest_ve_geri_okuma(self):
        hedef = self.root / "dump"
        sonuc = gece_dokumu(community_db=self.topluluk.path, k1_db=self.k1, public_url="https://devtrellis.test",
                            target_dir=hedef, now=SIMDI)
        dosya = Path(sonuc["file"])
        self.assertEqual(dosya.name, "devtrellis-public-dump-v1-20270115.json")
        self.assertEqual(Path(sonuc["manifest"]).name, "devtrellis-public-dump-v1-20270115.manifest.json")
        manifest = json.loads(Path(sonuc["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["sha256"], hashlib.sha256(dosya.read_bytes()).hexdigest())
        self.assertEqual(manifest["counts"], {"v1_records": 3, "v4_records": 1, "v4_tombstones": 2})
        self.assertEqual(manifest["license"], "CC0-1.0")
        self.assertEqual(sonuc["model_calls"], 0)
        with self.assertRaises(DokumHatasi):
            gece_dokumu(community_db=self.topluluk.path, public_url="https://devtrellis.test", target_dir=hedef, now=SIMDI)
        dokum, sayilar = dokumu_oku(dosya)
        self.assertEqual(sayilar["v1_records"], 3)
        yeni_db = self.root / "mirror" / "library.sqlite3"
        yuklenen = dokumu_yukle(dosya, yeni_db)
        self.assertEqual(yuklenen["records_loaded"], 4)
        self.assertEqual(yuklenen["v4_tombstones"], 2)
        with closing(sqlite3.connect(yeni_db)) as baglanti:
            self.assertEqual(baglanti.execute("SELECT COUNT(*) FROM public_records WHERE schema='devtrellis-record'").fetchone()[0], 1)
            self.assertEqual(baglanti.execute("SELECT COUNT(*) FROM public_records WHERE kind='project'").fetchone()[0], 1)
            satir = baglanti.execute("SELECT record FROM public_records WHERE id=? AND version=3", (self.yayinda,)).fetchone()
            self.assertEqual(json.loads(satir[0])["packet"]["title"], "Yayındaki araştırma, güncellenmiş")
            self.assertEqual(baglanti.execute("SELECT value FROM public_dump_meta WHERE name='public_url'").fetchone()[0], "https://devtrellis.test")
        with self.assertRaises(DokumHatasi):
            dokumu_yukle(dosya, yeni_db)
        # Manifest özeti bozulursa geri okuma durur.
        orijinal = dosya.read_bytes()
        dosya.write_bytes(orijinal + b"\n")
        with self.assertRaises(DokumHatasi):
            dokumu_oku(dosya)
        # Manifest yoksa döküm kabul edilmez (bulgu 6): inspect ve load hata verir.
        dosya.write_bytes(orijinal)
        self.assertEqual(dokumu_oku(dosya)[1]["v1_records"], 3)
        Path(sonuc["manifest"]).rename(self.root / "yedek.manifest.json")
        with self.assertRaises(DokumHatasi):
            dokumu_oku(dosya)
        with self.assertRaises(DokumHatasi):
            dokumu_yukle(dosya, self.root / "mirror2.sqlite3")
        self.assertFalse((self.root / "mirror2.sqlite3").exists())

    def test_istege_bagli_git_commit_yerel_kalir(self):
        hedef = self.root / "repo"
        hedef.mkdir()
        subprocess.run(["git", "init", "-q", str(hedef)], check=True)
        sonuc = gece_dokumu(community_db=self.topluluk.path, public_url="https://devtrellis.test", target_dir=hedef,
                            now=SIMDI, git_commit=True)
        self.assertEqual(len(sonuc["commit"]), 40)
        listelenen = subprocess.run(["git", "-C", str(hedef), "show", "--stat", "--format=%s", "HEAD"], capture_output=True,
                                    text=True, check=True).stdout
        self.assertIn("Public dump 2027-01-15: 3 v1, 0 v4 records", listelenen)
        self.assertIn("devtrellis-public-dump-v1-20270115.json", listelenen)
        self.assertEqual(subprocess.run(["git", "-C", str(hedef), "remote"], capture_output=True, text=True).stdout, "")
        with self.assertRaises(DokumHatasi):
            gece_dokumu(community_db=self.topluluk.path, public_url="https://devtrellis.test", target_dir=self.root / "plain",
                        now=SIMDI, git_commit=True)
        # Bulgu 7: başka bir deponun alt dizini reddedilir; üst depoya commit girmez, dosyalar yerinde kalır.
        alt = hedef / "dumps-sub"
        with self.assertRaises(DokumHatasi):
            gece_dokumu(community_db=self.topluluk.path, public_url="https://devtrellis.test", target_dir=alt,
                        now=SIMDI + 86400, git_commit=True)
        self.assertEqual(len(list(alt.iterdir())), 2)
        self.assertEqual(subprocess.run(["git", "-C", str(hedef), "rev-list", "--count", "HEAD"], capture_output=True,
                                        text=True, check=True).stdout.strip(), "1")
        self.assertIn("dumps-sub/", subprocess.run(["git", "-C", str(hedef), "status", "--porcelain"], capture_output=True,
                                                   text=True, check=True).stdout)

    def test_komut_satiri(self):
        hedef = self.root / "cli-dump"
        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(cli.main(["export", "--community", str(self.topluluk.path), "--k1-db", str(self.k1),
                                       "--target", str(hedef), "--public-url", "https://devtrellis.test"]), 0)
            dosyalar = sorted(p.name for p in hedef.iterdir())
            self.assertEqual(len(dosyalar), 2)
            dokum = next(p for p in hedef.iterdir() if not p.name.endswith(".manifest.json"))
            self.assertEqual(cli.main(["inspect", str(dokum)]), 0)
            self.assertEqual(cli.main(["load", str(dokum), "--db", str(self.root / "cli-mirror.sqlite3")]), 0)
            self.assertEqual(cli.main(["load", str(dokum), "--db", str(self.root / "cli-mirror.sqlite3")]), 1)
            self.assertEqual(cli.main(["inspect", str(self.root / "yok.json")]), 1)
        satirlar = [json.loads(s) for s in stdout.getvalue().splitlines() if s.strip()]
        self.assertEqual(satirlar[0]["counts"], {"v1_records": 3, "v4_records": 1, "v4_tombstones": 2})
        self.assertEqual(satirlar[2]["records_loaded"], 4)
        for deger in GIZLI.values():
            self.assertNotIn(deger, stdout.getvalue() + stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
