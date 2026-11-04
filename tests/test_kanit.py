"""Ayrı anlamsal denetimin kaynak konumlarını, çelişkileri ve sağlayıcı bütçesini gerçek ağa çıkmadan sınar."""

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".deps"))

from radar import kanit, zeka, derin_arastirma
from radar.depo import Depo


def report(source, kind="summary"):
    value = {"title_tr": "Piper ses hızında ölçülen gelişme",
             "summary_tr": "Kaynakta anlatılan Piper sürümü, ses üretiminde yüzde 50 hızlanma sağlamıştır.",
             "benefit_tr": "Yerel ses deneyimini değerlendirmek için kullanılabilir.",
             "limitations": ["Yerel cihazdaki sonuçlar ayrıca incelenmeli."],
             "next_steps": ["Ayrı bir ortamda süre ölçümü yapılmalı."],
             "evidence": [{"item_id": source["item_id"], "quote_id": 1}]}
    if kind == "deep":
        value.update(recommendation="denemeye_deger", compatibility_tr="Yerel ses koşullarındaki çalışma ayrıca incelenmeli.",
                     requirements=["Cihazın ses çıkışı için sürücü koşulları incelenmeli."],
                     integration_steps=["Ayrı deneme ortamındaki ses kalitesi karşılaştırılmalı."],
                     verification_plan=["Aynı örneklerle ses üretim süreleri karşılaştırılmalı."],
                     open_questions=["Donanımımızdaki bellek tüketimi ne kadar olacak?"])
    return value


def verdict(payload, status="supported"):
    return json.dumps({"coverage_complete": True, "claims": [
        {"id": claim["id"], "status": status, "reason_tr": "Bu iddia verilen kaynak pasajına göre değerlendirildi.",
         "evidence": [payload["passages"][0]["passage_id"]] if status in {"supported", "contradicted"} else []}
        for claim in payload["claims"]]}, ensure_ascii=False)


class KanitSozlesmeTestleri(unittest.TestCase):
    def setUp(self):
        self.source = {"item_id": "piper", "url": "https://example.org/piper", "source_revision": 3,
                       "public_text": "Piper duyurusu\nBu sürüm için hız ölçümü yapılmadı; performans artışı henüz bilinmiyor."}
        self.result = report(self.source)
        self.messages, self.claims, self.passages, self.complete = kanit.denetim_istemi(self.result, [self.source])
        self.payload = json.loads(self.messages[1]["content"])

    def check(self, raw):
        return kanit.denetimi_dogrula(raw, self.claims, self.passages, model="fake-verifier",
                                      checked_at="2026-10-26T12:00:00+00:00", coverage_complete=self.complete)

    def test_birebir_alinti_sayisal_yanlis_iddiayi_kendiliginden_dogrulamaz(self):
        accepted = zeka.sonucu_dogrula(json.dumps(self.result, ensure_ascii=False), [self.source])
        self.assertEqual(accepted["evidence_quality"]["status"], "unverified")
        self.assertIn("yüzde 50", self.payload["claims"][1]["text"])
        self.assertIn("ölçümü yapılmadı", self.payload["passages"][0]["quote"])
        self.assertIn("Check entailment", self.messages[0]["content"])
        quality = self.check(verdict(self.payload, "contradicted"))
        self.assertEqual(quality["status"], "contradicted")

    def test_destekli_pasaj_modelin_alintisi_degildir_konum_ve_hash_kaynaktan_gelir(self):
        self.source["public_text"] = "Piper duyurusu\nYayıncının aynı cihaz ve ses örneğiyle ölçümünde Piper ses üretimi yüzde 50 hızlandı."
        self.messages, self.claims, self.passages, self.complete = kanit.denetim_istemi(self.result, [self.source])
        self.payload = json.loads(self.messages[1]["content"])
        quality = self.check(verdict(self.payload))
        self.assertEqual(quality["status"], "verified")
        evidence = quality["claims"][0]["evidence"][0]
        self.assertEqual(evidence["quote"], self.source["public_text"][evidence["start"]:evidence["end"]])
        self.assertEqual(evidence["text_hash"], hashlib.sha256(self.source["public_text"].encode()).hexdigest())
        self.assertEqual(evidence["source_revision"], 3)

    def test_erisim_yok_ve_belirsizlik_olumlu_hukme_donusmez(self):
        for state in ("inaccessible", "uncertain"):
            quality = self.check(verdict(self.payload, state))
            self.assertEqual(quality["status"], "unverified")
            self.assertEqual(quality["claims"][0]["evidence"], [])
            self.assertFalse(quality["coverage_complete"])

    def test_dis_yayinin_ozeti_baska_yayinin_pasajiyla_dogrulanamaz(self):
        self.result["external_sources"] = [{"url": "https://example.org/unread", "title": "Okunmayan yayın",
                                            "summary_tr": "Bu yayında ses üretimindeki hızlanmanın ölçüldüğü açıklanıyor."}]
        messages, claims, passages, complete = kanit.denetim_istemi(self.result, [self.source])
        payload = json.loads(messages[1]["content"])
        self.assertEqual(len(payload["unread_sources"]), 1)
        quality = kanit.denetimi_dogrula(verdict(payload), claims, passages, model="fake", checked_at="today",
                                        coverage_complete=complete)
        self.assertEqual(quality["claims"][-1]["status"], "inaccessible")
        self.assertEqual(quality["status"], "partial")
        self.assertFalse(quality["coverage_complete"])

    def test_uygulama_kapsam_notu_yayin_olgusu_sayilmaz_ama_sahte_kapsam_haric(self):
        result = {"summary_tr": "Yayıncının ses sürümü hakkında bilgi veriliyor.",
                  "limitations": [zeka.TEK_KAYNAK_SINIRI]}
        for count in (1, 2):
            sources = [{**self.source, "item_id": str(index)} for index in range(count)]
            messages, claims, passages, complete = kanit.denetim_istemi(result, sources)
            payload = json.loads(messages[1]["content"])
            raw = json.loads(verdict(payload))
            raw["claims"][-1].update(status="not_factual", evidence=[])
            quality = kanit.denetimi_dogrula(json.dumps(raw), claims, passages, model="fake", checked_at="today",
                                            coverage_complete=complete)
            self.assertEqual(quality["status"], "verified" if count == 1 else "partial")

    def test_gercek_kabuldeki_uretim_sessizligi_hakem_desteklese_de_dogrulanmaz(self):
        result = {"summary_tr": "Çalışmada ToolBench araçları karşılaştırıldı.",
                  "limitations": ["Gerçek zamanlı veya üretim ortamı doğrulama sonuçları rapor edilmemiştir.",
                                  "Çalışma yalnızca ToolBench veri setiyle sınırlıdır."]}
        source = {**self.source, "public_text": "Evaluating on ToolBench across single-tool and multi-tool queries, we report results."}
        messages, claims, passages, complete = kanit.denetim_istemi(result, [source])
        quality = kanit.denetimi_dogrula(verdict(json.loads(messages[1]["content"])), claims, passages,
                                        model="fake", checked_at="today", coverage_complete=complete)
        self.assertEqual([claim["status"] for claim in quality["claims"]], ["supported", "uncertain", "uncertain"])
        self.assertEqual(quality["status"], "partial")
        self.assertFalse(quality["coverage_complete"])

    def test_acik_negatif_beyan_denetcinin_destek_hukmunu_gereksiz_yere_engellemez(self):
        result = {"limitations": ["Üretim ortamı bu çalışmada değerlendirilmemiştir."]}
        source = {**self.source, "public_text": "Production deployment was not evaluated in this study."}
        messages, claims, passages, complete = kanit.denetim_istemi(result, [source])
        quality = kanit.denetimi_dogrula(verdict(json.loads(messages[1]["content"])), claims, passages,
                                        model="fake", checked_at="today", coverage_complete=complete)
        self.assertEqual(quality["status"], "verified")

    def test_kisaltma_parantez_ve_saf_oneri_gercek_kabul_regresyonu(self):
        result = {"next_steps": ["Farklı alanlarda (finans, sağlık vb.) karşılaştırma yapılması.",
                                 "Karmaşıklığa göre (ör. RtP top-1 skoru ile) yönlendirme stratejileri geliştirilmesi.",
                                 "Geri besleme mekanizmaları test edilmesi."]}
        messages, claims, passages, complete = kanit.denetim_istemi(result, [self.source])
        self.assertEqual(len(claims), 3)
        quality = kanit.denetimi_dogrula(verdict(json.loads(messages[1]["content"]), "not_factual"), claims, passages,
                                        model="fake", checked_at="today", coverage_complete=complete)
        self.assertEqual([claim["status"] for claim in quality["claims"]], ["not_factual", "uncertain", "not_factual"])
        # Sayısal/teknik öncülü olan öneri pasajsız biçimde denetimden çıkarılamaz.
        self.assertFalse(quality["coverage_complete"])

    def test_uydurma_pasaj_eksik_id_ayni_id_ve_desteksiz_olumlu_hukum_reddedilir(self):
        for change in ("missing", "duplicate", "fake_passage", "empty_support", "extra_field"):
            value = json.loads(verdict(self.payload))
            if change == "missing":
                value["claims"].pop()
            elif change == "duplicate":
                value["claims"][-1] = value["claims"][0]
            elif change == "fake_passage":
                value["claims"][0]["evidence"] = ["private-note:P1"]
            elif change == "empty_support":
                value["claims"][0]["evidence"] = []
            else:
                value["claims"][0]["quote"] = "Modelin eklediği sahte alıntı"
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.check(json.dumps(value))

    def test_not_factual_sayisal_iddia_ve_olgusal_basliktan_kacis_olamaz(self):
        value = json.loads(verdict(self.payload))
        for index in (0, 1):
            changed = json.loads(json.dumps(value))
            changed["claims"][index].update(status="not_factual", evidence=[])
            quality = self.check(json.dumps(changed))
            self.assertEqual(quality["status"], "partial")
            self.assertFalse(quality["coverage_complete"])
            self.assertEqual(quality["claims"][index]["status"], "uncertain")

    def test_gercek_soru_denetime_olgusal_iddia_diymis_gibi_sokulmaz(self):
        result = {"open_questions": ["Donanım koşulları nasıl karşılanacak?"]}
        messages, claims, passages, complete = kanit.denetim_istemi(result, [self.source])
        value = kanit.denetimi_dogrula(verdict(json.loads(messages[1]["content"]), "not_factual"), claims, passages,
                                      model="fake", checked_at="today", coverage_complete=complete)
        self.assertEqual(value["claims"][0]["status"], "not_factual")
        self.assertEqual(value["status"], "unverified")

    def test_iddia_sinirinda_kalanlari_sessizce_tam_saymaz(self):
        result = {"summary_tr": "Önemli bir kaynak iddiası açıklandı. " * 60}
        messages, claims, passages, complete = kanit.denetim_istemi(result, [self.source])
        self.assertEqual(len(claims), kanit.EN_COK_IDDIA)
        self.assertFalse(complete)
        value = kanit.denetimi_dogrula(verdict(json.loads(messages[1]["content"])), claims, passages,
                                      model="fake", checked_at="today", coverage_complete=complete)
        self.assertEqual(value["status"], "partial")

    def test_uzun_kaynagin_sonundaki_olcum_siniri_metin_butcesi_icinde_kalir(self):
        text = "Piper voice speed\n" + "Generic background context. " * 400 + "Piper voice speed: no measurement was made."
        selected = kanit.baglam_sec(text, "Piper voice speed", 6000)
        self.assertLessEqual(len(selected), 6000)
        self.assertTrue(selected.startswith("Piper voice speed"))
        self.assertIn("no measurement was made", selected)


class KanitIsAkisiTestleri(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.depo = Depo(Path(self.temp.name) / "data.sqlite3", [
            {"id": "source", "name": "Kamusal kaynak", "publisher": "Yayıncı", "topic": "ai", "interval_hours": 24,
             "benefit": "Kamusal gelişmeleri incelemek için."}])
        self.depo.kaydet("source", [{"external_id": "piper", "url": "https://example.org/piper", "title": "Piper voice speed",
                                     "excerpt": "Bu sürüm için hız ölçümü yapılmadı; performans artışı henüz bilinmiyor. " * 4,
                                     "language": "tr", "evidence_type": "duyuru"}])
        self.item = self.depo.gorunum()["items"][0]["id"]
        self.calls = []
        self.verification_status = "supported"

    def provider(self, messages, **options):
        payload = json.loads(messages[1]["content"])
        self.calls.append((payload, options))
        if payload["kind"] == "verify":
            return verdict(payload, self.verification_status)
        return json.dumps(report(payload["sources"][0], payload["kind"]), ensure_ascii=False)

    def manager(self, **options):
        instance = zeka.ZekaIsleri(self.depo, provider=self.provider, credential_provider=lambda: "FAKE_TEST_KEY",
                                  clock=lambda: datetime(2026, 10, 26, 12, tzinfo=timezone.utc), **options)
        self.addCleanup(instance.kapan)
        return instance

    def finish(self, manager, kind="summary"):
        manager.baslat(self.item, kind)
        manager.worker.join(5)
        self.assertFalse(manager.worker.is_alive())
        return manager.oku(self.item, kind)

    def test_ayri_cagri_ayri_butce_kalici_sonuc_ve_eski_kayitlara_dokunmama(self):
        manager = self.manager()
        saved = self.finish(manager)
        self.assertEqual([entry[0]["kind"] for entry in self.calls], ["summary", "verify"])
        self.assertEqual(manager.durum()["daily_used"], 2)
        self.assertEqual(saved["result"]["evidence_quality"]["status"], "verified")
        self.assertIn("public_text", self.calls[0][0]["sources"][0])
        self.assertNotIn("local_requirements", self.calls[1][0])
        with self.depo.baglanti() as connection:
            attempts = connection.execute("SELECT kind,status FROM ai_attempts ORDER BY rowid").fetchall()
        self.assertEqual([tuple(row) for row in attempts], [("summary", "hazir"), ("summary:verify", "hazir")])

    def test_uretim_sonrasi_kota_biterse_rapor_korunur_hakem_cagrilmaz(self):
        manager = self.manager(daily_limit=1)
        saved = self.finish(manager)
        self.assertEqual(saved["status"], "hazir")
        self.assertEqual(saved["result"]["evidence_quality"]["status"], "unverified")
        self.assertIn("bütçe", saved["result"]["evidence_quality"]["summary_tr"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(manager.durum()["daily_used"], 1)

    def test_olcum_yok_ama_yuzde_50_hizlandi_celiskisi_kalici_ve_olumlu_degil(self):
        self.verification_status = "contradicted"
        manager = self.manager()
        saved = self.finish(manager)
        self.assertEqual(saved["result"]["evidence_quality"]["status"], "contradicted")
        self.assertIn("yüzde 50", saved["result"]["summary_tr"])
        self.assertIn("ölçümü yapılmadı", saved["result"]["evidence"][0]["quote"])

    def test_denetcinin_ag_hatasi_raporu_silmez_ve_deneme_sayilir(self):
        original = self.provider
        def failure(messages, **options):
            if json.loads(messages[1]["content"])["kind"] == "verify":
                raise RuntimeError("SECRET must never be stored")
            return original(messages, **options)
        self.provider = failure
        manager = self.manager()
        saved = self.finish(manager)
        self.assertEqual(saved["status"], "hazir")
        self.assertEqual(saved["result"]["evidence_quality"]["status"], "unverified")
        self.assertNotIn("SECRET", json.dumps(saved))
        self.assertEqual(manager.durum()["daily_used"], 2)

    def test_derin_denetim_claude_butcesinde_nvidia_cagrisi_yok(self):
        manager = self.manager(deep_provider=self.provider, deep_available=lambda: True, deep_daily_limit=2)
        manager.provider = Mock(side_effect=AssertionError("NVIDIA must not see deep input"))
        saved = self.finish(manager, "deep")
        manager.provider.assert_not_called()
        self.assertEqual(manager.durum("deep")["daily_used"], 2)
        self.assertEqual(manager.durum()["daily_used"], 0)
        self.assertTrue(all(options["model"] == "claude-opus" for _, options in self.calls))
        self.assertIn("research_plan", saved["result"])
        self.assertEqual(saved["result"]["research_plan"], self.calls[0][0]["research_plan"])

    def test_celiskili_derin_rapor_yalniz_bir_kez_duzeltilir_iki_ek_cagri_da_sayilir(self):
        calls = []
        def correction(messages, **options):
            payload = json.loads(messages[1]["content"])
            calls.append(payload)
            if payload["kind"] == "verify":
                return verdict(payload, "contradicted" if len(calls) == 2 else "supported")
            value = report(payload["sources"][0], "deep")
            if payload["kind"] == "revise":
                value["summary_tr"] = "Kaynak, bu sürüm için hız ölçümü yapılmadığını ve performans artışının henüz bilinmediğini belirtiyor."
            return json.dumps(value, ensure_ascii=False)
        manager = self.manager(deep_provider=correction, deep_available=lambda: True, deep_daily_limit=4)
        saved = self.finish(manager, "deep")
        self.assertEqual([payload["kind"] for payload in calls], ["deep", "verify", "revise", "verify"])
        self.assertEqual(manager.durum("deep")["daily_used"], 4)
        self.assertEqual(saved["result"]["evidence_quality"]["status"], "verified")
        self.assertEqual(saved["result"]["review_history"][0]["status"], "contradicted")
        self.assertTrue(saved["result"]["follow_up"]["attempted"])
        self.assertNotIn("yüzde 50", saved["result"]["summary_tr"])

    def test_duzeltmeye_iki_cagri_kalmamissa_celiskili_rapor_saklanir(self):
        self.verification_status = "contradicted"
        manager = self.manager(deep_provider=self.provider, deep_available=lambda: True, deep_daily_limit=3)
        saved = self.finish(manager, "deep")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(manager.durum("deep")["daily_used"], 2)
        self.assertEqual(saved["result"]["evidence_quality"]["status"], "contradicted")
        self.assertFalse(saved["result"]["follow_up"]["attempted"])

    def test_bozuk_duzeltme_ilk_raporu_ve_celiski_kaydini_kaybettirmez(self):
        calls = []
        def invalid_correction(messages, **options):
            payload = json.loads(messages[1]["content"])
            calls.append(payload)
            if payload["kind"] == "verify":
                return verdict(payload, "contradicted")
            if payload["kind"] == "revise":
                return '{"unapproved_field": true}'
            return json.dumps(report(payload["sources"][0], "deep"))
        manager = self.manager(deep_provider=invalid_correction, deep_available=lambda: True, deep_daily_limit=4)
        saved = self.finish(manager, "deep")
        self.assertEqual(saved["status"], "hazir")
        self.assertEqual(saved["result"]["evidence_quality"]["status"], "contradicted")
        self.assertIn("yüzde 50", saved["result"]["summary_tr"])
        self.assertEqual(manager.durum("deep")["daily_used"], 3)
        self.assertEqual(len(calls), 3)

    def test_gercek_web_arac_alintisi_denetim_girdisine_eklenir_json_alani_reddedilir(self):
        calls = []
        def fetched(messages, **options):
            payload = json.loads(messages[1]["content"])
            calls.append(payload)
            if payload["kind"] == "verify":
                return verdict(payload, "uncertain")
            value = report(payload["sources"][0], "deep")
            return derin_arastirma.ClaudeYaniti(json.dumps(value), [{"item_id": "web:read-1",
                "url": "https://example.org/read", "public_text": "Gerçek araç çıktısındaki ölçüm koşulları ayrıca açıklanıyor.",
                "content_kind": "web_fetch_output"}])
        manager = self.manager(deep_provider=fetched, deep_available=lambda: True)
        self.finish(manager, "deep")
        passage = next(value for value in calls[-1]["passages"] if value["source_id"] == "web:read-1")
        self.assertEqual(passage["content_kind"], "web_fetch_output")
        self.assertIn("Gerçek araç çıktısı", passage["quote"])
        source = calls[0]["sources"][0]
        value = {**report(source, "deep"), "read_sources": [{"public_text": "Sahte okuma"}]}
        with self.assertRaises(zeka.ZekaYanitHatasi):
            zeka.sonucu_dogrula(json.dumps(value), [source], "deep")

    def test_eski_rapor_silinmeden_denlenmedi_gorunur(self):
        manager = self.manager()
        self.finish(manager)
        with self.depo.baglanti(write=True) as connection:
            raw = connection.execute("SELECT result_json FROM ai_results").fetchone()[0]
            old = json.loads(raw)
            old.pop("evidence_quality")
            old_raw = json.dumps(old)
            connection.execute("UPDATE ai_results SET result_json=?", (old_raw,))
        self.assertEqual(manager.oku(self.item)["result"]["evidence_quality"]["status"], "unverified")
        self.assertIn("ayrıca denetlenmedi", manager.oku(self.item)["text"])
        with self.depo.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT result_json FROM ai_results").fetchone()[0], old_raw)


if __name__ == "__main__":
    unittest.main()
