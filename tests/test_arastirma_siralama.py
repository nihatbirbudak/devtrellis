"""Sıralı aramanın teknik belirteçlerini, sınırlarını ve kısmi kapsam açıklamasını sınar."""

import time
import unittest

from radar.arastirma_siralama import (CONCEPT_GROUP_COUNT, INDEX_FIELDS, _CONCEPTS, _encode, aday_kapsami,
                                      belge_terimleri, belirtecler, eslesme_bilgisi, govde_alanlari, sorgu_hazirla,
                                      uretim_notu_mu)


class SiralamaTestleri(unittest.TestCase):
    def test_soru_baglaclari_cikar_icerik_kaybi_acik_kalir(self):
        plan = sorgu_hazirla("Why does Python need dependency checks?")
        fields = belge_terimleri({"title": "Python dependency", "summary": "", "body": "", "tags": []})
        match = eslesme_bilgisi(plan, fields)
        self.assertIn("python", match["matched_terms"])
        self.assertIn("checks", match["missing_terms"])
        self.assertFalse(match["all_terms_matched"])
        self.assertEqual(match["answer_sufficiency"], "not_assessed")

    def test_turkce_ascii_ve_yaygin_teknik_karsiliklar(self):
        packet = {"title": "Conditional request caching", "summary": "", "body": "", "tags": []}
        for query in ("koşullu istek önbellek", "KOSULLU ISTEK ONBELLEK"):
            self.assertTrue(eslesme_bilgisi(sorgu_hazirla(query), belge_terimleri(packet))["all_terms_matched"])

    def test_teknik_isaretler_baska_surumle_karismaz(self):
        fields = belge_terimleri({"title": "C++ Python 3.14 /api/v1 FTS5", "summary": "", "body": "", "tags": []})
        for query in ("C++", "3.14", "/api/v1", "FTS5"):
            plan = sorgu_hazirla(query)
            self.assertTrue(plan["required"])
            self.assertTrue(eslesme_bilgisi(plan, fields)["all_terms_matched"])
        self.assertFalse(eslesme_bilgisi(sorgu_hazirla("3.15"), fields)["all_terms_matched"])

    def test_ros_yazim_bicimi_iki_yonde_esdeger_surumu_korur(self):
        spellings = {1: ("ROS 1", "ros1"), 2: ("ROS 2", "ros2"), 12: ("ROS 12", "ros12")}
        for document_version, titles in spellings.items():
            for title in titles:
                fields = belge_terimleri({"title": title + " robot recording", "summary": "", "body": "", "tags": []})
                self.assertGreater(aday_kapsami(sorgu_hazirla("ROS robot recording"), fields), 0)
                for query_version, queries in spellings.items():
                    for query in queries:
                        with self.subTest(title=title, query=query):
                            plan = sorgu_hazirla(query + " robot recording")
                            self.assertEqual(plan["labels"][0], "ros" + str(query_version))
                            self.assertIn(0, plan["required"])
                            self.assertEqual(aday_kapsami(plan, fields) >= 0, document_version == query_version)
                            self.assertEqual(eslesme_bilgisi(plan, fields)["answer_sufficiency"], "not_assessed")

    def test_ros_normalizasyonu_diger_ad_sayi_ve_isaretleri_birlestirmez(self):
        for query, labels in (
            ("ROS 2", ("ros2",)), ("micros 2", ("micros", "2")), ("rosbag 2", ("rosbag", "2")),
            ("/ros 2", ("ros", "2")), ("ROS 2.1", ("ros", "2.1")), ("ROS 2beta", ("ros", "2beta")),
            ("Python 3.14 C++ FTS5 requires 44 pixels", ("python", "3.14", "c++", "fts5", "requires", "44", "pixels")),
        ):
            with self.subTest(query=query):
                self.assertEqual(sorgu_hazirla(query)["labels"], labels)
        fields = belge_terimleri({"title": "C++ Python 3.14 FTS5", "summary": "", "body": "", "tags": []})
        for query in ("C++", "Python 3.14", "FTS5"):
            self.assertGreater(aday_kapsami(sorgu_hazirla(query), fields), 0)
        for query in ("C#", "Python 3.13", "FTS6"):
            self.assertEqual(aday_kapsami(sorgu_hazirla(query), fields), -1)

    def test_karsilastirma_baglaci_icerik_terimi_sayilmaz(self):
        for title, left, right in (("DuckDB Polars CSV", "DuckDB", "Polars"), ("Rust Go compilation", "Rust", "Go")):
            fields = belge_terimleri({"title": title, "summary": "", "body": "", "tags": []})
            for connector in ("vs", "versus", "VS", "versus."):
                with self.subTest(title=title, connector=connector):
                    plan = sorgu_hazirla(left + " " + connector + " " + right)
                    self.assertEqual(len(plan["groups"]), 2)
                    self.assertTrue(eslesme_bilgisi(plan, fields)["all_terms_matched"])
                    self.assertGreater(aday_kapsami(plan, fields), 0)
            for connector in ("vs", "versus"):
                self.assertEqual(aday_kapsami(sorgu_hazirla(connector), fields), -1)

    def test_baglaci_cikarmak_belgede_olmayan_urun_kapsami_uretmez(self):
        fields = belge_terimleri({"title": "DuckDB Polars CSV", "summary": "", "body": "", "tags": []})
        for query in ("duckdb vs pandas", "duckdb versus pandas"):
            plan = sorgu_hazirla(query)
            self.assertEqual(plan["minimum"], 2)
            self.assertEqual(aday_kapsami(plan, fields), -1)
            self.assertEqual(eslesme_bilgisi(plan, fields)["missing_terms"], ["pandas"])

    def test_veritabani_karsiligi_baska_urun_adi_uretmiyor(self):
        fields = belge_terimleri({"title": "SQLite database indexing", "summary": "", "body": "", "tags": []})
        for query in ("veritabanı", "veri tabanı", "veritabanlarında", "DATABASES"):
            with self.subTest(query=query):
                self.assertGreater(aday_kapsami(sorgu_hazirla(query), fields), 0)
        self.assertEqual(aday_kapsami(sorgu_hazirla("PostgreSQL database indexing"), fields), -1)

    def test_urun_ve_yaziyla_surumu_kismi_eslesmede_atlamaz(self):
        examples = (
            ("ROS 2 Jazzy recording QoS", "ROS 2 Humble recording QoS"),
            ("ROS 2 Jazzy recording QoS", "ROS 2 Kilted recording QoS"),
            ("Firebase notifications delivery", "Android notifications delivery"),
            ("Python cooperative thread work without locks", "CUDA cooperative thread work without locks"),
            ("Python cooperative thread work without locks", "ROCm cooperative thread work without locks"),
            ("SQLite database indexing", "Postgres database indexing"),
        )
        for title, query in examples:
            with self.subTest(query=query):
                fields = belge_terimleri({"title": title, "summary": "", "body": "", "tags": []})
                self.assertEqual(aday_kapsami(sorgu_hazirla(query), fields), -1)
                self.assertEqual(eslesme_bilgisi(sorgu_hazirla(query), fields)["answer_sufficiency"], "not_assessed")
        fields = belge_terimleri({"title": "PostgreSQL database indexing", "summary": "", "body": "", "tags": []})
        self.assertGreater(aday_kapsami(sorgu_hazirla("Postgres database indexing"), fields), 0)

    def test_ad_eslesmesi_dislanan_kapsami_dogrulanmis_yapmaz(self):
        fields = belge_terimleri({"title": "ROS 2 Jazzy", "summary": "", "body": "Humble is not covered.", "tags": []})
        match = eslesme_bilgisi(sorgu_hazirla("ROS 2 Humble"), fields)
        self.assertTrue(match["all_terms_matched"])
        self.assertEqual(match["answer_sufficiency"], "not_assessed")

    def test_sql_sorgu_dili_kullaniciya_acilmaz(self):
        for query in ('" OR * NEAR(x) --', "%' OR 1=1 --", "x'); DROP TABLE reports; --"):
            plan = sorgu_hazirla(query)
            self.assertRegex(plan["match"], r'^"t[0-9a-f]+"(?: OR "t[0-9a-f]+")*$')
        self.assertTrue(sorgu_hazirla("%_")["match"])
        self.assertEqual(sorgu_hazirla("")["match"], "")

    def test_bilinmeyen_belirtec_ve_sinir_korunur(self):
        plan = sorgu_hazirla("Python missing_xyz")
        self.assertEqual(plan["required"], (0, 1))
        self.assertEqual(plan["minimum"], 2)
        for query in (None, True, 1, "x" * 201, "a\nb", " ".join(f"term{n}" for n in range(25))):
            with self.assertRaises(ValueError):
                sorgu_hazirla(query)

    def test_bilesik_sozcuk_ve_ingilizce_ekler(self):
        fields = belge_terimleri({"title": "Free-threaded builds", "summary": "", "body": "", "tags": []})
        self.assertTrue(eslesme_bilgisi(sorgu_hazirla("free threading build"), fields)["all_terms_matched"])
        self.assertTrue(eslesme_bilgisi(sorgu_hazirla("free-threading"), fields)["all_terms_matched"])
        self.assertEqual(len(sorgu_hazirla("source sources kaynak kaynaklar")["groups"]), 1)

    def test_miktar_soru_icin_iddiadir_zorunlu_surum_degildir(self):
        plan = sorgu_hazirla("Does CSS require 50 pixels in spec 3.2?")
        self.assertNotIn(plan["labels"].index("50"), plan["required"])
        self.assertIn(plan["labels"].index("3.2"), plan["required"])

    def test_genel_govde_kelimeleri_yetersiz_kismi_adaydir(self):
        fields = belge_terimleri({"title": "Database integrity", "summary": "", "body": "Keep work without losing edits.", "tags": []})
        self.assertEqual(aday_kapsami(sorgu_hazirla("without edits gardening tools"), fields), -1)
        self.assertGreater(aday_kapsami(sorgu_hazirla("database edits gardening tools"), fields), 0)
        self.assertGreater(aday_kapsami(sorgu_hazirla("without edits"), fields), 0)

    def test_bilinen_kavramin_turkce_eki_ve_uzun_soru(self):
        fields = belge_terimleri({"title": "Keyboard accessibility", "summary": "", "body": "", "tags": []})
        plan = sorgu_hazirla("Klavyeyle gezinirken uygulamamın erişilebilirliğinde yaşadığım sorunu hangi yöntemle anlamalıyım?")
        match = eslesme_bilgisi(plan, fields)
        self.assertEqual(plan["minimum"], 2)
        self.assertIn("klavyeyle", match["matched_terms"])
        # Ünsüz değişimi için genel ve hataya açık kök bulma yapılmaz.
        self.assertIn("erisilebilirliginde", match["missing_terms"])


def alanlar(title="", summary="", body="", tags=(), **extra):
    return belge_terimleri({"title": title, "summary": summary, "body": body, "tags": list(tags), **extra})


def kapsar(query, fields):
    return aday_kapsami(sorgu_hazirla(query), fields) > 0


class TekNormallestiriciTestleri(unittest.TestCase):
    """A1: dizin ve sorgu aynı belirteçleyiciden geçer; birleşik biçim hiçbir zaman zorunlu değildir."""

    def test_turkce_i_katlama_ve_aksanlar_iki_yonde_ayni(self):
        for spellings in (("ılık", "ilik", "ILIK", "Ilık"), ("İstanbul", "istanbul", "ISTANBUL", "İSTANBUL"),
                          ("şirket", "sirket", "ŞİRKET", "SIRKET"), ("öğrenme", "ogrenme", "ÖĞRENME")):
            tokens = {tuple(belirtecler(value)) for value in spellings}
            self.assertEqual(len(tokens), 1, spellings)
        fields = alanlar(title="Kısıtlı ılık ŞİRKET verisi")
        for query in ("kisitli ilik sirket", "KISITLI ILIK ŞİRKET", "kısıtlı ılık şirket"):
            self.assertTrue(eslesme_bilgisi(sorgu_hazirla(query), fields)["all_terms_matched"], query)

    def test_kisa_ad_ve_sayi_birlesik_ve_ayrik_iki_yonde_bulunur(self):
        pairs = (("ros2 bag", "ROS 2 bag"), ("h264 codec", "H 264 codec"), ("fts5 ranking", "FTS 5 ranking"),
                 ("gpt4 prompt", "GPT 4 prompt"))
        for combined, separated in pairs:
            for document, query in ((combined, separated), (separated, combined), (combined, combined), (separated, separated)):
                with self.subTest(document=document, query=query):
                    plan = sorgu_hazirla(query)
                    self.assertEqual(len(plan["groups"]), 2)
                    self.assertTrue(kapsar(query, alanlar(title=document)))
        # Sayı sürümdür: başka sayı başka addır, genel kural ROS'a özel değildir.
        self.assertFalse(kapsar("h265 codec", alanlar(title="H 264 codec")))
        self.assertFalse(kapsar("fts 4 ranking", alanlar(title="FTS5 ranking")))
        self.assertFalse(kapsar("ros 1 bag", alanlar(title="ros2 bag")))

    def test_birlesik_bicim_zorunlu_degil_parcalar_ayri_gecince_de_eslesir(self):
        # Belgede ayraç veya noktalama araya girince birleşik belirteç oluşmaz; parçalar yine yeter.
        self.assertTrue(kapsar("ros2 jazzy", alanlar(title="ROS (2) Jazzy replay")))
        self.assertTrue(kapsar("port 8080 proxy", alanlar(title="Proxy", body="Listen on port: 8080")))
        self.assertTrue(kapsar("X-GitHub-Delivery redelivery", alanlar(title="Webhook redelivery",
                                                                       body="The X GitHub Delivery header is repeated")))
        self.assertEqual(sorgu_hazirla("x-github-delivery")["parts"],
                         (((_encode("x"),), (_encode("github"),), (_encode("delivery"),)),))
        # Rakamlı bileşikte dağınık parça eşleşmesi yok: dizin bitişik biçimi zaten yazar (B3).
        for query in ("ros2", "3.14", "v1.2.3", "gpt-4", "h264"):
            with self.subTest(query=query):
                self.assertEqual(sorgu_hazirla(query)["parts"], ((),))

    def test_kisa_ad_ve_sayi_noktali_surumlu_belgeyi_bulur(self):
        # B2: "wcag 2" sorgusu "WCAG 2.2" belgesini bulur; ana sürüm farklıysa bulmaz.
        wcag = alanlar(title="WCAG 2.2: visible focus, pointer targets", body="odak görünürlüğü")
        for query in ("wcag 2 focus", "wcag 2 odak görünürlüğü", "wcag2 focus", "wcag 2.2 focus", "wcag2.2 focus"):
            with self.subTest(query=query):
                self.assertTrue(kapsar(query, wcag))
        for query in ("wcag 3 focus", "wcag 2.0 focus", "wcag3 focus"):
            with self.subTest(query=query):
                self.assertFalse(kapsar(query, wcag))
        self.assertTrue(kapsar("usb 3 hub", alanlar(title="USB 3.0 hub review", body="hub")))
        self.assertTrue(kapsar("gpt 4 prompt", alanlar(title="GPT-4 prompt guide", body="prompt")))
        self.assertTrue(kapsar("cuda 12 kernel", alanlar(title="CUDA 12.4 kernel notes")))
        self.assertFalse(kapsar("cuda 11 kernel", alanlar(title="CUDA 12.4 kernel notes")))

    def test_rakamli_bilesik_basibos_sayiyla_surum_tuzagini_delmez(self):
        # B3: "ros2" gövdedeki "Table 2" ile eşleşmez; sürüm farkı korunur.
        for query, document in (("ros2 bag", dict(title="ROS 1 bag tools", body="Table 2 lists topics")),
                                ("fts5 ranking", dict(title="FTS4 ranking", body="5 columns")),
                                ("h264 decoding", dict(title="H.265 decoding", body="264 kbit")),
                                ("v1.2.3 runtime", dict(title="v1.2.4 runtime", body="3 steps")),
                                ("ros 1 bag", dict(title="ROS 2 Jazzy rosbag2", body="step 1")),
                                ("gpt4 prompt", dict(title="GPT-3 prompt guide", body="4 examples"))):
            with self.subTest(query=query):
                self.assertEqual(aday_kapsami(sorgu_hazirla(query), alanlar(**document)), -1)
        # Doğru sürüm, ayrı yazımla da bulunur.
        self.assertTrue(kapsar("ros2 bag", alanlar(title="ROS 2 bag tools", body="Table 1 lists topics")))
        self.assertTrue(kapsar("v1.2.3 runtime", alanlar(title="Runtime v1.2.3 notes")))

    def test_uzun_sozcuk_ve_sayi_birlestirilmez_miktarlar_filtre_olmaz(self):
        for query, labels in (("python 3 build", ("python", "3", "build")), ("requires 44 pixels", ("requires", "44", "pixels")),
                              ("the 3 steps", ("3", "steps")), ("wcag 2.2 focus", ("wcag", "2.2", "focus")),
                              ("react 19 forms", ("react", "19", "forms"))):
            with self.subTest(query=query):
                self.assertEqual(sorgu_hazirla(query)["labels"], labels)
        # Belgede birleşik yazılmış python3 ayrık sorguyla, ayrık yazılmış Python 3 birleşik sorguyla bulunur.
        self.assertTrue(kapsar("python 3 build", alanlar(title="python3 build")))
        self.assertTrue(kapsar("python3 build", alanlar(title="Python 3 build")))
        self.assertFalse(kapsar("python3 build", alanlar(title="Python 2 build")))

    def test_bilesik_sozcugun_parcasi_baslik_denetimine_girer(self):
        fields = alanlar(title="Designing a GitHub release feed", summary="Polling and webhooks.",
                         body="X-GitHub-Delivery identifies a delivery, and requested redelivery preserves that ID.")
        plan = sorgu_hazirla("X-GitHub-Delivery redelivery same id")
        self.assertEqual(eslesme_bilgisi(plan, fields)["missing_terms"], ["same"])
        self.assertEqual(aday_kapsami(plan, fields), 3)
        # Parça başlıkta yoksa gövdedeki genel sözcükler zayıf kısmi aday olarak kalır.
        other = alanlar(title="Mail queue", body="x-mail-delivery redelivery preserves the id")
        self.assertEqual(aday_kapsami(plan, other), -1)

    def test_ozel_teknik_belirtecler_tek_belirtectir(self):
        for query, label in (("C#", "c#"), ("C++", "c++"), (".NET", ".net"), ("snake_case", "snake_case"),
                             ("v1.2.3", "v1.2.3"), ("3.14.0", "3.14.0"), ("/api/v1", "api/v1")):
            with self.subTest(query=query):
                plan = sorgu_hazirla(query)
                self.assertEqual(plan["labels"], (label,))
                self.assertTrue(plan["required"])
        self.assertTrue(kapsar(".net runtime", alanlar(title=".NET runtime")))
        self.assertTrue(kapsar("snake_case names", alanlar(title="snake_case names")))
        self.assertFalse(kapsar("c# runtime", alanlar(title="C++ runtime")))
        self.assertFalse(kapsar("v1.2.3 runtime", alanlar(title="v1.2.4 runtime")))

    def test_karsi_ve_ya_da_baglac_sayilir(self):
        fields = alanlar(title="DuckDB Polars CSV")
        for query in ("DuckDB karşı Polars", "DuckDB karsi Polars", "duckdb ya da polars", "duckdb veya polars"):
            with self.subTest(query=query):
                plan = sorgu_hazirla(query)
                self.assertEqual(plan["labels"], ("duckdb", "polars"))
                self.assertTrue(kapsar(query, fields))
        self.assertEqual(aday_kapsami(sorgu_hazirla("karşı"), fields), -1)

    def test_sozluk_donduruldu(self):
        self.assertEqual(len(_CONCEPTS), CONCEPT_GROUP_COUNT)
        self.assertEqual(CONCEPT_GROUP_COUNT, 29)
        # Altın kümedeki bilinen kaçaklar sözlükle değil belge alanlarıyla düzelir.
        self.assertNotIn("nogil", {word for group in _CONCEPTS for word in group})


class GovdeAlanlariTestleri(unittest.TestCase):
    """A2: üretim notu indeks dışı, Limits bölümü ayrı ve düşük ağırlıklı alanda; kayıt değişmez."""

    BODY = ("Research checked: 2026-11-05.\n\n## Read release records\n\nReleases exclude tags without a release.\n\n"
            "## Limits\n\nThis is a documentation synthesis generated with NVIDIA and edited after source review. "
            "It does not describe an implemented feed or measure operating cost.\n\n"
            "## Türkçe özet\n\nSürüm duyuruları için webhook ve tarama birlikte kullanılır.\n")

    def test_uretim_notu_indeks_disinda_kalir_kayit_degismez(self):
        packet = {"title": "GitHub release feed", "summary": "", "body": self.BODY, "tags": []}
        before = dict(packet)
        fields = belge_terimleri(packet)
        self.assertEqual(packet, before)
        all_tokens = " ".join(fields.values()).split()
        self.assertNotIn(_encode("nvidia"), all_tokens)
        self.assertNotIn(_encode("synthesis"), all_tokens)
        self.assertFalse(kapsar("nvidia api limits", fields))
        # Araştırmanın konusu olan sağlayıcı adı kalır.
        topical = belge_terimleri({"title": "NIM APIs", "summary": "", "tags": [],
                                   "body": "NVIDIA describes free Developer Program access to hosted NIM endpoints."})
        self.assertIn(_encode("nvidia"), topical["body"].split())
        for note in ("Bu rapor bir model ile üretildi ve kaynaklar okunduktan sonra düzenlendi.",
                     "Generated with NVIDIA Nemotron and edited by hand.",
                     "This summary was written using an LLM from the cited pages.",
                     "Bu özet Claude tarafından oluşturuldu."):
            with self.subTest(note=note):
                self.assertEqual(govde_alanlari("Keep this sentence.\n" + note)["body"].strip(), "Keep this sentence.")
                self.assertEqual(govde_alanlari("Keep this sentence. " + note)["body"].strip(), "Keep this sentence.")

    def test_uretim_notu_suzgeci_icerik_cumlelerini_silmez(self):
        # B4: sağlayıcı ya da model adı geçen konu cümleleri indekste kalır.
        for sentence in ("MCP was created by Anthropic in November 2024.",
                         "Responses generated by the model include usage metadata.",
                         "Nemotron models are produced by NVIDIA and served through NIM.",
                         "Model ile üretilen özetler denetlenmelidir.",
                         "The CUDA kernels were written by NVIDIA engineers.",
                         "Embeddings created using the model run on the GPU.",
                         "Images were generated with Stable Diffusion.",
                         "This API was created by Google in 2015."):
            with self.subTest(sentence=sentence):
                self.assertFalse(uretim_notu_mu(sentence))
                self.assertEqual(govde_alanlari(sentence)["body"], sentence)
        fields = alanlar(title="Review panel", body="MCP was created by Anthropic in November 2024. Nemotron models are produced by NVIDIA.")
        self.assertTrue(kapsar("anthropic mcp", fields))
        self.assertTrue(kapsar("nemotron nvidia", fields))

    def test_uretim_notu_suzgeci_uzun_noktasiz_govdede_dogrusal(self):
        # B1: 32 KB tek satır, noktasız gövde; eski desen 47–61 s sürüyordu.
        body = "lorem ipsum dolor sit amet " * 1200
        self.assertGreater(len(body.encode("utf-8")), 32000)
        started = time.perf_counter()
        parts = govde_alanlari(body)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.1, f"govde_alanlari {elapsed:.3f} s")
        self.assertEqual(parts["body"], body)
        started = time.perf_counter()
        govde_alanlari("word " * 6000 + ".")
        govde_alanlari("\n".join("this is line %d with words" % index for index in range(1500)))
        self.assertLess(time.perf_counter() - started, 0.1)

    def test_limits_bolumu_ayri_alanda_turkce_ozet_govdede(self):
        parts = govde_alanlari(self.BODY)
        self.assertIn("operating cost", parts["limits"])
        self.assertNotIn("generated with", parts["limits"])
        self.assertNotIn("operating cost", parts["body"])
        self.assertIn("webhook ve tarama", parts["body"])
        self.assertIn("Releases exclude", parts["body"])
        fields = belge_terimleri({"title": "GitHub release feed", "summary": "", "body": self.BODY, "tags": []})
        self.assertIn(_encode("cost"), fields["limits"].split())
        self.assertNotIn(_encode("cost"), fields["body"].split())
        # Başlık sözcüğü "Limits" içerik değildir; yalnız bölüm başlığı olduğu için indekse girmez.
        self.assertNotIn(_encode("limit"), " ".join(fields.values()).split())
        # Sınırlama bilgisi silinmez: Limits bölümündeki terim kapsamda sayılmaya devam eder.
        self.assertTrue(kapsar("github operating cost", fields))
        for heading in ("## Limitations", "### Sınırlar", "## Kısıtlar", "# Known limitations"):
            with self.subTest(heading=heading):
                self.assertEqual(govde_alanlari(heading + "\nOnly here.\n## Next\nMain.")["limits"].strip(), "Only here.")
        self.assertEqual(govde_alanlari("## Limits of the API\nStill main.")["limits"], "")

    def test_answers_alani_istege_bagli_ve_bossa_etkisiz(self):
        self.assertEqual(tuple(belge_terimleri({"title": "T", "summary": "", "body": "", "tags": []})), INDEX_FIELDS)
        self.assertEqual(alanlar(title="T")["answers"], "")
        self.assertEqual(alanlar(title="T", answers=None)["answers"], "")
        self.assertEqual(alanlar(title="T", answers=[3, {"x": 1}])["answers"], "")
        fields = alanlar(title="Python 3.14 free-threading",
                         answers=["Is nogil Python production ready?", "Does removing the GIL make Python faster?"])
        self.assertIn(_encode("nogil"), fields["answers"].split())
        self.assertNotIn(_encode("nogil"), fields["body"].split())
        self.assertTrue(kapsar("is nogil python production ready", fields))
        self.assertEqual(alanlar(title="T", answers="single question text")["answers"],
                         alanlar(title="T", answers=["single question text"])["answers"])

    def test_eslesme_bilgisi_bicimi_dort_anahtar_kalir(self):
        match = eslesme_bilgisi(sorgu_hazirla("python cost"), alanlar(title="Python", body="## Limits\ncost"))
        self.assertEqual(set(match), {"matched_terms", "missing_terms", "all_terms_matched", "answer_sufficiency"})
        self.assertEqual(match["matched_terms"], ["python", "cost"])


if __name__ == "__main__":
    unittest.main()
