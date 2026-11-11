"""Köprünün 28.000 karakter sınırı, son yanıt metni üzerinde (durum değerlendirmesi 11 Kasım 2026, iki uç durum).

Girdiler değerlendirmenin betiğinden (`incelemeler/durum-degerlendirmesi-20261111/sinir_iki_durum.py`): (a) şema sınırında, metni
özel karakterle dolu rapor (`"`, `\\`, satır sonu, sekme, denetim karakteri, çok baytlı karakter); (b) bir büyük kayıt ve birden çok
"zaten gösterildi" hatırlatması tek bulma çağrısında. Her okuma yolu (bulma kart/bütün, arama, okuma, outline, section, hatırlatma)
gerçek MCP istemci oturumunda çağrılır ve modele giden metin `len(metin) <= TEXT_LIMIT` olmalıdır. Eski düz metinli sınır testleri
bu iki durumu yakalamıyordu (`test_k2_istemci.py`). Sahte yerel uygulama; ağ ve model yok.
"""

import asyncio
import importlib.util
import json
import unittest

import mcp_topluluk as kopru
from mcp_topluluk import CommunityClient, DeliveryLog, HitLog, SourceLookup, create_server
from tests import k2_sahte_sunucu as sahte

DESENLER = {
    "tirnak_ters_bolu": '"\\',
    "kod_benzeri": '{"path": "C:\\\\tmp\\\\a", "re": "\\\\d+"} ',
    "satir_sonu_sekme": 'a\n\t"b"\r\n',
    "denetim_karakteri": 'x\x01\x02\x1f"',
    "cok_bayt": 'ğüşİöç漢字😀"',
    "karisik": '"\\\n\t\x01ğ漢😀 ',
    "duz_metin": "Plain text without special characters. ",
}
URLS = [f"https://docs.python.org/3.14/library/b{i}.html" for i in range(5)]
KUCUK = [(f"{i + 2:x}" * 32, URLS[i]) for i in range(1, 5)]


def dolgu(desen, uzun):
    return (desen * (uzun // len(desen) + 1))[:uzun]


def a_kayit(rid, url, desen):
    rows = [(dolgu(f"{n} " + desen, 240), dolgu(f"{n} " + desen, 300), "cpython", "3.14", "exact") for n in range(60)]
    content = {"title": "Special characters " + rid[:4], "short_answer": "A short answer.", "answers": ["Q1?", "Q2?", "Q3?"],
               "not_covered": ["Benchmarks."]}
    return sahte.synthetic_agent_view(url, rows, record_id=rid, kind="research_report", content=content)


def b_kayit(rid, url, iddia, uzun):
    text = dolgu("Claim text about a documented behaviour, long enough to be realistic for a report claim. ", uzun)
    quote = dolgu("Quoted sentence from the cited page, kept exactly as the gate found it in the visible page text. ", 290)
    rows = [(f"{n} {text}", f"{n} {quote}", "cpython", "3.14", "exact") for n in range(iddia)]
    content = {"title": "Big record " + rid[:4], "short_answer": "A short answer.", "answers": ["Q1?", "Q2?", "Q3?"],
               "not_covered": ["n" * 240] * 10, "recommendations": ["r" * 400] * 5}
    return sahte.synthetic_agent_view(url, rows, record_id=rid, kind="research_report", content=content)


def kucuk_kayit(rid, url):
    rows = [("A small claim.", "A small quote from the page.", "cpython", "3.14", "exact")]
    return sahte.synthetic_agent_view(url, rows, record_id=rid, kind="source_card",
                                      content={"title": "Small " + rid[:4], "not_covered": ["x"]})


def blok_govdesi(metin):
    satirlar = metin.split("\n")
    return json.loads(satirlar[next(i for i, s in enumerate(satirlar) if s.startswith("BEGIN UNTRUSTED ")) + 1])


class SinirTaban(unittest.TestCase):
    def setUp(self):
        self.library = sahte.FakeLibrary()
        self.server, self.origin = sahte.start(self.library)
        self.client = CommunityClient(self.origin)

    def tearDown(self):
        sahte.stop(self.server)

    def ekle(self, view):
        for match in view["server"]["quote_matches"]:
            self.library.cards[match["address_key"]] = view


class SonMetinSiniri(unittest.TestCase):
    def test_hatirlatma_payi_en_uzun_hatirlatmadan_kucuk_degil(self):
        en_uzun = (f"record {'f' * 32}@{'9' * 10}: already_delivered true; version_match "
                   f"{max(kopru.VERSION_MATCHES, key=len)}; eligible_if_record_held false. {kopru.ALREADY_DELIVERED_HINT}")
        self.assertGreaterEqual(kopru.RECORD_RESERVE, len(en_uzun) + 2)

    def test_son_metin_siniri_asarsa_kesilmez_boyu_soylenir(self):
        self.assertEqual(kopru.within_limit("x" * kopru.TEXT_LIMIT), "x" * kopru.TEXT_LIMIT)
        metin = kopru.within_limit('{"a": "' + "y" * kopru.TEXT_LIMIT + '"}')
        self.assertLessEqual(len(metin), kopru.TEXT_LIMIT)
        self.assertIn(f"{kopru.TEXT_LIMIT + 9:,} characters", metin)
        self.assertNotIn("yyy", metin)


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Optional requirements-mcp.txt is not installed.")
class OzelKarakterliKayit(SinirTaban):
    """Uç durum a: bölüm boyu tek kat kaçışla ölçülüyordu, yanıt ikinci kez JSON'a sarılıyordu (29.642 karakter)."""

    def test_her_okuma_yolu_sinirin_altinda_ve_bolumler_butun_iddialari_kapsar(self):
        from mcp import Client

        kayitlar = {}
        for i, (ad, desen) in enumerate(DESENLER.items()):
            rid, url = f"{i + 10:02x}" * 16, f"https://docs.python.org/3.14/library/ozel{i}.html"
            self.ekle(a_kayit(rid, url, desen))
            kayitlar[ad] = (rid, url)
        rids = {rid: ad for ad, (rid, _) in kayitlar.items()}

        def ara(payload):
            ad = payload.get("query", "").split()[-1]
            rid = kayitlar[ad][0] if ad in kayitlar else None
            item = {"id": rid, "version": 1, "status": "published", "record_type": "v4", "kind": "research_report",
                    "human_reviewed": False, "report_url": f"https://devtrellis.org/api/community/v4/records/{rid}/v/1",
                    "publication": {"path": "machine_gate", "human_reviewed": False, "label": {"en": "Not human-reviewed"}},
                    "packet": {"title": "Special characters " + rid[:4], "summary": "s", "sources": []},
                    "search_match": {"matched_terms": ["special", "characters"], "missing_terms": [],
                                     "all_terms_matched": True}}
            return {"items": [item] if rid else [], "total": 1 if rid else 0, "limit": 3, "offset": 0}

        self.library.search = ara
        self.library.topic_entries = [{"card": {"id": rid, "version": 1}, "kind": "research",
                                       "products": [{"product": "cpython", "versions": ["3.14"]}], "terms": ["special"]}
                                      for rid in rids]
        en_uzun = {}

        async def senaryo():
            for ad, (rid, url) in kayitlar.items():
                metinler = {}
                # Köprünün akışı: bulma kaydı gösterir (kimliği öğrenir); kayıt bütün sığdıysa ilk okuma hatırlatma, ikinci
                # okuma bütün döner; sığmadıysa (kısmi) iki okuma da kısmi görünümdür. Hepsi sınırın altında olmalı.
                async with Client(create_server(client=self.client, hits=HitLog()), raise_exceptions=True) as s:
                    metinler["lookup_full"] = (await s.call_tool("lookup_source", {"urls": [url], "view": "full"})).content[0].text
                    metinler["read_reminder"] = (await s.call_tool("read_research", {"research_id": rid,
                                                                                     "version": 1})).content[0].text
                    metinler["read_full"] = (await s.call_tool("read_research", {"research_id": rid, "version": 1})).content[0].text
                    ana = (await s.call_tool("read_research", {"research_id": rid, "version": 1, "view": "outline"})).content[0].text
                    metinler["outline"] = ana
                    bolumler = blok_govdesi(json.loads(ana)["untrusted"])["sections"]
                    gorulen = []
                    for bolum in bolumler[1:]:
                        t = (await s.call_tool("read_research", {"research_id": rid, "version": 1, "view": "section",
                                                                 "section_id": bolum["section_id"]})).content[0].text
                        metinler["section " + bolum["title"][-40:]] = t
                        iddialar = blok_govdesi(json.loads(t)["untrusted"])["claims"]
                        self.assertEqual(len(iddialar), bolum["claims"], ad)
                        gorulen.extend(c["n"] for c in iddialar)
                    self.assertEqual(gorulen, list(range(1, 61)), ad)        # bölünme iddia kaybetmedi
                    # Bulmanın ikinci çağrısı (bütün gösterildiyse hatırlatma).
                    metinler["lookup_reminder"] = (await s.call_tool("lookup_source", {"urls": [url]})).content[0].text
                async with Client(create_server(client=self.client, hits=HitLog()), raise_exceptions=True) as s:
                    metinler["lookup_card"] = (await s.call_tool("lookup_source", {"urls": [url]})).content[0].text
                async with Client(create_server(client=self.client, hits=HitLog()), raise_exceptions=True) as s:
                    metinler["search"] = (await s.call_tool("search_research",
                                                            {"query": "special characters " + ad})).content[0].text
                self.assertIn("hit true", metinler["search"], ad)
                for yol, metin in metinler.items():
                    self.assertLessEqual(len(metin), kopru.TEXT_LIMIT, (ad, yol, len(metin)))
                    self.assertNotIn("over the 28,000-character limit", metin, (ad, yol))   # içerik gerçekten sığdı
                en_uzun[ad] = max(len(m) for m in metinler.values())

        asyncio.run(senaryo())
        self.assertGreater(max(en_uzun.values()), kopru.TEXT_LIMIT - 1000)   # sınıra gerçekten yaklaşıldı


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Optional requirements-mcp.txt is not installed.")
class CokHatirlatma(SinirTaban):
    """Uç durum b: bir büyük kayıt ve 1–4 "zaten gösterildi" hatırlatması tek bulma çağrısında (eskiden 28.253 karakter)."""

    def _kur(self, iddia, uzun):
        self.library.cards.clear()
        self.ekle(b_kayit("1" * 32, URLS[0], iddia, uzun))
        for rid, url in KUCUK:
            self.ekle(kucuk_kayit(rid, url))

    def test_tarama_dogrudan(self):
        """Değerlendirmenin taramasının seyreltilmiş hâli (hatırlatma 1–4 × iddia 40/50/60 × iddia boyu 8'er adım)."""
        en = 0
        for hatirlatma in (1, 2, 3, 4):
            for iddia in (40, 50, 60):
                for uzun in range(60, 241, 8):
                    self._kur(iddia, uzun)
                    look = SourceLookup(self.client, delivered=DeliveryLog())
                    for _, url in KUCUK[:hatirlatma]:
                        look.lookup_result([url])
                    cevap, metin = look.lookup_result(URLS[:1 + hatirlatma])
                    self.assertEqual(sum(1 for r in cevap["records"] if r.get("already_delivered")), hatirlatma)
                    self.assertLessEqual(len(metin), kopru.TEXT_LIMIT, (hatirlatma, iddia, uzun, len(metin)))
                    en = max(en, len(metin))
        self.assertGreater(en, kopru.TEXT_LIMIT - 500)

    def test_gercek_mcp_oturumunda_en_kotu_durumlar(self):
        from mcp import Client

        async def senaryo(hatirlatma, iddia, uzun):
            self._kur(iddia, uzun)
            async with Client(create_server(client=self.client, hits=HitLog()), raise_exceptions=True) as s:
                for _, url in KUCUK[:hatirlatma]:
                    await s.call_tool("lookup_source", {"urls": [url]})
                metin = (await s.call_tool("lookup_source", {"urls": URLS[:1 + hatirlatma]})).content[0].text
            self.assertEqual(metin.count("already_delivered true"), hatirlatma)
            self.assertLessEqual(len(metin), kopru.TEXT_LIMIT, (hatirlatma, iddia, uzun, len(metin)))
            self.assertNotIn("over the 28,000-character limit", metin)
            return len(metin)

        # Değerlendirmenin en uzun durumu (4 hatırlatma, 50 iddia, 100 karakter) ve düzeltmeden sonraki en uzun durum.
        boylar = [asyncio.run(senaryo(*durum)) for durum in ((4, 50, 100), (1, 50, 116), (2, 60, 240))]
        self.assertGreater(max(boylar), kopru.TEXT_LIMIT - 500)


if __name__ == "__main__":
    unittest.main()
