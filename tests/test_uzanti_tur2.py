"""alpha.15, düzeltme turu 2: bağımsız kabulün (`incelemeler/alpha15-kabul-20261111.md`) uzantı bulgusu Ö-B.

Adres anahtarının "farklı sayfa" saydığı ama sunucunun aynı sayfayı verdiği yazımlar (`%2E`, sondaki `/`, yolda büyük harf,
izinli sorgu parametrelerinin sırası) çelişki ve yineleme denetimini aşıyordu. Bu sınamalar düzeltmeden önceki kodda (f83523c6)
başarısız olur. Sahte ağ, geçici depo; canlıya istek yok.
"""

from denetim_kapisi.yardimci import html
from test_uzanti_yolu import KAYNAK_3, SAYFA_3, SYNC, UzantiTaban, iddia, uzanti
from test_uzanti_tur1 import ON, YIRMI

KOK = "https://docs.example.org"
YAZIMLAR = ("/widget/2.0/cli%2Ehtml", "/widget/2.0/cli.html/", "/widget/2.0/CLI.html")


class OBAyniSayfaFarkliYazim(UzantiTaban):
    def _celiski(self, ust_adres, yeni_adres):
        v1, _ = self.rapor_yayinla()
        self.yayinla(uzanti(v1, [iddia(ON["text"], ON["quote"], ust_adres)]))
        v2 = self.kayit(v1["id"]).json()
        yanit, anahtar = self.gonder_yeni(uzanti(v2, [iddia(*YIRMI, yeni_adres)]))
        self.assertEqual(yanit.status_code, 202, (yeni_adres, yanit.text))
        self.isle()
        durum = self.durum(yanit.json()["submission"]["id"], anahtar)
        self.assertEqual((durum["status"], durum["hold_reason"]), ("pending_review", "extension_conflict"), yeni_adres)
        self.assertEqual(self.kayit(v1["id"]).json()["version"], 2, yeni_adres)

    def test_yol_yazimi_ayni_sayfa_celiski_moderasyona_gider(self):
        for yol in YAZIMLAR:
            # Gerçek sunucular bu yazımlarda çoğunlukla aynı sayfayı verir; sahte ağda aynı gövde o yola da konur.
            self.ag.sayfalar[("docs.example.org", yol)] = html(SAYFA_3)
            with self.subTest(yol=yol):
                self._celiski(KAYNAK_3, KOK + yol)

    def test_sorgu_sirasi_ayni_sayfa_celiski_moderasyona_gider(self):
        for sorgu in ("?version=2&lang=en", "?lang=en&version=2"):
            self.ag.sayfalar[("docs.example.org", "/widget/2.0/cli.html" + sorgu)] = html(SAYFA_3)
        self._celiski(KAYNAK_3 + "?version=2&lang=en", KAYNAK_3 + "?lang=en&version=2")

    def test_yineleme_ayni_alinti_ayni_sayfa_farkli_yazim_reddedilir(self):
        self.ag.sayfalar[("docs.example.org", "/widget/2.0/CLI.html")] = html(SAYFA_3)
        v1, _ = self.rapor_yayinla()
        self.yayinla(uzanti(v1, [SYNC]))
        v2 = self.kayit(v1["id"]).json()
        tekrar = iddia("Uploading every project happens with the sync subcommand.", SYNC["quote"],
                       KOK + "/widget/2.0/CLI.html")
        yanit, anahtar = self.gonder_yeni(uzanti(v2, [tekrar]))
        self.assertEqual(yanit.status_code, 202, yanit.text)
        self.isle()
        durum = self.durum(yanit.json()["submission"]["id"], anahtar)
        self.assertEqual(durum["status"], "rejected")
        self.assertIn("uzanti.yinelenen_iddia", durum["gate"]["reasons"])
        self.assertEqual(self.kayit(v1["id"]).json()["version"], 2)

    def test_farkli_sayfa_celiski_sayilmaz(self):
        # Başka gövdeli sayfa (aynı alıntı orada da geçiyor): sayfa kimliği farklı, çelişki kuşkusu yok, yayımlanır.
        self.ag.sayfalar[("docs.example.org", "/widget/2.0/CLI.html")] = html(SAYFA_3 + "<p>Other page.</p>")
        v1, _ = self.rapor_yayinla()
        self.yayinla(uzanti(v1, [ON]))
        v2 = self.kayit(v1["id"]).json()
        yanit, anahtar = self.gonder_yeni(uzanti(v2, [iddia(*YIRMI, KOK + "/widget/2.0/CLI.html")]))
        self.assertEqual(yanit.status_code, 202, yanit.text)
        self.isle()
        durum = self.durum(yanit.json()["submission"]["id"], anahtar)
        self.assertEqual(durum["status"], "published")
        self.assertNotIn("uzanti.celiski", durum["gate"]["warnings"])
