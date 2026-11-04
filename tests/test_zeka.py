"""Yapay zekâ işlerinin gizlilik, kaynak kanıtı, kalıcılık ve bütçe sınırlarını gerçek ağa çıkmadan doğrular."""

import hashlib
import json
import os
import ssl
import sys
import tempfile
import threading
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".deps"))

from radar import zeka
from radar.depo import Depo


def answer(sources, kind="summary", **changes):
    value = {"title_tr": "Yerel ses ajanlarının kaynaklı değerlendirmesi",
             "summary_tr": "Kaynaklar yerel ses ajanlarının bellek kullanımı ve çalışma yöntemleri hakkında bilgiler veriyor.",
             "benefit_tr": "Yerel cihazlardaki ses işleme seçeneklerini karşılaştırmaya yardımcı olabilir.",
             "limitations": ["Yayıncı iddiaları bağımsız bir deneyle doğrulanmadı."],
             "next_steps": ["Donanım ve lisans koşullarını özgün kaynaklardan inceleyin."],
             "evidence": [{"item_id": source["item_id"], "quote": " ".join(source["public_text"].split())[:100]}
                          for source in sources]}
    if kind in {"interest", "screen", "review"}:
        value.update(relevance_score=82, relevance_reason_tr="Yerel ses ve gizlilik gereksinimleriyle ilişkili bir seçenek olabilir.",
                     fit="high", blockers=["Donanım uyumluluğu kaynak metninde belirtilmiyor."])
        if kind == 'review':
            value['research_decision'] = 'research'
    elif kind == "deep":
        value.update(recommendation="denemeye_deger", compatibility_tr="Yerel ses işleme gereksinimiyle ilgili olabilir; donanım uyumu doğrulanmalı.",
                     requirements=["Kaynakta belirtilen donanım gereksinimleri incelenmeli."],
                     integration_steps=["İnsan onayından sonra ayrı deneme ortamında değerlendirme yapılmalı."],
                     verification_plan=["Özgün kaynak koşullarıyla ses kalitesi karşılaştırılmalı."],
                     open_questions=["Yerel cihazdaki bellek tüketimi ne kadar olacak?"])
    value.update(changes)
    return json.dumps(value, ensure_ascii=False)


def uncertain_audit(payload):
    """Eski davranış testinin sahtesi semantik desteği kendiliğinden onaylamaz."""
    return json.dumps({"coverage_complete": True, "claims": [
        {"id": claim["id"], "status": "uncertain", "reason_tr": "Bu davranış testinde kaynak desteği değerlendirilmedi.",
         "evidence": []} for claim in payload["claims"]]}, ensure_ascii=False)


class ZekaIsleriTestleri(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
        self.configs = [{"id": f"source_{number}", "name": f"Kaynak {number}", "publisher": f"Yayıncı {number}",
                         "topic": "ai", "interval_hours": 24, "benefit": "PUBLIC_BENEFIT"} for number in range(7)]
        self.depo = Depo(Path(self.temporary.name) / "radar.sqlite3", self.configs)
        self.raw = {"external_id": "one", "url": "https://example.org/article", "title": "Local voice agents and memory methods",
                    "excerpt": "Local voice agents use memory models for reliable speech processing. " * 4,
                    "language": "en", "evidence_type": "duyuru"}
        self.depo.kaydet("source_0", [self.raw])
        self.item = self.depo.gorunum()["items"][0]["id"]
        self.calls = []

        def provider(messages, **kwargs):
            self.calls.append((messages, kwargs))
            payload = json.loads(messages[1]["content"])
            if payload["kind"] == "verify":
                return uncertain_audit(payload)
            return answer(payload["sources"], payload["kind"])

        self.provider = provider
        self.manager = self.make_manager()

    def make_manager(self, **changes):
        values = {"provider": self.provider, "credential_provider": lambda: "FAKE-TEST-SECRET",
                  "clock": lambda: self.now, **changes}
        manager = zeka.ZekaIsleri(self.depo, **values)
        self.addCleanup(manager.kapan)
        return manager

    def finish(self, manager=None):
        manager = manager or self.manager
        manager.worker.join(timeout=5)
        self.assertFalse(manager.worker.is_alive())

    def production_call(self):
        return next(call for call in reversed(self.calls)
                    if json.loads(call[0][1]["content"])["kind"] not in {"verify", "revise"})

    def other(self, number=1, topic=None, publisher=None):
        if topic or publisher:
            source = {**self.configs[number], **({"topic": topic} if topic else {}), **({"publisher": publisher} if publisher else {})}
            Depo(self.depo.path, [source])
        self.depo.kaydet(f"source_{number}", [{**self.raw, "external_id": str(number), "url": f"https://example.org/{number}"}])
        return next(item["id"] for item in self.depo.gorunum()["items"] if item["source_id"] == f"source_{number}")

    def article(self, *, body="ARTICLE_PUBLIC_BODY " * 30, revision=1, status="hazir"):
        with self.depo.baglanti(write=True) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS article_reads (
                item_id TEXT PRIMARY KEY, status TEXT, body TEXT, body_hash TEXT, source_revision INTEGER, source_url TEXT
            )""")
            connection.execute("INSERT OR REPLACE INTO article_reads VALUES(?,?,?,?,?,?)",
                (self.item, status, body, hashlib.sha256(body.encode()).hexdigest(), revision, self.raw["url"]))

    def test_baslangic_durum_ve_oku_model_cagirmaz(self):
        self.assertTrue(self.manager.durum()["configured"])
        self.assertEqual(self.manager.durum()["daily_remaining"], 20)
        self.assertEqual(self.manager.oku(self.item)["status"], "yapilmadi")
        self.assertEqual(self.calls, [])

    def test_ozet_kalici_ayri_alan_ve_ozgun_kayit_degismaz(self):
        original = self.depo.gorunum()["items"][0]
        self.assertTrue(self.manager.baslat(self.item)["started"])
        self.finish()
        saved = self.manager.oku(self.item)
        self.assertEqual(saved["status"], "hazir")
        self.assertFalse(saved["stale"])
        self.assertEqual(saved["source_revision"], 1)
        self.assertEqual(saved["sources"][0]["url"], self.raw["url"])
        self.assertNotIn("public_text", saved["sources"][0])
        self.assertIn("Kaynak metninde eşleşen alıntılar", saved["text"])
        with self.depo.baglanti() as connection:
            enriched = zeka.ozetleri_ekle([original], connection)[0]
            all_stored = str([tuple(row) for row in connection.execute("SELECT * FROM ai_results")])
            all_stored += str([tuple(row) for row in connection.execute("SELECT * FROM ai_attempts")])
        self.assertIn("ai_summary", enriched)
        self.assertNotIn("ai_summary", original)
        self.assertEqual(enriched["title"], original["title"])
        self.assertEqual(enriched["summary"], original["summary"])
        self.assertNotIn("FAKE-TEST-SECRET", all_stored)
        self.manager.kapan()
        reopened = self.make_manager()
        self.assertEqual(reopened.oku(self.item)["result"], saved["result"])
        self.assertEqual(reopened.durum()["daily_used"], 2)

    def test_kisisel_notlar_ve_isler_gonderilmez_degismez(self):
        job = self.depo.arastirma_sec(self.item)
        self.depo.geri_bildirim(self.item, "yararli", "PRIVATE_FEEDBACK_NOTE")
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE jobs SET result='PRIVATE_JOB_RESULT',owner='PRIVATE_OWNER' WHERE id=?", (job["id"],))
            connection.execute("INSERT INTO research_notes VALUES(?,1,1,'PRIVATE_RESEARCH_NOTE',?)", (job["id"], self.now.isoformat()))
            before = [tuple(row) for row in connection.execute("SELECT * FROM jobs")]
            before_notes = [tuple(row) for row in connection.execute("SELECT * FROM research_notes")]
        self.manager.baslat(self.item)
        self.finish()
        sent = json.dumps(self.calls[0][0])
        for private in ("PRIVATE_FEEDBACK_NOTE", "PRIVATE_JOB_RESULT", "PRIVATE_OWNER", "PRIVATE_RESEARCH_NOTE", "PUBLIC_BENEFIT"):
            self.assertNotIn(private, sent)
        with self.depo.baglanti() as connection:
            self.assertEqual([tuple(row) for row in connection.execute("SELECT * FROM jobs")], before)
            self.assertEqual([tuple(row) for row in connection.execute("SELECT * FROM research_notes")], before_notes)

    def test_guncel_ozetler_arama_ve_kartlar_icin_ayni_kaynak_denetimini_kullanir(self):
        second = self.other()
        for item in (self.item, second):
            self.manager.baslat(item)
            self.finish()
        with self.depo.baglanti() as connection:
            summaries = zeka.guncel_ozetler(connection)
            self.assertEqual(set(summaries), {self.item, second})
            self.assertEqual(set(summaries[self.item]), {"title_tr", "summary_tr", "benefit_tr", "generated_at", "model"})
            self.assertEqual(zeka.guncel_ozetler(connection, item_ids=[second, second]), {second: summaries[second]})
            self.assertEqual(zeka.guncel_ozetler(connection, item_ids=[]), {})
            self.assertEqual(zeka.ozetleri_ekle([{"id": self.item}], connection)[0]["ai_summary"], summaries[self.item])
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE items SET url='https://example.org/changed' WHERE id=?", (self.item,))
            connection.execute("UPDATE ai_results SET status='hata' WHERE item_id=?", (second,))
        with self.depo.baglanti() as connection:
            self.assertEqual(zeka.guncel_ozetler(connection), {})
            self.assertNotIn("ai_summary", zeka.ozetleri_ekle([{"id": self.item, "ai_summary": {"old": True}}], connection)[0])

    def test_yalniz_hazir_guncel_makale_govdesi_gonderilir(self):
        self.article()
        self.manager.baslat(self.item)
        self.finish()
        self.assertIn("ARTICLE_PUBLIC_BODY", self.calls[0][0][1]["content"])
        self.assertTrue(self.manager.oku(self.item)["sources"][0]["body_hash"])
        self.now += timedelta(minutes=5)
        self.article(body="STALE_PRIVATE_LOOKING_BODY " * 30, revision=99)
        self.manager.baslat(self.item)
        self.finish()
        self.assertNotIn("STALE_PRIVATE_LOOKING_BODY", self.calls[-1][0][1]["content"])
        self.assertIsNone(self.manager.oku(self.item)["sources"][0]["body_hash"])

    def test_tarih_baglami_ve_ozgun_makale_icerigi_kart_ozetinden_once_gelir(self):
        self.article(body="Start project: 01-01-2026\n\nA public article describes reliable local speech agents and memory methods in detail.")
        with self.depo.baglanti(write=True) as connection:
            connection.execute("ALTER TABLE article_reads ADD COLUMN title TEXT")
            connection.execute("UPDATE article_reads SET title='Original article headline'")
            connection.execute("UPDATE items SET title='CARD_TITLE_ONLY',excerpt='CARD_EXCERPT_ONLY' WHERE id=?", (self.item,))
        self.manager.baslat(self.item)
        self.finish()
        sent = json.loads(self.calls[0][0][1]["content"])
        self.assertEqual(sent["as_of"], "2026-10-26")
        source = sent["sources"][0]
        self.assertIn("Original article headline", source["public_text"])
        self.assertNotIn("CARD_TITLE_ONLY", source["public_text"])
        self.assertNotIn("CARD_EXCERPT_ONLY", source["public_text"])
        self.assertIn("A public article describes", source["quote_options"][0]['quote'])
        self.assertNotIn("Start project", source["quote_options"][0]['quote'])
        self.assertTrue(all(choice['quote'] in ' '.join(source["public_text"].split()) for choice in source["quote_options"]))

    def test_govde_degistiginde_onceki_ozet_eskir(self):
        self.article()
        self.manager.baslat(self.item)
        self.finish()
        self.article(body="New public article content " * 30)
        self.assertTrue(self.manager.oku(self.item)["stale"])
        with self.depo.baglanti() as connection:
            enriched = zeka.ozetleri_ekle([{"id": self.item}], connection)[0]
        self.assertNotIn("ai_summary", enriched)

    def test_sonradan_okunan_govde_aciklamadan_uretilen_tum_raporlari_eskitir(self):
        kinds = ("summary", "screen", "deep")
        for kind in kinds:
            self.manager.baslat(self.item, kind)
            self.finish()
            saved = self.manager.oku(self.item, kind)
            self.assertEqual(saved["status"], "hazir")
            self.assertFalse(saved["stale"])
            self.assertIsNone(saved["sources"][0]["body_hash"])
        self.article(body="The complete public article contains additional evidence about local speech agents. " * 20)
        for kind in kinds:
            with self.subTest(kind=kind):
                saved = self.manager.oku(self.item, kind)
                self.assertTrue(saved["stale"])
                self.assertTrue(saved["has_result"])
                self.assertEqual(saved["source_revision"], saved["current_revision"])
        with self.depo.baglanti() as connection:
            self.assertEqual(zeka.guncel_ozetler(connection), {})
        self.now += timedelta(minutes=5)
        self.manager.baslat(self.item, "deep")
        self.finish()
        updated = self.manager.oku(self.item, "deep")
        self.assertFalse(updated["stale"])
        self.assertTrue(updated["sources"][0]["body_hash"])

    def test_eski_metinozetsiz_kayit_da_govde_sonradan_okununca_eskir(self):
        self.manager.baslat(self.item, "deep")
        self.finish()
        with self.depo.baglanti(write=True) as connection:
            row = connection.execute("SELECT sources_json FROM ai_results WHERE item_id=? AND kind='deep'", (self.item,)).fetchone()
            refs = json.loads(row["sources_json"])
            refs[0].pop("text_hash")
            connection.execute("UPDATE ai_results SET sources_json=? WHERE item_id=? AND kind='deep'", (json.dumps(refs), self.item))
        self.assertFalse(self.manager.oku(self.item, "deep")["stale"])
        self.article()
        self.assertTrue(self.manager.oku(self.item, "deep")["stale"])

    def test_govde_ozeti_ayni_kalsa_da_modelin_gordugu_baslik_degisirse_rapor_eskir(self):
        self.article()
        with self.depo.baglanti(write=True) as connection:
            connection.execute("ALTER TABLE article_reads ADD COLUMN title TEXT")
            connection.execute("UPDATE article_reads SET title='Original public article title'")
        self.manager.baslat(self.item)
        self.finish()
        before = self.manager.oku(self.item)
        self.assertFalse(before["stale"])
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE article_reads SET title='Updated public article title'")
        after = self.manager.oku(self.item)
        self.assertTrue(after["stale"])
        self.assertEqual(before["sources"][0]["body_hash"], after["sources"][0]["body_hash"])

    def test_arastirma_farkli_yayincilarla_en_fazla_bes_kaynak_kullanir(self):
        for number in range(1, 7):
            self.other(number)
        self.manager.baslat(self.item, "research")
        self.finish()
        saved = self.manager.oku(self.item, "research")
        self.assertEqual(saved["status"], "hazir")
        self.assertEqual(len(saved["sources"]), 5)
        self.assertEqual(len({source["publisher"] for source in saved["sources"]}), 5)
        sent = json.loads(self.calls[0][0][1]["content"])["sources"]
        self.assertLessEqual(sum(len(source["public_text"]) for source in sent), 30_000)
        self.assertTrue(all(set(source) == {"item_id", "url", "public_text", "quote_options"} for source in sent))
        self.assertEqual({source['item_id']: source['url'] for source in sent},
                         {source['item_id']: source['url'] for source in saved['sources']})
        for source in sent:
            self.assertTrue(1 <= len(source["quote_options"]) <= 3)
            self.assertEqual([choice['id'] for choice in source['quote_options']], list(range(1, len(source['quote_options']) + 1)))
            for choice in source["quote_options"]:
                self.assertEqual(set(choice), {'id', 'quote'})
                quote = choice['quote']
                self.assertTrue(20 <= len(quote) <= 220)
                self.assertIn(quote, ' '.join(source["public_text"].split()))

    def test_ilgisiz_konu_ve_ayni_yayinci_arastirmayi_cogaltmaz(self):
        self.other(1, publisher="Yayıncı 0")
        self.other(2, topic="tarim")
        with self.assertRaises(zeka.ZekaHazirDegil):
            self.manager.baslat(self.item, "research")
        self.assertEqual(self.manager.durum()["daily_used"], 0)
        self.assertEqual(self.calls, [])

    def test_genel_birkaç_sozcuk_konu_ilgisi_sayilmaz(self):
        second = self.other()
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE items SET title='Student tutoring outcomes',excerpt=? WHERE id=?",
                ("Reliable processing methods help students understand educational subjects in several classroom situations.", second))
        with self.assertRaises(zeka.ZekaHazirDegil):
            self.manager.baslat(self.item, "research")
        self.assertEqual(self.manager.durum()["daily_used"], 0)

    def test_python_duyurusundaki_agents_dosyasi_ajan_arastirmasina_kaynak_olmaz(self):
        python_item = self.other()
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE items SET title=?,excerpt=? WHERE id=?", (
                "Self-Compacting Language Model Agents",
                "Long agent traces contain language model context. Agents can compact their history before the context window fills.",
                self.item))
            connection.execute("UPDATE items SET title=?,excerpt=? WHERE id=?", (
                "Lightning Talks (Python Language Summit 2026)",
                "Lightning talks discuss Python improvements and an AGENTS.md file for CPython, alongside language maintenance proposals.",
                python_item))
        with self.assertRaises(zeka.ZekaHazirDegil):
            self.manager.baslat(self.item, "research")
        self.assertEqual(self.manager.durum()["daily_used"], 0)
        related = self.other(2)
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE items SET title=?,excerpt=? WHERE id=?", (
                "Training Language Model Agents with Existing Tools",
                "Agents use tool context during training. The framework supports long model interactions and existing agent workflows.",
                related))
        self.manager.baslat(self.item, "research")
        self.finish()
        saved = self.manager.oku(self.item, "research")
        self.assertEqual(saved["status"], "hazir")
        self.assertEqual({source["item_id"] for source in saved["sources"]}, {self.item, related})

    def test_raspberry_pi_oyun_rehberine_ucus_karti_eklenmez_ayni_oyun_konusu_eklenir(self):
        flight = self.other()
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE items SET title=?,excerpt=? WHERE id=?", (
                "Introducing the Raspberry Pi Guide to Retro Gaming",
                "The definitive guide on how to play your favourite games of yesteryear on a Raspberry Pi. "
                "This retro gaming guide describes controllers and emulation on a Raspberry Pi compute module.", self.item))
            connection.execute("UPDATE items SET title=?,excerpt=? WHERE id=?", (
                "Get the Pixhawk Raspberry Pi CM4 Baseboard by Holybro talking with PX4",
                "A guide to the Raspberry Pi compute module baseboard, featuring flight control tips and tutorials.", flight))
        self.assertEqual(self.manager.kaynak_adaylari(self.item), [])
        with self.assertRaises(zeka.ZekaHazirDegil):
            self.manager.baslat(self.item, 'research')
        self.assertEqual(self.manager.durum()['daily_used'], 0)

        gaming = self.other(2)
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE items SET title=?,excerpt=? WHERE id=?", (
                "Retro gaming controllers and emulation on Raspberry Pi",
                "The article compares retro gaming controllers and emulation options for playing classic games at home.", gaming))
        self.assertEqual(self.manager.kaynak_adaylari(self.item), [gaming])
        self.manager.baslat(self.item, 'deep')
        self.finish()
        saved = self.manager.oku(self.item, 'deep')
        self.assertEqual(saved['status'], 'hazir', saved['last_error'])
        self.assertEqual({source['item_id'] for source in saved['sources']}, {self.item, gaming})

    def test_nvidia_cihaz_ailesi_oyun_ve_robotik_konularini_birlestirmez(self):
        robotics = self.other()
        gaming = self.other(2)
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE items SET title=?,excerpt=? WHERE id=?", (
                "NVIDIA GeForce GPU gaming performance update",
                "Gaming frame latency is measured on NVIDIA GeForce hardware. This update reports rendering and memory measurements.", self.item))
            connection.execute("UPDATE items SET title=?,excerpt=? WHERE id=?", (
                "NVIDIA GeForce GPU robotics perception update",
                "NVIDIA GeForce hardware is used for robotics perception. The update discusses memory use and camera processing.", robotics))
            connection.execute("UPDATE items SET title=?,excerpt=? WHERE id=?", (
                "Measuring gaming frame latency on NVIDIA GeForce",
                "This independent gaming article measures frame latency and rendering behaviour under several workloads.", gaming))
        self.assertEqual(self.manager.kaynak_adaylari(self.item), [gaming])
        self.manager.baslat(self.item, 'research')
        self.finish()
        saved = self.manager.oku(self.item, 'research')
        self.assertEqual(saved['status'], 'hazir', saved['last_error'])
        self.assertEqual({source['item_id'] for source in saved['sources']}, {self.item, gaming})

    def test_kaynak_degiserken_suren_is_eski_revizyonu_dogru_isaretler(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def blocked(messages, **kwargs):
            started.set()
            release.wait(timeout=5)
            return self.provider(messages, **kwargs)
        self.manager.provider = blocked
        self.manager.baslat(self.item)
        self.assertTrue(started.wait(timeout=2))
        self.depo.kaydet("source_0", [{**self.raw, "excerpt": "Changed voice agents content." * 6}])
        release.set()
        self.finish()
        self.assertTrue(self.manager.oku(self.item)["stale"])
        self.assertEqual(self.manager.oku(self.item)["source_revision"], 1)
        self.assertEqual(self.manager.oku(self.item)["current_revision"], 2)

    def test_tek_isci_ayni_istegi_tekrarlamaz_farkli_isi_reddeder(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def blocked(messages, **kwargs):
            started.set()
            release.wait(timeout=5)
            return self.provider(messages, **kwargs)
        self.manager.provider = blocked
        self.manager.baslat(self.item)
        self.assertTrue(started.wait(timeout=2))
        self.assertFalse(self.manager.baslat(self.item)["started"])
        with self.assertRaises(zeka.ZekaMesgul):
            self.manager.baslat(self.item, "research")
        with self.assertRaises(zeka.ZekaMesgul):
            self.make_manager()
        release.set()
        self.finish()
        self.assertEqual(self.manager.durum()["daily_used"], 2)

    def test_hata_sir_saklamaz_ve_onceki_sonucu_korur(self):
        self.manager.baslat(self.item)
        self.finish()
        previous = self.manager.oku(self.item)["result"]
        self.now += timedelta(minutes=5)
        self.manager.provider = Mock(side_effect=RuntimeError("FAKE-TEST-SECRET HTTP body private debug"))
        self.manager.baslat(self.item)
        self.finish()
        failed = self.manager.oku(self.item)
        self.assertEqual(failed["status"], "hata")
        self.assertEqual(failed["result"], previous)
        self.assertNotIn("FAKE-TEST-SECRET", json.dumps(failed))
        with self.depo.baglanti() as connection:
            errors = str([tuple(row) for row in connection.execute("SELECT * FROM ai_attempts")])
            self.assertNotIn("ai_summary", zeka.ozetleri_ekle([{"id": self.item}], connection)[0])
        self.assertNotIn("private debug", errors)
        self.assertEqual(self.manager.durum()["daily_used"], 3)

    def test_saglayici_zaman_asimi_ve_dogrulama_hatalari_guvenli_ayri_mesajlardir(self):
        errors = []
        for response in (zeka.ZekaZamanAsimi("FAKE_SECRET timeout internals"),
                         zeka.ZekaYanitHatasi("FAKE_SECRET HTTP headers"), "geçersiz JSON"):
            self.manager.provider = Mock(side_effect=response) if isinstance(response, Exception) else Mock(return_value=response)
            self.manager.baslat(self.item)
            self.finish()
            value = self.manager.oku(self.item)
            self.assertEqual(value['status'], 'hata')
            self.assertNotIn('FAKE_SECRET', value['last_error'])
            errors.append(value['last_error'])
            self.now += timedelta(minutes=5)
        self.assertIn('süre', errors[0])
        self.assertIn('sağlayıcısı isteği', errors[1])
        self.assertIn('kaynak veya biçim', errors[2])
        self.assertEqual(len(set(errors)), 3)
        self.assertEqual(self.manager.durum()['daily_used'], 3)

    def test_gunluk_yirmi_basarisiz_deneme_kalici_sinir_ve_yeni_gun(self):
        self.manager.provider = Mock(side_effect=ValueError("Sahte hata"))
        for _ in range(20):
            self.manager.baslat(self.item)
            self.finish()
            self.now += timedelta(minutes=5)
        self.assertEqual(self.manager.durum()["daily_remaining"], 0)
        self.manager.kapan()
        reopened = self.make_manager()
        with self.assertRaises(zeka.ZekaSiniri):
            reopened.baslat(self.item)
        self.assertEqual(reopened.durum()["daily_used"], 20)
        self.now = self.now.replace(hour=0, minute=0) + timedelta(days=1)
        self.assertEqual(reopened.durum()["daily_remaining"], 20)

    def test_bes_dakika_bekleme_iki_is_turu_icin_ortaktir(self):
        self.other()
        self.manager.baslat(self.item)
        self.finish()
        self.now += timedelta(seconds=299)
        with self.assertRaises(zeka.ZekaSiniri) as error:
            self.manager.baslat(self.item, "research")
        self.assertEqual(error.exception.retry_after_seconds, 1)
        self.now += timedelta(seconds=1)
        self.manager.baslat(self.item, "research")
        self.finish()
        self.assertEqual(self.manager.oku(self.item, "research")["status"], "hazir")

    def test_yarim_is_yeniden_acilista_kesildi_olur_ve_butcede_kalir(self):
        self.manager.kapan()
        with self.depo.baglanti(write=True) as connection:
            connection.execute("INSERT INTO ai_attempts VALUES('interrupted',?,'summary',?,?,NULL,'calisiyor',NULL)",
                               (self.item, zeka.MODEL, self.now.isoformat()))
            connection.execute("INSERT INTO ai_results(item_id,kind,status,model,attempt_id,last_attempt_at) "
                               "VALUES(?,'summary','calisiyor',?,'interrupted',?)", (self.item, zeka.MODEL, self.now.isoformat()))
        reopened = self.make_manager()
        self.assertEqual(reopened.oku(self.item)["status"], "kesildi")
        self.assertEqual(reopened.durum()["daily_used"], 1)
        self.assertEqual(self.calls, [])

    def test_isci_baslayamazsa_calisiyor_durumunda_takilmaz(self):
        with patch("radar.zeka.threading.Thread.start", side_effect=RuntimeError("SECRET_FAILURE")):
            with self.assertRaises(zeka.ZekaHazirDegil):
                self.manager.baslat(self.item)
        self.assertEqual(self.manager.oku(self.item)["status"], "hata")
        self.assertEqual(self.manager.durum()["daily_used"], 1)
        self.assertFalse(self.manager.durum()["running"])

    def test_kimlik_yokken_ve_kapandiktan_sonra_model_cagrilmaz(self):
        self.manager.credential_provider = lambda: None
        self.assertFalse(self.manager.durum()["configured"])
        with self.assertRaises(zeka.ZekaHazirDegil):
            self.manager.baslat(self.item)
        self.assertEqual(self.manager.durum()["daily_used"], 0)
        self.manager.kapan()
        with self.assertRaises(zeka.ZekaMesgul):
            self.manager.baslat(self.item)
        self.assertEqual(self.calls, [])

    def test_on_inceleme_profil_karar_ve_sinirlarla_kalici_saklanir(self):
        profile = {"project_name": "Özel asistan projesi", "summary": "Türkçe yerel ses asistanı.",
                   "environment": ["Windows", "RTX 3070"], "constraints": ["İnsan onayı gerekli."],
                   "priorities": ["Sesli kullanım"]}
        self.manager.baslat(self.item, "screen", profile=profile)
        self.finish()
        saved = self.manager.oku(self.item, "screen")
        self.assertEqual(saved["status"], "hazir")
        self.assertEqual(saved["result"]["relevance_score"], 82)
        self.assertEqual(saved["result"]["fit"], "high")
        self.assertIn("Proje uygunluğu", saved["text"])
        self.assertEqual(saved["profile"], profile)
        self.assertEqual(saved["profile_hash"], zeka.profil_ozeti(profile))
        sent = json.loads(self.production_call()[0][1]["content"])
        self.assertEqual(set(sent["local_requirements"]), {"project_name", "summary"})
        self.assertEqual(sent["local_requirements"]["project_name"], "Genel ilgi alanları")
        self.assertNotEqual(sent["local_requirements"]["summary"], profile["summary"])
        self.assertNotIn("Özel asistan projesi", sent["sources"][0]["public_text"])
        self.assertIn("not a publication or verified evidence", self.production_call()[0][0]["content"])
        self.assertEqual(self.production_call()[1]["max_tokens"], 2400)
        self.manager.kapan()
        reopened = self.make_manager()
        self.assertEqual(reopened.oku(self.item, "screen")["profile_hash"], saved["profile_hash"])
        self.assertEqual(reopened.durum()["daily_used"], 2)

    def test_tek_kaynakla_derin_arastirma_acik_sinir_ve_plan_uretir(self):
        self.manager.baslat(self.item, "deep")
        self.finish()
        saved = self.manager.oku(self.item, "deep")
        self.assertEqual(saved["status"], "hazir")
        self.assertEqual(len(saved["sources"]), 1)
        self.assertIn(zeka.TEK_KAYNAK_SINIRI, saved["result"]["limitations"])
        self.assertIn("Doğrulama planı", saved["text"])
        self.assertIn("İnsan onayından", saved["text"])
        self.assertEqual(self.calls[-1][1]["max_tokens"], 4800)
        self.assertIn("only ONE source", self.production_call()[0][0]["content"])
        self.assertEqual(json.loads(self.production_call()[0][1]["content"])["local_requirements"], {})
        with self.assertRaises(zeka.ZekaHazirDegil):
            self.manager.baslat(self.item, "research")

    def test_yeni_turler_birbirini_bekletmez_ayni_tur_bes_dakika_bekler(self):
        self.manager.baslat(self.item, "screen")
        self.finish()
        self.assertEqual(self.manager.oku(self.item, "deep")["retry_after_seconds"], 0)
        self.manager.baslat(self.item, "deep")
        self.finish()
        self.assertEqual(self.manager.durum()["daily_used"], 4)
        for kind in ("screen", "deep"):
            with self.subTest(kind=kind), self.assertRaises(zeka.ZekaSiniri) as error:
                self.manager.baslat(self.item, kind)
            self.assertEqual(error.exception.retry_after_seconds, 300)
            self.assertEqual(self.manager.oku(self.item, kind)["retry_after_seconds"], 300)
        self.now += timedelta(minutes=5)
        self.manager.baslat(self.item, "screen")
        self.finish()
        self.assertEqual(self.manager.durum()["daily_used"], 6)

    def test_ayarlanabilir_gunluk_sinir_basarisiz_deneme_ve_yeniden_acilista_korunur(self):
        self.manager.kapan()
        manager = self.make_manager(daily_limit=2, provider=Mock(side_effect=RuntimeError("Sahte hata")))
        for kind in ("screen", "deep"):
            manager.baslat(self.item, kind)
            self.finish(manager)
        with self.assertRaisesRegex(zeka.ZekaSiniri, "Günlük 2"):
            manager.baslat(self.item, "summary")
        self.assertEqual(manager.durum()["daily_limit"], 2)
        self.assertEqual(manager.durum()["daily_remaining"], 0)
        manager.kapan()
        reopened = self.make_manager(daily_limit=80)
        self.assertEqual(reopened.durum()["daily_used"], 2)
        self.assertEqual(reopened.durum()["daily_remaining"], 78)
        reopened.kapan()
        for invalid in (True, 0, -1, 20.5, "80", 1001):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.make_manager(daily_limit=invalid)

    def test_kaynak_adaylari_ag_butce_ve_kimlik_okumadan_bes_yayinciyla_sinirlidir(self):
        for number in range(1, 7):
            self.other(number)
        self.manager.credential_provider = Mock(side_effect=AssertionError("Kimlik okunmamalı"))
        candidates = self.manager.kaynak_adaylari(self.item)
        self.assertEqual(len(candidates), 4)
        self.assertNotIn(self.item, candidates)
        self.manager.credential_provider.assert_not_called()
        self.assertEqual(self.calls, [])
        with self.depo.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ai_attempts").fetchone()[0], 0)
        self.manager.credential_provider = lambda: "FAKE-TEST-SECRET"
        self.manager.baslat(self.item, "deep")
        self.finish()
        saved = self.manager.oku(self.item, "deep")
        self.assertEqual([source["item_id"] for source in saved["sources"]][1:], candidates)
        self.assertEqual(len(saved["sources"]), 5)
        sent = json.loads(self.production_call()[0][1]["content"])["sources"]
        self.assertTrue(all(len(source["public_text"]) <= 6000 for source in sent))

    def test_gecersiz_profil_model_denemesine_sayilmaz_ve_eski_profil_hatada_korunur(self):
        for profile in ({"notes": "PRIVATE_NOTE"}, {"summary": "api_key=FAKE_SECRET"}):
            with self.assertRaises(ValueError):
                self.manager.baslat(self.item, "screen", profile=profile)
        self.assertEqual(self.manager.durum()["daily_used"], 0)
        self.assertEqual(self.calls, [])
        profile = {"project_name": "İlk proje"}
        self.manager.baslat(self.item, "screen", profile=profile)
        self.finish()
        self.now += timedelta(minutes=5)
        self.manager.provider = Mock(side_effect=RuntimeError("FAKE_SECRET"))
        self.manager.baslat(self.item, "screen", profile={"project_name": "Başka proje"})
        self.finish()
        saved = self.manager.oku(self.item, "screen")
        self.assertEqual(saved["status"], "hata")
        self.assertEqual(saved["profile"], profile)
        self.assertEqual(saved["profile_hash"], zeka.profil_ozeti(profile))

    def test_yeni_isler_ozel_notlari_gondermez_ve_govde_degismesi_sonucu_eskitir(self):
        self.article()
        self.depo.geri_bildirim(self.item, "yararli", "PRIVATE_FEEDBACK_NOTE")
        for kind in ("screen", "deep"):
            self.manager.baslat(self.item, kind, profile={"project_name": "Açık proje tanımı"})
            self.finish()
            saved = self.manager.oku(self.item, kind)
            self.assertEqual(saved["status"], "hazir")
            self.assertFalse(saved["stale"])
            self.assertNotIn("PRIVATE_FEEDBACK_NOTE", str(self.calls[-1][0]))
        self.article(body="Changed public article body about local voice processing " * 20)
        for kind in ("screen", "deep"):
            self.assertTrue(self.manager.oku(self.item, kind)["stale"])

    def test_eski_sonuc_tablosu_profil_gocu_ile_sonucunu_kaybetmez(self):
        self.manager.baslat(self.item)
        self.finish()
        saved = self.manager.oku(self.item)
        self.manager.kapan()
        with self.depo.baglanti(write=True) as connection:
            connection.execute("ALTER TABLE ai_results DROP COLUMN profile_json")
            connection.execute("ALTER TABLE ai_results DROP COLUMN profile_hash")
        reopened = self.make_manager()
        migrated = reopened.oku(self.item)
        self.assertEqual(migrated["result"], saved["result"])
        self.assertEqual(migrated["profile"], {})
        self.assertIsNone(migrated["profile_hash"])
        self.assertEqual(reopened.durum()["daily_used"], 2)

    def test_yeni_isin_turkce_ve_kaynak_dogrulamasi_hatasi_kalici_butcede_sayilir(self):
        for kind, change in (("screen", {"relevance_reason_tr": "This model provides automated voice processing and improves performance."}),
                             ("deep", {"evidence": [{"item_id": self.item, "quote": "Yalnız proje profilinde bulunan özel gereksinim."}]})):
            def invalid_provider(messages, **kwargs):
                payload = json.loads(messages[1]["content"])
                return answer(payload["sources"], kind=kind, **change)
            self.manager.provider = invalid_provider
            self.manager.baslat(self.item, kind, profile={"summary": "Yalnız proje profilinde bulunan özel gereksinim."})
            self.finish()
            saved = self.manager.oku(self.item, kind)
            self.assertEqual(saved["status"], "hata")
            self.assertFalse(saved["has_result"])
            self.assertNotIn("This model", saved["last_error"])
        self.assertEqual(self.manager.durum()["daily_used"], 2)

    def test_calisan_is_farkli_profil_istegini_sessizce_yutmaz(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def blocked(messages, **kwargs):
            started.set()
            release.wait(timeout=5)
            return self.provider(messages, **kwargs)
        self.manager.provider = blocked
        profile = {"project_name": "Proje A"}
        self.manager.baslat(self.item, "screen", profile=profile)
        self.assertTrue(started.wait(timeout=2))
        self.assertFalse(self.manager.baslat(self.item, "screen", profile={"project_name": " Proje  A "})["started"])
        with self.assertRaises(zeka.ZekaMesgul):
            self.manager.baslat(self.item, "screen", profile={"project_name": "Proje B"})
        profile["project_name"] = "Sonradan değiştirilen proje"
        release.set()
        self.finish()
        self.assertEqual(self.manager.oku(self.item, "screen")["profile"], {"project_name": "Proje A"})
        self.assertEqual(self.manager.durum()["daily_used"], 2)

    def test_ilgi_ve_beyin_isleri_numarali_alintiyi_ayni_kaynak_metine_cozer(self):
        self.article(body='A public source describes reliable local speech agents and memory methods in detail.\n'
                          'Another public paragraph explains how an engineer can check the original hardware requirements.')
        captured = []
        def numbered_provider(messages, **kwargs):
            payload = json.loads(messages[1]['content'])
            if payload['kind'] == 'verify':
                return uncertain_audit(payload)
            value = json.loads(answer(payload['sources'], kind=payload['kind']))
            value['evidence'] = [{'item_id': source['item_id'], 'quote_id': source['quote_options'][0]['id']}
                                 for source in payload['sources']]
            captured.append(payload)
            return json.dumps(value)
        self.manager.provider = numbered_provider
        for kind in ('interest', 'screen', 'review'):
            self.manager.baslat(self.item, kind, profile={'project_name': 'PRIVATE_PROJECT_482'})
            self.finish()
            saved = self.manager.oku(self.item, kind)
            self.assertEqual(saved['status'], 'hazir', saved['last_error'])
            self.assertEqual(saved['result']['evidence'], [{'item_id': self.item, 'quote': captured[-1]['sources'][0]['quote_options'][0]['quote']}])
            self.assertEqual(captured[-1]['local_requirements']['project_name'], 'PRIVATE_PROJECT_482' if kind == 'review' else 'Genel ilgi alanları')
            self.assertEqual(set(captured[-1]['local_requirements']),
                             {'project_name'} if kind == 'review' else {'project_name', 'summary'})
            if kind == 'review':
                self.assertEqual(saved['result']['research_decision'], 'research')
                self.assertIn('Derin araştır', saved['text'])

    def test_nvidia_yalniz_genel_ilgiyi_alir_beyin_tam_dogrulanmis_profili_alir(self):
        self.manager.kapan()
        nvidia_calls, brain_calls = [], []

        def capture(target):
            def provider(messages, **kwargs):
                payload = json.loads(messages[1]['content'])
                target.append(payload)
                if payload['kind'] == 'verify':
                    return uncertain_audit(payload)
                return answer(payload['sources'], payload['kind'])
            return provider

        manager = self.make_manager(provider=capture(nvidia_calls), deep_provider=capture(brain_calls),
                                    deep_available=lambda: True)
        second = self.other()
        for item_id, name in ((self.item, 'PRIVATE_PROJECT_482'), (second, 'Yalnız yerelde kalan proje adı')):
            profile = {'project_name': name, 'summary': 'LOCAL_SUMMARY_741',
                       'environment': ['LOCAL_MACHINE_852'], 'constraints': ['LOCAL_CONSTRAINT_963'],
                       'priorities': ['LOCAL_PRIORITY_159']}
            for kind in ('interest', 'screen', 'review', 'deep'):
                with self.subTest(name=name, kind=kind):
                    manager.baslat(item_id, kind, profile=profile)
                    self.finish(manager)
                    saved = manager.oku(item_id, kind)
                    self.assertEqual(saved['status'], 'hazir', saved['last_error'])
                    self.assertEqual(saved['profile'], profile)
                    self.assertEqual(saved['profile_hash'], zeka.profil_ozeti(profile))
                    if kind in ('interest', 'screen'):
                        sent = nvidia_calls[-2]
                        self.assertEqual(set(sent['local_requirements']), {'project_name', 'summary'})
                        self.assertEqual(sent['local_requirements']['project_name'], 'Genel ilgi alanları')
                        serialized = json.dumps(nvidia_calls[-2:], ensure_ascii=False)
                        for value in (profile['summary'], *profile['environment'], *profile['constraints'], *profile['priorities']):
                            self.assertNotIn(value, serialized)
                        self.assertNotIn(name, serialized)
                    else:
                        self.assertEqual(brain_calls[-2]['local_requirements'], profile)
                        if kind == 'review':
                            self.assertEqual(brain_calls[-2]['preliminary_assessment'], manager.oku(item_id, 'screen')['result'])
        self.assertEqual([call['kind'] for call in nvidia_calls], ['interest', 'verify', 'screen', 'verify'] * 2)
        self.assertEqual([call['kind'] for call in brain_calls], ['review', 'verify', 'deep', 'verify'] * 2)


class ZekaYanitTestleri(unittest.TestCase):
    def setUp(self):
        self.sources = [{"item_id": "one", "public_text": "A public source describes reliable local speech agents and memory methods in detail."}]

    def test_bosluklar_normallesir_ama_uydurulan_alinti_reddedilir(self):
        raw = answer(self.sources, evidence=[{"item_id": "one", "quote": "A public\nsource describes   reliable local speech agents"}])
        result = zeka.sonucu_dogrula(raw, self.sources)
        self.assertEqual(result["evidence"][0]["quote"], "A public source describes reliable local speech agents")
        for change in ({"evidence": [{"item_id": "unknown", "quote": self.sources[0]["public_text"]}]},
                       {"evidence": [{"item_id": "one", "quote": "The model invents this unsupported statement."}]},
                       {"evidence": []}, {"title_tr": "x" * 221}, {"limitations": []},
                       {"evidence": [{"item_id": "one", "quote": self.sources[0]["public_text"], "url": "https://evil.example"}]}):
            with self.subTest(change=change), self.assertRaises(zeka.ZekaYanitHatasi):
                zeka.sonucu_dogrula(answer(self.sources, **change), self.sources)

    def test_json_disindaki_ve_yinelenen_alanlar_kabul_edilmez(self):
        for value in ("```json\n" + answer(self.sources) + "\n```", '{"title_tr":"first","title_tr":"second"}',
                      "[]", "x" * (zeka.EN_COK_YANIT + 1)):
            with self.subTest(value=value[:30]), self.assertRaises(zeka.ZekaYanitHatasi):
                zeka.sonucu_dogrula(value, self.sources)
        with self.assertRaises(zeka.ZekaYanitHatasi):
            zeka.sonucu_dogrula(answer(self.sources), self.sources, "research")

    def test_turkce_alanlardaki_cjk_karismasi_reddedilir_ozgun_alinti_korunur(self):
        changes = [{"title_tr": "Sera 園藝 projesi"},
                   {"summary_tr": "Bu çalışma sera園藝ciliğinin iklim hedeflerine yönelik bir araştırma projesini anlatıyor."},
                   {"benefit_tr": "Serada センサー kullanımıyla veri toplanabilir."},
                   {"limitations": ["Kaynak metin 실험 sonuçlarını açıklamıyor."]},
                   {"next_steps": ["Kaynak metindeki 项目 ayrıntılarını inceleyin."]}]
        for change in changes:
            with self.subTest(field=next(iter(change))), self.assertRaises(zeka.ZekaYanitHatasi):
                zeka.sonucu_dogrula(answer(self.sources, **change), self.sources)
        sources = [{"item_id": "one", "public_text": "Original source text uses 園藝 to describe horticulture in its own language."}]
        result = zeka.sonucu_dogrula(answer(sources), sources)
        self.assertIn("園藝", result["evidence"][0]["quote"])

    def test_tek_kaynak_siniri_hicbir_sinirlamayi_silmeden_yeniden_dogrulanabilir(self):
        limitations = [f"Kaynakta belirtilmeyen {number}. koşul ayrıca incelenmeli." for number in range(6)]
        result = zeka.sonucu_dogrula(answer(self.sources, kind="deep", limitations=limitations), self.sources, "deep")
        self.assertEqual(result["limitations"][:-1], limitations)
        self.assertEqual(result["limitations"][-1], zeka.TEK_KAYNAK_SINIRI)
        raw_report = {key: value for key, value in result.items() if key != 'evidence_quality'}
        self.assertEqual(zeka.sonucu_dogrula(json.dumps(raw_report), self.sources, "deep"), result)
        with self.assertRaises(zeka.ZekaYanitHatasi):
            zeka.sonucu_dogrula(answer(self.sources, kind='deep', evidence_quality={'status': 'verified'}), self.sources, 'deep')
        for kind in ("screen", "deep"):
            for change in ({"evidence": [{"item_id": "unknown", "quote": self.sources[0]["public_text"]}]},
                           {"evidence": []}, {"unexpected": "alan"}):
                with self.subTest(kind=kind, change=change), self.assertRaises(zeka.ZekaYanitHatasi):
                    zeka.sonucu_dogrula(answer(self.sources, kind=kind, **change), self.sources, kind)

    def test_alinti_numarasi_kaynaga_baglidir_ve_metni_model_yazamaz(self):
        quote = 'A public source describes reliable local speech agents and memory methods in detail.'
        sources = [{'item_id': 'one', 'public_text': 'Paper title\n' + quote}]
        for kind in ('interest', 'screen', 'review', 'deep'):
            value = zeka.sonucu_dogrula(answer(sources, kind=kind, evidence=[{'item_id': 'one', 'quote_id': 1}]), sources, kind)
            self.assertEqual(value['evidence'], [{'item_id': 'one', 'quote': quote}])
            for entry in ({'item_id': 'one', 'quote_id': True}, {'item_id': 'one', 'quote_id': 0},
                          {'item_id': 'one', 'quote_id': -1}, {'item_id': 'one', 'quote_id': 99},
                          {'item_id': 'one', 'quote_id': '1'}, {'item_id': 'one', 'quote_id': 1.0},
                          {'item_id': 'unknown', 'quote_id': 1},
                          {'item_id': 'one', 'quote_id': 1, 'quote': 'Model bunu değiştiremez.'}):
                with self.subTest(kind=kind, entry=entry), self.assertRaises(zeka.ZekaYanitHatasi):
                    zeka.sonucu_dogrula(answer(sources, kind=kind, evidence=[entry]), sources, kind)
        sources.append({'item_id': 'two', 'public_text': 'Second title\nThis independent article documents different public speech experiments and device requirements.'})
        result = zeka.sonucu_dogrula(answer(sources, kind='review', evidence=[{'item_id': source['item_id'], 'quote_id': 1} for source in sources]), sources, 'review')
        self.assertNotEqual(result['evidence'][0]['quote'], result['evidence'][1]['quote'])

    def test_beyin_karari_zorunlu_ve_dis_kaynak_yalniz_derinde_istege_baglidir(self):
        reviewed = json.loads(answer(self.sources, kind='review'))
        reviewed.pop('research_decision')
        with self.assertRaises(zeka.ZekaYanitHatasi):
            zeka.sonucu_dogrula(json.dumps(reviewed), self.sources, 'review')
        with self.assertRaises(zeka.ZekaYanitHatasi):
            zeka.sonucu_dogrula(answer(self.sources, kind='review', research_decision='develop'), self.sources, 'review')
        external = [{'url': 'https://docs.example.org/paper', 'title': 'Original public article',
                     'summary_tr': 'Bu kaynak, yöntem ve cihaz gereksinimleri hakkında ek bilgi veriyor.'}]
        result = zeka.sonucu_dogrula(answer(self.sources, kind='deep', external_sources=external), self.sources, 'deep')
        self.assertEqual(result['external_sources'], external)
        with self.assertRaises(zeka.ZekaYanitHatasi):
            zeka.sonucu_dogrula(answer(self.sources, kind='screen', external_sources=external), self.sources, 'screen')

    def test_kimlik_onceligi_ve_windows_kasasi_anahtari_yazdirilmaz(self):
        vault = Mock(return_value=Mock(get_password=Mock(return_value="FAKE-VAULT-KEY")))
        windows = types.ModuleType("keyring.backends.Windows")
        windows.WinVaultKeyring = vault
        with patch.dict(sys.modules, {"keyring.backends.Windows": windows}), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(zeka.kimlik_bilgisi(), "FAKE-VAULT-KEY")
            vault.return_value.get_password.assert_called_once_with("ufuk-nvidia", "api-key")
            os.environ["NVIDIA_API_KEY"] = "FAKE-NVIDIA-ENV"
            self.assertEqual(zeka.kimlik_bilgisi(), "FAKE-NVIDIA-ENV")
            os.environ["UFUK_NVIDIA_API_KEY"] = "FAKE-UFUK-ENV"
            self.assertEqual(zeka.kimlik_bilgisi(), "FAKE-UFUK-ENV")
            self.assertEqual(vault.call_count, 1)

    def test_http_sabit_endpoint_tls_sinirlar_ve_aracsiz_istek(self):
        body = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": answer(self.sources)}}]}).encode()
        response = Mock(status_code=200, headers={"content-length": str(len(body))})
        response.iter_raw.return_value = iter([body])
        stream = Mock()
        stream.__enter__ = Mock(return_value=response)
        stream.__exit__ = Mock(return_value=None)
        client = Mock()
        client.stream.return_value = stream
        context = Mock()
        context.__enter__ = Mock(return_value=client)
        context.__exit__ = Mock(return_value=None)
        with patch("radar.zeka.httpx.Client", return_value=context) as factory:
            value = zeka.nvidia_yaniti([{"role": "user", "content": "public"}], api_key="FAKE-HTTP-SECRET")
        self.assertEqual(value, answer(self.sources))
        settings = factory.call_args.kwargs
        self.assertIsInstance(settings["verify"], ssl.SSLContext)
        self.assertEqual(settings["verify"].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(settings["verify"].check_hostname)
        self.assertFalse(settings["follow_redirects"])
        self.assertFalse(settings["trust_env"])
        self.assertEqual(settings["timeout"].read, zeka.TOPLAM_SURE)
        args, kwargs = client.stream.call_args
        self.assertEqual(args, ("POST", zeka.ENDPOINT))
        self.assertEqual(kwargs["json"]["model"], zeka.MODEL)
        self.assertEqual(kwargs["json"]["chat_template_kwargs"], {"enable_thinking": False})
        self.assertLessEqual(kwargs["json"]["max_tokens"], 3200)
        self.assertNotIn("tools", kwargs["json"])
        self.assertNotIn("FAKE-HTTP-SECRET", json.dumps(kwargs["json"]))

    def test_http_hata_yonlendirme_eksik_ve_buyuk_yanitlari_reddeder(self):
        valid = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": answer(self.sources)}}]}).encode()
        for status, headers, parts in [
            (401, {}, [b"FAKE-SECRET-IN-HTTP-BODY"]),
            (302, {"location": "https://another.example/"}, []),
            (200, {"content-encoding": "gzip"}, [b"compressed"]),
            (200, {"content-length": str(zeka.EN_COK_YANIT + 1)}, []),
            (200, {}, [b"x" * (zeka.EN_COK_YANIT + 1)]),
            (200, {"content-length": str(len(valid) + 3)}, [valid]),
            (200, {}, [b'{"choices":[{"finish_reason":"length","message":{"content":"truncated"}}]}']),
            (200, {}, [b'{"choices":[{"finish_reason":"stop","message":{"content":"{}","tool_calls":[{}]}}]}']),
        ]:
            response = Mock(status_code=status, headers=headers)
            response.iter_raw.return_value = iter(parts)
            stream = Mock(__enter__=Mock(return_value=response), __exit__=Mock(return_value=None))
            client = Mock(stream=Mock(return_value=stream))
            context = Mock(__enter__=Mock(return_value=client), __exit__=Mock(return_value=None))
            with self.subTest(status=status, headers=headers), patch("radar.zeka.httpx.Client", return_value=context):
                with self.assertRaises(zeka.ZekaYanitHatasi) as error:
                    zeka.nvidia_yaniti([], api_key="FAKE-HTTP-SECRET")
                self.assertNotIn("FAKE", str(error.exception))
                self.assertEqual(client.stream.call_count, 1)

    def test_http_akisi_toplam_sureyi_gecerse_kesilir(self):
        response = Mock(status_code=200, headers={})
        response.iter_raw.return_value = iter([b"{}"])
        stream = Mock(__enter__=Mock(return_value=response), __exit__=Mock(return_value=None))
        client = Mock(stream=Mock(return_value=stream))
        context = Mock(__enter__=Mock(return_value=client), __exit__=Mock(return_value=None))
        with patch("radar.zeka.httpx.Client", return_value=context), \
                patch("radar.zeka.time.monotonic", side_effect=[0, zeka.TOPLAM_SURE + 1]):
            with self.assertRaises(zeka.ZekaZamanAsimi):
                zeka.nvidia_yaniti([], api_key="FAKE-HTTP-SECRET")

    def test_http_zaman_asimi_ozel_ayrinti_tasimayan_sabit_hata_verir(self):
        client = Mock(stream=Mock(side_effect=zeka.httpx.ReadTimeout('FAKE_SECRET Authorization details')))
        context = Mock(__enter__=Mock(return_value=client), __exit__=Mock(return_value=None))
        with patch('radar.zeka.httpx.Client', return_value=context):
            with self.assertRaises(zeka.ZekaZamanAsimi) as error:
                zeka.nvidia_yaniti([], api_key='FAKE-HTTP-SECRET')
        self.assertNotIn('FAKE', str(error.exception))
        self.assertIn('süre', str(error.exception))
        self.assertEqual(client.stream.call_count, 1)


if __name__ == "__main__":
    unittest.main()
