"""Proje bağlamının sınırlarını ve Türkçe karar alanlarını dış bağlantı olmadan denetler."""

import unittest

from radar.arastirma_icerigi import dis_kaynaklari_dogrula, ek_istem, ek_sonucu_dogrula, profil_dogrula, profil_ozeti, turkce_metin


class ArastirmaIcerigiTestleri(unittest.TestCase):
    def test_profil_sinirli_kopya_ve_kararli_ozet_uretir(self):
        source = {"project_name": "  Örnek   Proje ", "summary": "Yerel ses asistanı.",
                  "environment": ["Windows", "RTX 3070"], "constraints": ["Sulama sunucuya bağımlı olamaz."],
                  "priorities": ["Türkçe ses", "İnsan onayı"]}
        result = profil_dogrula(source)
        self.assertEqual(result["project_name"], "Örnek Proje")
        self.assertEqual(profil_ozeti(source), profil_ozeti(dict(reversed(list(result.items())))))
        source["environment"].append("Sonradan değişen alan")
        self.assertEqual(result["environment"], ["Windows", "RTX 3070"])
        self.assertEqual(profil_dogrula(None), {})
        self.assertNotEqual(profil_ozeti(result), profil_ozeti({}))

    def test_profil_ek_alan_uzunluk_tur_ve_belirgin_sirlari_reddeder(self):
        for value in (["proje"], {"notes": "özel not"}, {"summary": None}, {"project_name": "x" * 121},
                      {"environment": ["Windows"] * 11}, {"constraints": ["x" * 241]}, {"summary": "a\nb"},
                      {"summary": "api_key=FAKE_VALUE"}, {"summary": "Bearer FAKE_TOKEN"},
                      {"summary": "nvapi-FAKELONGTOKEN12345"}, {"summary": "https://user:pass@example.org/"},
                      {"summary": "Bearer TEST_TOKEN"}, {"priorities": [False]}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                profil_dogrula(value)
        self.assertEqual(profil_dogrula({"constraints": ["API anahtarı kasada tutulmalı."]})["constraints"],
                         ["API anahtarı kasada tutulmalı."])

    def test_ingilizce_cumle_reddedilir_urun_adi_ve_ascii_turkce_korunur(self):
        for value in ("This model provides faster speech processing with improved memory usage.",
                      "Local voice agents and memory methods", "Piper improves local speech processing.",
                      "The source describes a useful feature. Bu uygulama iyidir."):
            with self.subTest(value=value), self.assertRaises(ValueError):
                turkce_metin(value, 5, 500)
        for value in ("Home Assistant ve Piper ile yerel ses işleme", "Kaynak, NVIDIA Nemotron modelini anlatiyor.",
                      "Piper ses çıktısını düzenleyen bir araçtır.", "OpenAI GPT-5 için yerel doğrulama gerekir."):
            self.assertEqual(turkce_metin(value, 5, 500), value)

    def test_yeni_alanlar_sinirli_ve_sabit_seceneklidir(self):
        screen = {"relevance_score": 75, "relevance_reason_tr": "Yerel ses işleme önceliğiyle ilgili olabilir.",
                  "fit": "possible", "blockers": []}
        self.assertEqual(ek_sonucu_dogrula(screen, "screen"), screen)
        for change in ({"relevance_score": True}, {"relevance_score": 101}, {"relevance_score": 2.5},
                       {"fit": "definitely"}, {"blockers": ["kısa"]}, {"blockers": ["Kaynak ayrıntısı eksik."] * 7},
                       {"relevance_reason_tr": "This model provides strong support for automated speech processing."}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                ek_sonucu_dogrula({**screen, **change}, "screen")
        deep = {"recommendation": "izle", "compatibility_tr": "Yerel cihazla uyumu henüz doğrulanmadı.",
                **{field: ["Kaynak ve yerel gereksinimler birlikte incelenmeli."]
                   for field in ("requirements", "integration_steps", "verification_plan", "open_questions")}}
        self.assertEqual(ek_sonucu_dogrula(deep, "deep"), deep)
        for change in ({"recommendation": "deploy_now"}, {"requirements": []}, {"verification_plan": ["x" * 601]},
                       {"integration_steps": ["Yerel kaynak incelenmeli."] * 9}, {"open_questions": "Liste değil"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                ek_sonucu_dogrula({**deep, **change}, "deep")

    def test_ilgi_ve_beyin_karari_uygunlugu_onay_ve_gelistirmeden_ayirir(self):
        base = {'relevance_score': 15, 'relevance_reason_tr': 'Bu tarihsel gösterim yerel ses asistanıyla doğrudan ilgili görünmüyor.',
                'fit': 'low', 'blockers': []}
        self.assertEqual(ek_sonucu_dogrula(base, 'interest'), base)
        for decision in ('research', 'watch', 'dismiss'):
            reviewed = {**base, 'research_decision': decision}
            self.assertEqual(ek_sonucu_dogrula(reviewed, 'review'), reviewed)
        with self.assertRaises(ValueError):
            ek_sonucu_dogrula({**base, 'research_decision': 'deploy'}, 'review')
        for kind in ('interest', 'screen', 'review'):
            prompt = ek_istem(kind, 1)
            self.assertIn('blockers must be EMPTY unless', prompt)
            self.assertIn('not invent a technical blocker', prompt)
        self.assertIn('not human approval', ek_istem('review', 1))

    def test_dis_kaynak_https_tam_domain_ve_sinirli_alanlarla_sinirlidir(self):
        source = {'url': 'https://docs.example.org/reference', 'title': 'Original English title',
                  'summary_tr': 'Bu kaynak, cihaz gereksinimleri ve yöntem hakkında ek bilgi veriyor.'}
        self.assertEqual(dis_kaynaklari_dogrula([source]), [source])
        self.assertEqual(dis_kaynaklari_dogrula([]), [])
        for url in ('http://example.org/a', 'https://127.0.0.1/a', 'https://[::1]/a', 'https://8.8.8.8/a',
                    'https://user:pass@example.org/a', 'https://user@example.org/a', 'https://localhost/a',
                    'https://private.local/a', 'https://private.internal/a', 'https://example.org:8443/a',
                    'https://example.org:bad/a', 'https://example.org\\@localhost/a', 'https://exam ple.org/a',
                    'https://-example.org/a', 'https://example.org./a', 'file:///etc/passwd'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                dis_kaynaklari_dogrula([{**source, 'url': url}])
        for value in ([source] * 6, [source, source], [{**source, 'verified': True}], [{**source, 'title': 'x' * 241}],
                      [{**source, 'summary_tr': 'This article provides new research and improves local voice processing.'}]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                dis_kaynaklari_dogrula(value)


if __name__ == "__main__":
    unittest.main()
