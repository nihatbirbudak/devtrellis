"""Otomatik yedeğin tutarlılığını, hata korumasını ve tek işçiyle çalışmasını doğrular."""

import shutil
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from radar.bakim import Bakim, YEDEK_ARALIGI
from radar.depo import Depo


class BakimKabulTestleri(unittest.TestCase):
    def setUp(self):
        self.gecici = tempfile.TemporaryDirectory()
        self.addCleanup(self.gecici.cleanup)
        self.kok = Path(self.gecici.name)
        self.kaynaklar = [{"id": "deneme", "name": "Deneme kaynağı", "topic": "ai",
                          "url": "https://example.org/", "kind": "rss", "interval_hours": 24,
                          "benefit": "Araştırma için kaynak.", "enabled": True}]
        self.depo = Depo(self.kok / "radar.sqlite3", self.kaynaklar)
        self.depo.kaydet("deneme", [{"external_id": "haber-1", "url": "https://example.org/haber-1",
                                    "title": "Doğrulanmış kaynak başlığı", "excerpt": "İlk açıklama.",
                                    "language": "tr", "evidence_type": "birincil_duyuru"}])
        self.item_id = self.depo.gorunum()["items"][0]["id"]
        self.depo.geri_bildirim(self.item_id, "yararli")
        self.depo.arastirma_sec(self.item_id)
        self.bakim = Bakim(self.depo)
        self.addCleanup(self.bakim.kapan)

    def test_yedek_yokken_durum_ve_indirme_aciktir(self):
        state = self.bakim.gorunum()
        self.assertFalse(state["running"])
        self.assertFalse(state["available"])
        self.assertIsNone(state["last_success_at"])
        self.assertEqual(state["filename"], "radar-son.sqlite3")
        with self.assertRaises(FileNotFoundError):
            self.bakim.dosya()

    def test_ayni_klasordeki_deneme_deposu_asil_yedegi_degistirmez(self):
        self.assertTrue(self.bakim.yedekle())
        real_backup = self.bakim.dosya()
        before = real_backup.read_bytes()
        trial = Depo(self.kok / "qa.sqlite3", self.kaynaklar)
        trial_backup = Bakim(trial)
        self.assertTrue(trial_backup.yedekle())
        self.assertEqual(real_backup.parent.resolve(), (self.kok / "yedekler").resolve())
        self.assertEqual(trial_backup.dosya().parent.resolve(), (self.kok / "qa-yedekler").resolve())
        self.assertNotEqual(trial_backup.dosya(), real_backup)
        self.assertEqual(real_backup.read_bytes(), before)
        restored = Depo(trial_backup.dosya(), self.kaynaklar)
        self.assertEqual(restored.gorunum()["stats"]["total"], 0)

    def test_yedek_tum_verileri_korur_ve_bagimsiz_depo_olarak_acilir(self):
        before = self.depo.disa_aktar()
        self.assertTrue(self.bakim.yedekle())
        backup = self.bakim.dosya()
        restored_path = self.kok / "geri-yuklenen.sqlite3"
        shutil.copyfile(backup, restored_path)
        restored = Depo(restored_path, self.kaynaklar).disa_aktar()
        for table in ("items", "sources", "source_entries", "revisions", "feedback_events",
                      "jobs", "audit", "editorial", "research_notes"):
            with self.subTest(table=table):
                self.assertEqual(restored.get(table), before.get(table))
        connection = sqlite3.connect(backup)
        try:
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchall(), [("ok",)])
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        finally:
            connection.close()
        self.assertEqual(len(list(backup.parent.iterdir())), 2)
        state = self.bakim.gorunum()
        self.assertTrue(state["available"])
        self.assertIsNone(state["last_error"])
        expected_time = datetime.fromtimestamp(backup.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        self.assertEqual(state["last_success_at"], expected_time)
        self.assertEqual(Bakim(self.depo).gorunum()["last_success_at"], expected_time)

    def test_acik_yazma_islemi_yedegi_yarim_degistirmez(self):
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE items SET title=? WHERE id=?", ("Henüz kaydedilmedi", self.item_id))
            self.assertTrue(self.bakim.yedekle())
        backup_connection = sqlite3.connect(self.bakim.dosya())
        try:
            actual = backup_connection.execute("SELECT title FROM items WHERE id=?", (self.item_id,)).fetchone()[0]
        finally:
            backup_connection.close()
        self.assertEqual(actual, "Doğrulanmış kaynak başlığı")
        self.assertEqual(self.depo.gorunum()["items"][0]["title"], "Henüz kaydedilmedi")

    def test_yazma_hatasi_onceki_yedegi_korur_ve_sonraki_deneme_duzelir(self):
        self.bakim.yedekle()
        previous = self.bakim.dosya().read_bytes()
        with patch.object(self.depo, "yedekle", side_effect=OSError("Disk dolu.")):
            with self.assertRaises(OSError):
                self.bakim.yedekle()
        self.assertEqual(self.bakim.dosya().read_bytes(), previous)
        self.assertFalse(self.bakim.gorunum()["running"])
        self.assertIn("Önceki yedek korundu", self.bakim.gorunum()["last_error"])
        self.assertEqual(len(list(self.bakim.dosya().parent.iterdir())), 2)
        self.assertTrue(self.bakim.yedekle())
        self.assertIsNone(self.bakim.gorunum()["last_error"])

    def test_bozuk_yedek_dogrulamadan_gecmez_ve_eski_dosya_kalir(self):
        self.bakim.yedekle()
        previous = self.bakim.dosya().read_bytes()
        with patch.object(self.depo, "yedekle", side_effect=lambda path: path.write_bytes(b"bozuk dosya")):
            with self.assertRaises(sqlite3.DatabaseError):
                self.bakim.yedekle()
        self.assertEqual(self.bakim.dosya().read_bytes(), previous)
        self.assertIn("doğrulanamadı", self.bakim.gorunum()["last_error"])
        self.assertEqual(len(list(self.bakim.dosya().parent.iterdir())), 2)

    def test_son_dosya_degistirilemezse_onceki_yedek_silinmez(self):
        self.bakim.yedekle()
        previous = self.bakim.dosya().read_bytes()
        from radar import bakim
        replace = bakim.os.replace

        def reject_latest(source, target):
            if target.name == "radar-son.sqlite3":
                raise PermissionError("Dosya kullanımda.")
            return replace(source, target)

        with patch("radar.bakim.os.replace", side_effect=reject_latest):
            with self.assertRaises(PermissionError):
                self.bakim.yedekle()
        self.assertEqual(self.bakim.dosya().read_bytes(), previous)
        self.assertEqual(len(list(self.bakim.dosya().parent.iterdir())), 2)

    def test_eszamanli_istek_beklemeden_reddedilir_ve_tek_isci_kapanir(self):
        started = threading.Event()
        proceed = threading.Event()
        self.addCleanup(proceed.set)
        backup = self.depo.yedekle

        def delayed(path):
            started.set()
            self.assertTrue(proceed.wait(timeout=3))
            backup(path)

        with patch.object(self.depo, "yedekle", side_effect=delayed):
            self.assertTrue(self.bakim.baslat())
            self.assertTrue(started.wait(timeout=2))
            self.assertTrue(self.bakim.gorunum()["running"])
            self.assertFalse(self.bakim.baslat())
            self.assertFalse(self.bakim.yedekle())
            proceed.set()
            self.bakim.kapan()
        self.assertFalse(self.bakim.thread.is_alive())
        self.assertTrue(self.bakim.gorunum()["available"])
        self.assertFalse(self.bakim.gorunum()["running"])
        self.assertFalse(self.bakim.baslat())

    def test_zamanli_yedek_hatasi_donguyu_oldurmez(self):
        backup = self.depo.yedekle
        calls = []
        waits = []

        def fail_once(path):
            calls.append(path)
            if len(calls) == 1:
                raise OSError("İlk deneme başarısız.")
            return backup(path)

        def next_period(seconds):
            waits.append(seconds)
            if len(waits) == 2:
                self.bakim.stop.set()
            return self.bakim.stop.is_set()

        with patch.object(self.depo, "yedekle", side_effect=fail_once), \
             patch.object(self.bakim.stop, "wait", side_effect=next_period), \
             self.assertLogs("radar", level="ERROR"):
            self.bakim.baslat()
            self.bakim.thread.join(timeout=3)
        self.assertFalse(self.bakim.thread.is_alive())
        self.assertEqual(len(calls), 2)
        self.assertEqual(waits, [YEDEK_ARALIGI, YEDEK_ARALIGI])
        self.assertTrue(self.bakim.gorunum()["available"])
        self.assertIsNone(self.bakim.gorunum()["last_error"])

    def test_gunluk_yedek_sayisi_yediyle_sinirlidir_ve_diger_dosya_korunur(self):
        self.bakim.klasor.mkdir()
        foreign = self.bakim.klasor / "kullanici-notu.txt"
        foreign.write_text("Saklanmalı", encoding="utf-8")
        first_day = datetime(2026, 10, 26, tzinfo=timezone.utc)
        with patch("radar.bakim.datetime", wraps=datetime) as clock:
            for day in range(8):
                clock.now.return_value = first_day + timedelta(days=day)
                self.depo.geri_bildirim(self.item_id, "yararli" if day % 2 else "yok")
                self.assertTrue(self.bakim.yedekle())
        files = {path.name for path in self.bakim.klasor.iterdir()}
        self.assertEqual(files, {"radar-son.sqlite3", "kullanici-notu.txt", *[f"ufuk-{day}.sqlite3" for day in range(7)]})
        self.assertEqual((self.bakim.klasor / "ufuk-0.sqlite3").read_bytes(), self.bakim.dosya().read_bytes())
        self.assertEqual(foreign.read_text(encoding="utf-8"), "Saklanmalı")


if __name__ == "__main__":
    unittest.main()
