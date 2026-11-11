"""alpha.15, düzeltme turu 2: bağımsız kabulün (`incelemeler/alpha15-kabul-20261111.md`) köprü bulguları K-a, K-b, K-c, K-e.

K-a: bölüm boyu tahmini U+2028/U+2029/U+0085'i 1 karakter sayıyordu (son metinde 7); bölüm okunamıyordu ve ileti zaten çağrılan
section yolunu gösteriyordu. K-b: sınırı aşan yanıt geri çevrilse de kayıt "bütün verildi" diye işaretleniyordu. K-c: aramada
sunucunun katladığı Türkçe terimler "kayıtta bulunmayan terimler" satırından düşüyordu. K-e: köprü her uzantı sürümü için
"another contributor" diyordu. Bu sınamalar düzeltmeden önceki kodda (f83523c6) başarısız olur. Sahte yerel uygulama; ağ yok.
"""

import asyncio
import importlib.util
import json
import unittest
from unittest import mock

import mcp_topluluk as kopru
from mcp_topluluk import HitLog, create_server
from test_kopru_sinir import SinirTaban, blok_govdesi, dolgu, kucuk_kayit
from tests import k2_sahte_sunucu as sahte

AYIRICILAR = {"u2028": "a ", "u0085": "b\x85", "u2029_tirnak": " \""}


def ayirici_kayit(rid, url, desen):
    rows = [(dolgu(f"{n} " + desen, 240), dolgu(f"{n} " + desen, 300), "cpython", "3.14", "exact") for n in range(60)]
    return sahte.synthetic_agent_view(url, rows, record_id=rid, kind="research_report",
                                      content={"title": "Separators " + rid[:4], "short_answer": "A short answer.",
                                               "not_covered": ["x"]})


async def cagir(oturum, arac, girdi):
    return (await oturum.call_tool(arac, girdi)).content[0].text


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Optional requirements-mcp.txt is not installed.")
class KaSatirAyirici(SinirTaban):
    def test_satir_ayirici_yogun_kayitta_her_bolum_okunur(self):
        from mcp import Client

        kayitlar = {}
        for i, (ad, desen) in enumerate(AYIRICILAR.items()):
            rid, url = f"{i + 40:02x}" * 16, f"https://docs.python.org/3.14/library/ayirici{i}.html"
            self.ekle(ayirici_kayit(rid, url, desen))
            kayitlar[ad] = (rid, url)

        async def senaryo():
            async with Client(create_server(client=self.client, hits=HitLog()), raise_exceptions=True) as s:
                for ad, (rid, url) in kayitlar.items():
                    await cagir(s, "lookup_source", {"urls": [url]})   # köprü kimliği bulmadan öğrenir
                    ana = await cagir(s, "read_research", {"research_id": rid, "version": 1, "view": "outline"})
                    gorulen = []
                    for bolum in blok_govdesi(json.loads(ana)["untrusted"])["sections"][1:]:
                        t = await cagir(s, "read_research", {"research_id": rid, "version": 1, "view": "section",
                                                             "section_id": bolum["section_id"]})
                        self.assertLessEqual(len(t), kopru.TEXT_LIMIT, (ad, bolum["title"]))
                        self.assertNotIn("over the 28,000-character limit", t, (ad, bolum["title"]))
                        gorulen.extend(c["n"] for c in blok_govdesi(json.loads(t)["untrusted"])["claims"])
                    self.assertEqual(gorulen, list(range(1, 61)), ad)

        asyncio.run(senaryo())

    def test_sigmayan_bolumun_iletisi_section_yolunu_gostermez(self):
        from mcp import Client

        rid, url = "4a" * 16, "https://docs.python.org/3.14/library/buyuk.html"
        self.ekle(ayirici_kayit(rid, url, "a "))

        async def senaryo():
            # Bölme sınırları kaldırılınca tek bölüm sınırı aşar (bozuk ya da ele geçirilmiş sunucu benzeri).
            with mock.patch.object(kopru, "SECTION_CHARS", 10 ** 7), mock.patch.object(kopru, "SECTION_ESCAPED_CHARS", 10 ** 7):
                async with Client(create_server(client=self.client, hits=HitLog()), raise_exceptions=True) as s:
                    await cagir(s, "lookup_source", {"urls": [url]})
                    ana = await cagir(s, "read_research", {"research_id": rid, "version": 1, "view": "outline"})
                    bolum = blok_govdesi(json.loads(ana)["untrusted"])["sections"][1]
                    return await cagir(s, "read_research", {"research_id": rid, "version": 1, "view": "section",
                                                            "section_id": bolum["section_id"]})

        t = asyncio.run(senaryo())
        self.assertIn("over the 28,000-character limit", t)
        self.assertIn(kopru.SECTION_TOO_LARGE, t)
        self.assertNotIn('view="section"', t)


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Optional requirements-mcp.txt is not installed.")
class KbTeslimGunlugu(SinirTaban):
    """Son metin sınırı aşarsa kayıt "bütün verildi" sayılmaz: sonraki çağrı hatırlatma değil kaydın kendisini verir."""

    def setUp(self):
        super().setUp()
        self.rid, self.url = "5b" * 16, "https://docs.python.org/3.14/library/teslim.html"
        self.ekle(kucuk_kayit(self.rid, self.url))
        rid = self.rid

        def ara(payload):
            item = {"id": rid, "version": 1, "status": "published", "record_type": "v4", "kind": "source_card",
                    "human_reviewed": False, "report_url": f"https://devtrellis.org/api/community/v4/records/{rid}/v/1",
                    "publication": {"path": "machine_gate", "human_reviewed": False, "label": {"en": "Not human-reviewed"}},
                    "packet": {"title": "Small " + rid[:4], "summary": "s", "sources": []},
                    "search_match": {"matched_terms": ["small"], "missing_terms": [], "all_terms_matched": True}}
            return {"items": [item], "total": 1, "limit": 3, "offset": 0}

        self.library.search = ara
        self.library.topic_entries = [{"card": {"id": rid, "version": 1}, "kind": "source_card",
                                       "products": [{"product": "cpython", "versions": ["3.14"]}], "terms": ["small"]}]

    def _iki_cagri(self, ilk, sonraki, hazirlik=()):
        from mcp import Client
        asil = kopru.response_text

        def sisir(head, blocks):
            return asil(head, blocks) + " " * kopru.TEXT_LIMIT

        async def senaryo():
            async with Client(create_server(client=self.client, hits=HitLog()), raise_exceptions=True) as s:
                for adim in hazirlik:
                    await cagir(s, *adim)
                with mock.patch.object(kopru, "response_text", sisir):
                    reddedilen = await cagir(s, *ilk)
                return reddedilen, await cagir(s, *sonraki)

        return asyncio.run(senaryo())

    def test_bulma(self):
        cagri = ("lookup_source", {"urls": [self.url]})
        reddedilen, sonraki = self._iki_cagri(cagri, cagri)
        self.assertIn("over the 28,000-character limit", reddedilen)
        self.assertNotIn("already_delivered", sonraki)
        self.assertIn(f"record {self.rid}@1", sonraki)

    def test_arama(self):
        reddedilen, sonraki = self._iki_cagri(("search_research", {"query": "small widget"}),
                                              ("lookup_source", {"urls": [self.url]}))
        self.assertIn("over the 28,000-character limit", reddedilen)
        self.assertNotIn("already_delivered", sonraki)

    def test_okuma(self):
        oku = ("read_research", {"research_id": self.rid, "version": 1})
        # Kimlik önce aramayla öğrenilir (kayıt orada bütün gösterilir); ilk okuma hatırlatma, ikincisi zorla bütün okumadır.
        reddedilen, sonraki = self._iki_cagri(oku, oku, hazirlik=[("search_research", {"query": "small widget"}), oku])
        self.assertIn("over the 28,000-character limit", reddedilen)
        self.assertNotIn("already_delivered", sonraki)


class KcTurkceTerimler(unittest.TestCase):
    def test_katlanmis_terimler_satirdan_dusmez(self):
        self.assertEqual(kopru.asked_terms(["turkce", "istanbul"], kopru.query_words("Türkçe arama İstanbul")),
                         ["turkce", "istanbul"])
        self.assertEqual(kopru.asked_terms(["istanbul", "isik"], kopru.query_words("İSTANBUL ışık")), ["istanbul", "isik"])

    def test_sorguda_olmayan_sunucu_terimi_yine_duser(self):
        self.assertEqual(kopru.asked_terms(["evil", "run", "turkce"], kopru.query_words("Türkçe arama")), ["turkce"])
        self.assertEqual(kopru.asked_terms(["python", "3.14", "gil"], kopru.query_words("Python 3.14 GIL")),
                         ["python", "3.14", "gil"])


class KeUzantiIfadesi(unittest.TestCase):
    def test_uzanti_surumu_baska_katkici_demez(self):
        view = sahte.synthetic_agent_view("https://docs.python.org/3.14/library/uzanti.html",
                                          [("Claim one.", "Quote one.", "cpython", "3.14", "exact"),
                                           ("Claim two.", "Quote two.", "cpython", "3.14", "exact")],
                                          record_id="6c" * 16, kind="research_report", content={"title": "Ext"})
        view["server"]["extended"] = {"versions": [
            {"version": 1, "added_claims": [1, 1], "path": "machine_gate", "human_reviewed": False},
            {"version": 2, "added_claims": [2, 2], "path": "machine_gate", "human_reviewed": False}]}
        model = kopru.record_model(view)
        metin, _, _ = kopru.render_record(model)
        self.assertNotIn("another contributor", metin)
        self.assertIn("v2 claim 2 added by an extension", metin)
        self.assertIn("[added in version 2 (extension)]", metin)


class R1BozukKart(unittest.TestCase):
    """alpha.14'ten kalan: alanı eksik eski biçimli kart aynı bulma çağrısındaki öteki adresleri düşürüyordu."""

    def test_bozuk_r1_yalniz_kendi_adresini_dusurur(self):
        iyi_url, bozuk_url = "https://docs.python.org/3.14/library/iyi.html", "https://docs.python.org/3.13/library/bozuk.html"
        bozuk = sahte.synthetic_card(bozuk_url, [("t", "q", "cpython", "3.13", "pep440")], card_id="7d" * 16)
        del bozuk["claims"][0]["claim_review"]
        library = sahte.FakeLibrary([kucuk_kayit("8e" * 16, iyi_url), bozuk])
        server, origin = sahte.start(library)
        try:
            answer, text = kopru.SourceLookup(kopru.CommunityClient(origin)).lookup_result([iyi_url, bozuk_url])
        finally:
            sahte.stop(server)
        self.assertEqual([r["result"] for r in answer["results"]], ["found", "unavailable"])
        self.assertEqual(answer["results"][1]["reason"], "invalid_or_unreachable")
        self.assertIn(f"record {'8e' * 16}@1", text)


if __name__ == "__main__":
    unittest.main()
