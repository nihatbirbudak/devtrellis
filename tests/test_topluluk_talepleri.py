"""Özel talep kutusunun gizlilik, tekrar, kapasite ve saklama sınırlarını sınar."""

import io
import json
import sqlite3
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import topluluk
from radar import topluluk_talepleri as module
from radar.topluluk_depo import ToplulukDeposu
from radar.topluluk_talepleri import TalepDeposu, TalepHatasi, RETENTION_SECONDS


def payload(**changes):
    value = {"kind": "contribute", "category": "code", "subject": "Sentetik çeviri önerisi",
             "message": "Yalnız sentetik test mesajı.\nİkinci satır.", "name": "Örnek Katılımcı",
             "email": "tester+request@example.test", "language": "tr", "consent": True,
             "idempotency_key": str(uuid.uuid4()), "website": ""}
    value.update(changes)
    return value


class ToplulukTalepleriTestleri(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "community.sqlite3"
        self.now = 1800000000
        self.store = TalepDeposu(self.path, clock=lambda: self.now)

    def rows(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute("SELECT * FROM community_requests").fetchall()

    def assert_error(self, expected_status, call, *args, **kwargs):
        with self.assertRaises(TalepHatasi) as raised:
            call(*args, **kwargs)
        self.assertEqual(raised.exception.status, expected_status)
        return str(raised.exception)

    def test_receipt_ve_liste_iletisim_govde_ve_anahtari_aciklamaz(self):
        submitted = payload()
        receipt = self.store.gonder(submitted)
        self.assertEqual(set(receipt), {"id", "status", "created_at", "duplicate"})
        self.assertRegex(receipt["id"], r"^[0-9a-f]{32}$")
        self.assertEqual(receipt["status"], "new")
        self.assertFalse(receipt["duplicate"])
        self.assertTrue(receipt["created_at"].endswith("+00:00"))
        listing = self.store.listele()
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["limit"], 25)
        self.assertTrue({"message", "email", "name", "key_hash", "payload_hash", "idempotency_key"}.isdisjoint(listing["items"][0]))
        detail = self.store.oku(receipt["id"])
        self.assertEqual(detail["message"], submitted["message"])
        self.assertEqual(detail["email"], submitted["email"])
        self.assertEqual(detail["name"], submitted["name"])
        self.assertIs(detail["consent"], True)
        self.assertTrue({"key_hash", "payload_hash", "idempotency_key"}.isdisjoint(detail))
        self.assertNotIn(submitted["idempotency_key"], json.dumps(self.rows()))

    def test_ozel_mesaj_kamu_karti_aramasi_ve_hesap_tablosuna_girmez(self):
        community = ToplulukDeposu(self.path)
        with community.baglanti() as connection:
            before = {name: connection.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
                      for name in ("community_accounts", "community_entries", "community_revisions", "community_inbox")}
        self.store.gonder(payload(subject="SyntheticNeverPublic", message="Özel destek içeriği."))
        self.assertEqual(community.listele(query="SyntheticNeverPublic")["total"], 0)
        self.assertEqual(community.listele()["library"]["published_research_total"], 0)
        with community.baglanti() as connection:
            after = {name: connection.execute("SELECT COUNT(*) FROM " + name).fetchone()[0] for name in before}
        self.assertEqual(before, after)

    def test_tekrar_ayni_fis_farkli_icerik_409_durum_ve_sure_uzamaz(self):
        submitted = payload()
        first = self.store.gonder(submitted)
        self.now += 86400
        updated = self.store.durum_degistir(first["id"], "closed")
        self.now += 86400
        duplicate = self.store.gonder(submitted)
        self.assertEqual(duplicate, dict(first, status="closed", duplicate=True))
        self.assertEqual(self.store.oku(first["id"])["updated_at"], updated["updated_at"])
        self.assert_error(409, self.store.gonder, dict(submitted, message="Başka sentetik mesaj"))
        self.assertEqual(self.store.oku(first["id"])["message"], submitted["message"])
        self.assertEqual(len(self.rows()), 1)

    def test_istege_bagli_alanlar_ve_uuid_buyuk_harf_tekrari(self):
        submitted = payload()
        for field in ("name", "email", "website"):
            submitted.pop(field)
        first = self.store.gonder(submitted)
        duplicate = self.store.gonder(dict(submitted, name="", email="", website="",
                                            idempotency_key=submitted["idempotency_key"].upper()))
        self.assertEqual(duplicate, dict(first, duplicate=True))
        self.assertEqual(self.store.oku(first["id"])["email"], "")

    def test_kapasite_atomik_tekrar_doluyken_kabul_edilir(self):
        self.assertEqual((module.NEW_LIMIT, module.TOTAL_LIMIT), (200, 1000))
        submitted = payload()
        with patch.object(module, "NEW_LIMIT", 1), patch.object(module, "TOTAL_LIMIT", 2):
            first = self.store.gonder(submitted)
            self.assert_error(429, self.store.gonder, payload())
            self.assertTrue(self.store.gonder(submitted)["duplicate"])
            self.store.durum_degistir(first["id"], "in_review")
            second = self.store.gonder(payload())
            self.assert_error(429, self.store.durum_degistir, first["id"], "new")
            self.assertEqual(self.store.oku(first["id"])["status"], "in_review")
            self.store.durum_degistir(second["id"], "closed")
            self.assert_error(429, self.store.gonder, payload())
            self.assertEqual(len(self.rows()), 2)

    def test_es_zamanli_farkli_gonderimler_yeni_ve_toplam_kapasiteyi_asmaz(self):
        for new_limit, total_limit in ((1, 10), (10, 1)):
            with self.subTest(new_limit=new_limit, total_limit=total_limit):
                store = TalepDeposu(self.path.with_name(f"race-{new_limit}.sqlite3"), clock=lambda: self.now)
                barrier = threading.Barrier(6)
                def submit(number):
                    barrier.wait(timeout=5)
                    try:
                        return store.gonder(payload(subject=f"Sentetik yarış {number}"))["id"]
                    except TalepHatasi as exc:
                        return exc.status
                with patch.object(module, "NEW_LIMIT", new_limit), patch.object(module, "TOTAL_LIMIT", total_limit), \
                        ThreadPoolExecutor(max_workers=6) as executor:
                    results = list(executor.map(submit, range(6)))
                self.assertEqual(sum(isinstance(value, str) for value in results), 1)
                self.assertEqual(results.count(429), 5)
                self.assertEqual(store.listele()["total"], 1)

    def test_es_zamanli_ayni_anahtar_yalniz_bir_talep_olusturur(self):
        submitted = payload()
        barrier = threading.Barrier(5)
        def submit(_):
            barrier.wait(timeout=5)
            return self.store.gonder(submitted)
        with ThreadPoolExecutor(max_workers=5) as executor:
            results = list(executor.map(submit, range(5)))
        self.assertEqual(len({result["id"] for result in results}), 1)
        self.assertEqual(sum(not result["duplicate"] for result in results), 1)
        self.assertEqual(len(self.rows()), 1)

    def test_retention_ilk_tarihten_90_gun_durum_guncellemesi_uzatmaz(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TABLE unrelated (message TEXT, created_at INTEGER)")
            connection.execute("INSERT INTO unrelated VALUES ('korunacak',0)")
        receipt = self.store.gonder(payload())
        self.now += RETENTION_SECONDS - 1
        self.store.durum_degistir(receipt["id"], "in_review")
        self.assertEqual(self.store.listele()["total"], 1)
        self.now += 1
        self.assertEqual(self.store.listele()["total"], 0)
        self.assertEqual(self.rows(), [])
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT * FROM unrelated").fetchall(), [("korunacak", 0)])

    def test_expired_404_ve_409_yaniti_temizligi_geri_almaz(self):
        receipt = self.store.gonder(payload())
        self.now += RETENTION_SECONDS
        self.assert_error(404, self.store.oku, receipt["id"])
        self.assertEqual(self.rows(), [])
        old = self.store.gonder(payload())
        self.now += 1
        current_payload = payload()
        current = self.store.gonder(current_payload)
        self.now += RETENTION_SECONDS - 1
        self.assert_error(409, self.store.gonder, dict(current_payload, subject="Farklı konu"))
        self.assertEqual([row[0] for row in self.rows()], [current["id"]])
        self.assert_error(404, self.store.durum_degistir, old["id"], "closed")

    def test_expire_kapasiteyi_acar_ayni_eski_anahtar_yeni_fis_olusturur(self):
        submitted = payload()
        with patch.object(module, "NEW_LIMIT", 1), patch.object(module, "TOTAL_LIMIT", 1):
            first = self.store.gonder(submitted)
            self.now += RETENTION_SECONDS
            second = self.store.gonder(submitted)
        self.assertFalse(second["duplicate"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["created_at"], second["created_at"])
        self.assertEqual(len(self.rows()), 1)

    def test_yedek_kopyasi_yeniden_acilinca_eski_talep_gorunmez(self):
        receipt = self.store.gonder(payload())
        copied_path = self.path.with_name("restored.sqlite3")
        with closing(sqlite3.connect(self.path)) as original, closing(sqlite3.connect(copied_path)) as copied:
            original.backup(copied)
        self.now += RETENTION_SECONDS
        restored = TalepDeposu(copied_path, clock=lambda: self.now)
        self.assertEqual(restored.listele()["total"], 0)
        self.assert_error(404, restored.oku, receipt["id"])
        # Ayrı yedek/asıl dosya otomatik silinmiş gibi bir iddia yok.
        self.assertEqual(len(self.rows()), 1)

    def test_alan_dogrulama_kontrol_karakteri_honeypot_onay_ve_email(self):
        invalid = [
            {"extra": "izin verilmeyen alan"}, {"kind": "support", "category": "code"},
            {"kind": "contribute", "category": "privacy"}, {"kind": []}, {"category": True},
            {"consent": False}, {"consent": 1}, {"language": "de"}, {"website": " "}, {"website": None},
            {"subject": ""}, {"message": "  "}, {"subject": "x" * 161}, {"message": "x" * 4001},
            {"name": "x" * 81}, {"name": None}, {"email": []}, {"email": "x" * 255},
            {"subject": "A\nB"}, {"message": "A\x1b[31mB"}, {"message": "A\rB"},
            {"message": "A\u202eB"}, {"name": "A\u200bB"}, {"message": "A\x7fB"},
            {"idempotency_key": "x"}, {"idempotency_key": uuid.uuid4().hex},
            {"email": "a..b@example.test"}, {"email": "Name <a@example.test>"},
            {"email": "a@-example.test"}, {"email": "a@example"}, {"email": "a@@example.test"},
            {"email": "a\r\nb@example.test"}, {"email": "a@127.0.0.1"}, {"subject": False},
        ]
        for changes in invalid:
            with self.subTest(changes=changes):
                self.assert_error(422, self.store.gonder, payload(**changes))
        for bad in (None, [], "text", {}):
            self.assert_error(422, self.store.gonder, bad)
        self.assertEqual(self.rows(), [])

    def test_ust_sinirlar_unicode_isim_ve_mesaj_satir_sonu(self):
        email = "a" * 64 + "@" + "b" * 63 + "." + "c" * 63 + "." + "d" * 61
        self.assertEqual(len(email), 254)
        receipt = self.store.gonder(payload(subject="a" * 160, message="m" * 4000, name="Ö" * 80, email=email))
        self.assertEqual(len(self.store.oku(receipt["id"])["message"]), 4000)
        other = self.store.gonder(payload(kind="support", category="privacy", message="Bir\r\nİki", email="a@ÖRNEK.test"))
        self.assertEqual(self.store.oku(other["id"])["message"], "Bir\nİki")
        self.assertIn("@xn--", self.store.oku(other["id"])["email"])

    def test_sayfalama_durum_filtreleri_ve_gecersiz_kimlikler(self):
        ids = []
        for _ in range(3):
            ids.append(self.store.gonder(payload())["id"])
            self.now += 1
        self.store.durum_degistir(ids[0], "closed")
        page = self.store.listele(status="new", offset=1, limit=1)
        self.assertEqual((page["total"], page["offset"], page["limit"]), (2, 1, 1))
        self.assertEqual([item["id"] for item in page["items"]], [ids[1]])
        for values in ({"status": "invalid"}, {"status": []}, {"offset": -1}, {"offset": True},
                       {"limit": 0}, {"limit": 101}, {"limit": True}):
            self.assert_error(422, self.store.listele, **values)
        for identifier in (None, "../private", "a" * 31, "A" * 32, "' OR 1=1 --"):
            self.assert_error(422, self.store.oku, identifier)
        self.assert_error(404, self.store.oku, "0" * 32)
        self.assert_error(422, self.store.durum_degistir, ids[0], "published")

    def test_hatalar_govdeyi_emaili_ve_anahtari_yansitmaz(self):
        submitted = payload(message="SyntheticPrivateBody", email="unique-private@example.test")
        self.store.gonder(submitted)
        error = self.assert_error(409, self.store.gonder, dict(submitted, subject="Başka konu"))
        for private in (submitted["message"], submitted["email"], submitted["idempotency_key"]):
            self.assertNotIn(private, error)

    def test_cli_liste_detay_durum_saf_json_ve_eksik_dosya_korumasi(self):
        # CLI gerçek saate göre çalışır; sabit saati gerçek saatten ileri bir tarihe çekeriz.
        self.now = int(module.time.time())
        receipt = self.store.gonder(payload(subject="<script>sentetik</script>", message="İki\nsatır"))
        def cli(*args):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(topluluk.main(["--db", str(self.path), *args]), 0)
            text = output.getvalue()
            self.assertEqual(len(text.splitlines()), 1)
            self.assertTrue(text.isascii())
            return json.loads(text)
        listing = cli("requests-list", "--status", "new", "--limit", "1")
        self.assertNotIn("message", listing["items"][0])
        self.assertNotIn("email", listing["items"][0])
        self.assertEqual(cli("requests-show", receipt["id"])["message"], "İki\nsatır")
        # Eski veya dışarıda düzenlenmiş DB içeriği bile terminal kaçışını çalıştırmaz.
        with self.store.baglanti() as connection:
            connection.execute("UPDATE community_requests SET message=? WHERE id=?", ("\x1b[31mSentetik\u202e", receipt["id"]))
        output = io.StringIO()
        with redirect_stdout(output):
            topluluk.main(["--db", str(self.path), "requests-show", receipt["id"]])
        self.assertNotIn("\x1b", output.getvalue())
        self.assertNotIn("\u202e", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["message"], "\x1b[31mSentetik\u202e")
        changed = cli("requests-status", receipt["id"], "closed")
        self.assertEqual(changed["status"], "closed")
        self.assertEqual(set(changed), {"id", "status", "created_at", "updated_at"})
        missing = self.path.with_name("missing.sqlite3")
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            topluluk.main(["--db", str(missing), "requests-list"])
        self.assertEqual(raised.exception.code, 2)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
