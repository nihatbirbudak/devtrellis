"""alpha.13b makineyle kapsam: kartın alıntı almadığı sayfa bölümleri (teşhis T2, A1 zstd kartı); hepsi ağsız."""

import unittest
from datetime import datetime, timezone

from radar.denetim import denetle, kapsam
from radar.denetim.rapor import metin

from .yardimci import SahteAg, html, kart, rapor

SIMDI = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
URL = "https://docs.example.org/library/compression.zstd.html"
# docs.python.org Sphinx biçiminin küçültülmüş hâli: kenar çubuğu h3 ana içerikten önce, tanımlar `dt id` ile.
SAYFA = """<!doctype html><html><head><title>compression.zstd</title></head><body>
<div class="sphinxsidebar"><h3>Table of Contents</h3><ul><li><a href="#">Exceptions and other sections here</a></li></ul>
<h3>Previous topic</h3><p>compression</p></div>
<div class="body"><h1>compression.zstd - Compression compatible with the Zstandard format</h1>
<p>This module provides classes and functions for compressing and decompressing data using the Zstandard (or zstd) compression algorithm.</p>
<h2>Compressing and decompressing data in memory</h2>
<dl class="py function"><dt id="compression.zstd.compress">compression.zstd.compress(data, level=None, options=None)</dt>
<dd><p>The level argument is an integer controlling the level of compression. level is an alternative to setting CompressionParameter.compression_level in options. Use bounds() on compression_level to get the values that can be passed for level.</p></dd></dl>
<dl class="py function"><dt id="compression.zstd.decompress">compression.zstd.decompress(data)</dt>
<dd><p>If data is the concatenation of multiple distinct compressed frames, decompress all of these frames, and return the concatenation of the results.</p></dd></dl>
<dl class="py class"><dt id="compression.zstd.ZstdCompressor">class compression.zstd.ZstdCompressor(level=None)</dt>
<dd><p>Create a compressor object, which can be used to compress data incrementally.</p>
<dl class="py method"><dt id="compression.zstd.ZstdCompressor.compress">compress(data)</dt><dd><p>Compress data, returning a bytes object.</p></dd></dl></dd></dl>
<dl class="py class"><dt id="compression.zstd.ZstdDecompressor">class compression.zstd.ZstdDecompressor()</dt>
<dd><p>This class does not transparently handle inputs containing multiple compressed frames, unlike the decompress() function and ZstdFile class.</p></dd></dl>
<h2>Advanced parameter control</h2>
<dl class="py class"><dt id="compression.zstd.CompressionParameter">class compression.zstd.CompressionParameter</dt>
<dd><p>An IntEnum containing the advanced compression parameter keys.</p>
<dl class="py method"><dt id="compression.zstd.CompressionParameter.bounds">bounds()</dt><dd><p>Return the tuple of int bounds, (lower, upper), of a compression parameter.</p></dd></dl>
<dl class="py attribute"><dt id="compression.zstd.CompressionParameter.compression_level">compression_level</dt>
<dd><p>Regular compression levels are greater than 0. Values greater than 20 are considered "ultra" compression and require more memory than other levels. Setting the level to zero uses COMPRESSION_LEVEL_DEFAULT.</p></dd></dl></dd></dl>
<dl class="py class"><dt id="compression.zstd.DecompressionParameter">class compression.zstd.DecompressionParameter</dt>
<dd><p>An IntEnum containing the advanced decompression parameter keys.</p>
<dl class="py method"><dt id="compression.zstd.DecompressionParameter.bounds">bounds()</dt><dd><p>Return the tuple of int bounds of a decompression parameter.</p></dd></dl></dd></dl>
<h2>Miscellaneous</h2>
<dl class="py data"><dt id="compression.zstd.COMPRESSION_LEVEL_DEFAULT">compression.zstd.COMPRESSION_LEVEL_DEFAULT</dt>
<dd><p>The default compression level for Zstandard: 3.</p></dd></dl>
<h2>Examples</h2><p>Examples of using the compression.zstd module are shown below for reading data.</p>
</div><div class="footer"><h3>Navigation</h3><table><tr><td>Previous page Home Next page and more links here</td></tr></table></div>
</body></html>"""

IDDIA = {"checked_on": "2026-11-06", "claim_type": "source_statement", "source_url": URL,
         "applies_to": [{"product": "cpython", "versions": "3.14", "version_scheme": "exact"}]}
# Canlı A1 kartının (alpha.12) eksik hâli: varsayılan 3, bounds() ve ZstdDecompressor uyarısı alıntılanmamış.
EKSIK = [
    ("compression.zstd is a standard-library module for Zstandard compression.",
     "This module provides classes and functions for compressing and decompressing data using the Zstandard (or zstd) compression algorithm."),
    ("compress() takes an integer level; valid values come from CompressionParameter.compression_level.bounds().",
     "The level argument is an integer controlling the level of compression. level is an alternative to setting CompressionParameter.compression_level in options."),
    ("Levels above 20 are ultra; level 0 means the default.",
     "Regular compression levels are greater than 0. Values greater than 20 are considered \"ultra\" compression and require more memory than other levels. Setting the level to zero uses COMPRESSION_LEVEL_DEFAULT."),
    ("decompress() joins every frame of a multi-frame input.",
     "If data is the concatenation of multiple distinct compressed frames, decompress all of these frames, and return the concatenation of the results."),
]


def ag():
    return SahteAg(sayfalar={("docs.example.org", "/robots.txt"): (200, {"content-type": "text/plain"}, "User-agent: *\n"),
                             ("docs.example.org", "/library/compression.zstd.html"): html(SAYFA)},
                   dns={"docs.example.org": ["93.184.215.14"]})


def paket(iddialar):
    return kart(claims=[dict(IDDIA, text=t, quote=q) for t, q in iddialar])


class MakineKapsamiTests(unittest.TestCase):
    def kapi(self, p, **kw):
        sahte = ag()
        return denetle(p, getirici=sahte.getirici(), yayindakiler=[], simdi=SIMDI, **kw)

    def test_eksik_kartta_t2_bosluklari_yakalanir(self):
        sonuc = self.kapi(paket(EKSIK))
        self.assertEqual(sonuc["hukum"], "yayimlanir")  # bilgi; ret ya da uyarı değil
        self.assertEqual(sonuc["uyarilar"], [])
        adlar = sonuc["makine_kapsam_disi"]["adlar"]
        # (a) kartta adı geçen ama alıntılanmayan tanımlar önce, (b) kardeş tanım, (c) kalan başlıklar.
        self.assertEqual(adlar[:2], ["CompressionParameter.bounds", "COMPRESSION_LEVEL_DEFAULT"])
        self.assertIn("ZstdDecompressor", adlar[2:4])  # kapsanan compress/decompress ile aynı başlık altında
        self.assertNotIn("Examples", adlar)  # düşük değerli başlık süzülür
        self.assertIn("Miscellaneous", adlar)
        # Kapsanan işlevle aynı yaprak adlı yöntem ve adı geçmeyen aynı adlı tanım işaretlenmez.
        self.assertNotIn("ZstdCompressor.compress", adlar)
        self.assertNotIn("DecompressionParameter.bounds", adlar)
        # Gezinme başlıkları ve altbilgi bölüm sayılmaz.
        self.assertFalse({"Table of Contents", "Previous topic", "Navigation"} & set(adlar))
        kaynak = sonuc["makine_kapsam_disi"]["kaynaklar"][0]
        self.assertEqual(kaynak["url"], URL)
        self.assertGreater(kaynak["kapsanmayan"], 3)
        self.assertIn("Kartın alıntı almadığı sayfa bölümleri", metin(sonuc))
        self.assertIn("  - COMPRESSION_LEVEL_DEFAULT", metin(sonuc))

    def test_eksiksiz_kartta_bosluklar_kalkar(self):
        tam = EKSIK + [
            ("The default compression level is 3.", "The default compression level for Zstandard: 3."),
            ("ZstdDecompressor does not handle multi-frame input transparently.",
             "This class does not transparently handle inputs containing multiple compressed frames, unlike the decompress() function and ZstdFile class."),
            ("bounds() returns the (lower, upper) bounds of a parameter.",
             "Return the tuple of int bounds, (lower, upper), of a compression parameter."),
        ]
        adlar = self.kapi(paket(tam))["makine_kapsam_disi"]["adlar"]
        self.assertFalse({"CompressionParameter.bounds", "COMPRESSION_LEVEL_DEFAULT", "ZstdDecompressor"} & set(adlar))

    def test_rapor_yalniz_adi_gecenleri_sayfa_adiyla_yazar_ve_agsiz_kapida_hesap_yok(self):
        r = rapor(claims=[dict(text=t, quote=q, source_url=URL, checked_on="2026-11-06") for t, q in EKSIK],
                  sources=[{"title": "zstd", "url": URL}])
        adlar = self.kapi(r)["makine_kapsam_disi"]["adlar"]
        self.assertEqual(adlar, ["CompressionParameter.bounds (compression.zstd.html)",
                                 "COMPRESSION_LEVEL_DEFAULT (compression.zstd.html)"])
        self.assertEqual(denetle(paket(EKSIK), ag=False, simdi=SIMDI)["makine_kapsam_disi"], {"kaynaklar": [], "adlar": []})

    def test_iddia_alinti_destegi_onerisi(self):
        """Madde 5: kod adı alıntıda yoksa çevreye bakılır; öneri bilgi olarak döner, hüküm değişmez."""
        p = paket([("compress() takes an integer level; level is an alternative to CompressionParameter.compression_level.",
                    "The level argument is an integer controlling the level of compression."),
                   ("Valid values come from bounds(); 20 is the ultra threshold and ZstdFile is unrelated here.",
                    "level is an alternative to setting CompressionParameter.compression_level in options.")])
        sonuc = self.kapi(p)
        self.assertEqual(sonuc["hukum"], "yayimlanir")
        destek = {(d["iddia"], d["oneri"]): d["eksik"] for d in sonuc["iddia_destegi"]}
        self.assertEqual(destek[(0, "alintiyi_genislet")], ["CompressionParameter", "compression_level"])
        self.assertEqual(destek[(1, "alintiyi_genislet")], ["bounds"])
        self.assertEqual(destek[(1, "iddia_alintidan_fazla")], ["20", "ZstdFile"])
        self.assertNotIn((0, "iddia_alintidan_fazla"), destek)  # kapsam sürümü (3.14) sayılmaz
        self.assertIn("İddia–alıntı desteği", metin(sonuc))

    def test_grok_beyani_ucretli_erisimle_gecer_erisimsiz_kalir(self):
        """Madde 6 (araştırma döngüsü): Grok `provider: other`, `model: grok-4`; erişim türü `non_trial` olmalı (KD-020)."""
        asama = {"provider": "other", "model": "grok-4", "access_type": "non_trial", "basis": "contributor_declaration",
                 "terms_url": None}
        p = paket(EKSIK[:2])
        p["provenance"] = {"origin": "model_assisted", "stages": [asama]}
        self.assertEqual(denetle(p, ag=False, simdi=SIMDI)["ret_nedenleri"], [])
        p["provenance"]["stages"] = [dict(asama, access_type="unknown")]
        self.assertTrue(denetle(p, ag=False, simdi=SIMDI)["ret_nedenleri"])

    def test_az_bolumlu_sayfada_liste_maddeleri_birimdir(self):
        sayfa = ("<html><body><div class='navheader'><table><tr><td>Prev Up Chapter five Home Next and more</td></tr></table></div>"
                 "<h2>5.4. Generated Columns</h2><p>Several restrictions apply to the definition of generated columns.</p><ul>"
                 "<li><p>The generation expression can only use immutable functions and cannot use subqueries.</p></li>"
                 "<li><p>A generation expression cannot reference another generated column.</p></li>"
                 "<li><p>A generated column cannot be part of a partition key.</p></li><li>Short item</li></ul></body></html>")
        liste = kapsam.bolumler(sayfa.encode())
        sonuc = kapsam.kapsam_disi(liste, ["The generation expression can only use immutable functions and cannot use subqueries."], "")
        self.assertEqual(sonuc["adlar"], ["A generation expression cannot reference another generated…",
                                          "A generated column cannot be part of a partition key."])
        self.assertEqual(kapsam.bolumler(b"plain", "text/plain"), [])


if __name__ == "__main__":
    unittest.main()
