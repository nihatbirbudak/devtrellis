"""Denetimleri sırayla çalıştırır, sonuç çizelgesini, genel hükmü (yayımlanır / reddedilir / yayımlanamaz) ve tek satırlık kanıt etiketini üretir."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from radar import okuma_eki as okuma_eki_modulu

from . import adres, alinti, destek, getirme, gizlilik, kapsam, paketler, saglayici, sema, uzanti as uzanti_modulu, yinelenen, yonerge
from .ayarlar import SURUM, SURUM_OKUMA_EKSIZ, Ayarlar
from .metin import gorunur_metin, normallestir
from .sonuc import DENENEMEDI, KALDI, Denetim

YAYIMLANIR = "yayimlanir"
REDDEDILIR = "reddedilir"
YAYIMLANAMAZ = "yayimlanamaz"

KANITLAMADIKLARI = [
    "Alıntının iddiayı desteklediğini kanıtlamaz; yalnız alıntının kaynakta aynen geçtiğini gösterir.",
    "Raporun ve konu özetinin başlığı, özeti, kısa cevabı, önerileri, kapsam dışı notları ve cevapladığı sorular hiçbir kaynağa bağlanmaz; "
    "yalnız biçim, gizli bilgi, yönerge ve paket taramasından geçer.",
    "Bilginin güncel ya da doğru olduğunu kanıtlamaz; kaynak denetim günü böyle yazıyordu.",
    "Kaynağın güvenilir ya da resmî olduğunu kanıtlamaz; yalnız herkese açık olduğunu gösterir.",
    "Görünür metin denetimi dış stil dosyalarını ve betikle değişen görünümü görmez; bilinen gizleme yollarını yakalar.",
    "Gizli bilgi taraması bilinen kalıplara bakar; kalıp dışı kişisel veri (ad, adres, özel disk yolu) yakalanmaz.",
    "Paketin zararsız olduğunu kanıtlamaz; yalnız resmî kayıtta var olduğunu gösterir. Yalnız listedeki kurulum komutlarına bakılır.",
    "Sağlayıcı kuralı katkıcının beyanına dayanır; yanlış beyan yakalanmaz.",
    "Yönerge taraması sezgiseldir; temiz sonuç metnin ajanlar için zararsız olduğunu kanıtlamaz.",
    "Telif, kullanım hakkı ve katkıcı rızası bu kapıda denetlenmez.",
]
# Okuma eki açıkken (sunucu ayarı `reading_extensions`) eklenen cümle; kapalıyken liste v0.2.3'teki gibidir.
KANITLAMADIKLARI_OKUMA_EKI = KANITLAMADIKLARI + [
    "Kaynaklarda yok notları (not_in_sources) katkıcının beyanıdır; kapı yalnız maddedeki çift tırnak ya da ters tırnak "
    "içindeki terimleri (basit yazım çeşitlemeleriyle) ve maddenin 5 sözcüklük cümle parçalarını sayfa metninde ve kaydın "
    "alıntılarında arar; tırnaksız adları ve başka bir söyleyişi aramaz, yokluğu kanıtlamaz. Cevap bağlarında yalnız iddianın "
    "var olduğu denetlenir.",
]

# Kanıt etiketinde denetimlerin kısa adları (D4).
_ETIKET_ADI = {"sema": ("şema", "schema"), "kaynak_acikligi": ("kaynak açıklığı", "source access"),
               "kibar_getirme": ("kibar getirme", "polite fetch"), "alinti_eslesmesi": ("alıntı eşleşmesi", "quote match"),
               "gizli_bilgi": ("gizli bilgi (bilinen kalıplar)", "secrets (known patterns)"),
               "yonerge_kokusu": ("ajan yönergesi", "agent directives"), "paket_adlari": ("paket adları", "package names"),
               "saglayici_kurali": ("sağlayıcı kuralı (beyan)", "provider rule (declared)"),
               "yinelenen_yayin": ("yinelenen yayın", "duplicate check"),
               "yokluk_notu": ("kaynaklarda yok notu terim araması", "absence-note term search"),
               "uzanti": ("uzantı kuralları", "extension rules")}
# alpha.15 (14a): uzantı kapısının sonucu kural takımının adına bu eki alır (`gate.version` kural takımının adıdır).
UZANTI_EKI = "+uzanti-v1"
# alpha.15 güvenlik: sunucu ayarı `hold_instruction_like` açıkken geniş yönerge taraması da çalışır; kural takımının adı bu eki
# alır. Kapalıyken sonuç önceki sürümün aynısıdır.
YONERGE_GENIS_EKI = "+yonerge-genis-v1"
KANITLAMADIKLARI_UZANTI = (
    "Uzantıyla eklenen iddialar da insan incelemesinden geçmez; kapı birleşik kaydın bütün alıntılarını yeniden arar, yeni "
    "iddiaların üst kayıttakileri yinelemediğine bakar, ama yeni iddiaların bir boşluğu gerçekten kapattığını ya da üst iddialarla "
    "çelişmediğini kanıtlamaz (çelişki kuşkusu yalnız sezgiseldir).")
# Kaynağa bağlanmayan serbest metin alanları (O6). alpha.15 yokluk notları yalnız pakette varsa sayılır.
SERBEST_ALANLAR = [("title", "başlık", "title"), ("summary", "özet", "summary"), ("short_answer", "kısa cevap", "short answer"),
                   ("recommendations", "öneriler", "recommendations"), ("not_covered", "kapsam dışı notlar", "not-covered notes"),
                   ("answers", "cevaplanan sorular", "answered questions"),
                   ("not_in_sources", "kaynaklarda yok notları", "not-in-sources notes"),
                   ("not_researched", "araştırılmayanlar", "not-researched notes")]


def _ve(ogeler, baglac):
    return ogeler[0] if len(ogeler) == 1 else ", ".join(ogeler[:-1]) + f" {baglac} " + ogeler[-1]


def serbest_alanlar(paket, okuma_eki=True):
    """Pakette dolu olan ve hiçbir kaynağa bağlanmayan serbest metin alanlarının adları (okuma eki kapalıyken yalnız eski adlar)."""
    if not isinstance(paket, dict):
        return []
    return [ad for ad, _, _ in SERBEST_ALANLAR if paket.get(ad) not in (None, "", [])
            and (okuma_eki or ad not in okuma_eki_modulu.YOKLUK_ALANLARI)]


def kanit_etiketleri(denetimler, kaynak_sayisi, erisilen, toplam_iddia, bulunan, serbest, gun, ag_acik):
    """Tek satırlık Türkçe ve İngilizce kanıt etiketi: erişim, alıntı, geçilen denetimlerin adları, denetlenmeyenler, gün."""
    if ag_acik:
        tr = [f"{kaynak_sayisi} kaynağın {iyelik(erisilen)} erişilebilirdi, {toplam_iddia} iddianın "
              f"{iyelik_bulunma(bulunan)} alıntı kaynakta bulundu"]
        en = [f"{erisilen} of {kaynak_sayisi} sources reachable, quotes found in source for {bulunan} of {toplam_iddia} claims"]
    else:
        tr = [f"kaynaklara erişilmedi, {toplam_iddia} iddianın alıntısı kaynakta aranmadı"]
        en = [f"sources not fetched, quotes not checked for {toplam_iddia} claims"]
    gecen = [d for d in denetimler if d.durum in ("gecti", "uyari")]
    if gecen:
        tr.append("geçilen makine denetimleri: " + ", ".join(
            _ETIKET_ADI[d.ad][0] + (" (uyarıyla)" if d.durum == "uyari" else "") for d in gecen))
        en.append("machine checks passed: " + ", ".join(
            _ETIKET_ADI[d.ad][1] + (" (with warnings)" if d.durum == "uyari" else "") for d in gecen))
    tr.append("alıntının iddiayı desteklediği denetlenmedi")
    en.append("whether quotes support the claims was not checked")
    if serbest:
        adlar = {ad: (t, e) for ad, t, e in SERBEST_ALANLAR}
        tr.append(_ve([adlar[a][0] for a in serbest], "ve") + " hiçbir kaynağa bağlanmadı")
        en.append(_ve([adlar[a][1] for a in serbest], "and") + (" are" if len(serbest) > 1 else " is") + " not tied to any source")
    return "İnsan incelemedi; " + "; ".join(tr) + f"; {gun}", "Not human-reviewed; " + "; ".join(en) + f"; {gun}"

# Türkçe iyelik eki için sayının okunuşundaki son sözcüğün son ünlüsü ve ünsüzle bitip bitmediği.
_BIRLER = {1: "bir", 2: "iki", 3: "üç", 4: "dört", 5: "beş", 6: "altı", 7: "yedi", 8: "sekiz", 9: "dokuz"}
_ONLAR = {1: "on", 2: "yirmi", 3: "otuz", 4: "kırk", 5: "elli", 6: "altmış", 7: "yetmiş", 8: "seksen", 9: "doksan"}


def _son_sozcuk(n):
    if n == 0:
        return "sıfır"
    if n % 10:
        return _BIRLER[n % 10]
    if n % 100:
        return _ONLAR[(n % 100) // 10]
    if n % 1000:
        return "yüz"
    if n % 1_000_000:
        return "bin"
    return "milyon"


def iyelik(n):
    """'5'i', '12'si', '3'ü' gibi; sayıya gelen 3. tekil iyelik eki."""
    sozcuk = _son_sozcuk(n)
    unlu = next(c for c in reversed(sozcuk) if c in "aeıioöuü")
    ek = {"a": "ı", "ı": "ı", "e": "i", "i": "i", "o": "u", "u": "u", "ö": "ü", "ü": "ü"}[unlu]
    return f"{n}'{'s' if sozcuk[-1] in 'aeıioöuü' else ''}{ek}"


def iyelik_bulunma(n):
    """'12'sinde', '6'sında' gibi; iyelik + bulunma eki."""
    temel = iyelik(n)
    return temel + ("nde" if temel[-1] in "iü" else "nda")


def kaynak_listesi(paket):
    """İddia adresleri ve kaynak listesi, ilk görülme sırasıyla ve yinelenmeden."""
    adresler = []
    if isinstance(paket, dict):
        # claims liste değilse (sayı, sözlük …) şema reddeder; burada çökmesin (v0.2.1, tür karışıklığı denemesi).
        for iddia in paket.get("claims") if isinstance(paket.get("claims"), list) else []:
            if isinstance(iddia, dict) and isinstance(iddia.get("source_url"), str):
                adresler.append(iddia["source_url"])
        for kaynak in paket.get("sources") or [] if isinstance(paket.get("sources"), list) else []:
            if isinstance(kaynak, dict) and isinstance(kaynak.get("url"), str):
                adresler.append(kaynak["url"])
    return list(dict.fromkeys(adresler))


class _SayfaMetinleri:
    """Erişilen kaynağın görünür metni (alıntı denetiminin aradığı normalleştirilmiş metin), adres başına bir kez."""

    def __init__(self, sonuclar):
        self.sonuclar, self._metin = sonuclar, {}

    def __call__(self, url):
        sonuc = self.sonuclar.get(url)
        if sonuc is None or sonuc.erisim != "erisildi":
            return None
        if url not in self._metin:
            self._metin[url] = normallestir(gorunur_metin(sonuc.govde, sonuc.icerik_turu))
        return self._metin[url]


def kaynak_anliklari(kaynaklar, sonuclar, metin):
    """alpha.15 (14r2, tasarım engel 1): erişilen her kaynak için getirme zamanı ve alıntıların arandığı görünür metnin
    SHA-256 özeti. Sayfa sürümünün izidir; tazelik değildir (sonraki değişiklik izlenmez)."""
    anliklar = []
    for url in kaynaklar:
        sonuc, govde = sonuclar.get(url), metin(url)
        if govde is None or not getattr(sonuc, "zaman", None):
            continue
        anliklar.append({"url": url, "getirilme_zamani": sonuc.zaman,
                         "gorunur_metin_sha256": hashlib.sha256(govde.encode("utf-8")).hexdigest()})
    return anliklar


def _sayfa_kimligi(metin):
    """Düzeltme turu 2 (kabul Ö-B): adres → bu çalışmada getirilen görünür metnin SHA-256 özeti (getirilmediyse ya da boşsa
    None). Uzantı denetimi aynı sayfanın farklı yazımlarını bununla tanır."""
    def ozet(url):
        govde = metin(url) if isinstance(url, str) else None
        return hashlib.sha256(govde.encode("utf-8")).hexdigest() if govde and govde.strip() else None
    return ozet


def yokluk_denetle(paket, metin, *, ag):
    """11. denetim (alpha.15, tasarım engel 2; yalnız uyarı, yalnız `not_in_sources` varsa). Her madde için:

    - maddenin ters tırnak ya da çift tırnak içindeki terimleri (basit yazım çeşitlemesiyle: `per_page` = `per-page` =
      `perpage`; Kiril benzer harf ve görünmeyen karakter katlanır) ve madde cümlesinin 5 sözcüklük parçaları, maddenin
      aranmış dediği sayfaların görünür metninde aranır (`yokluk.terim_sayfada`);
    - aynı arama kaydın kendi alıntılarında da yapılır (`yokluk.alintida`; düzeltme turu 1, kabul Ö1).

    Tırnaksız adlar aranmaz (düzeltme turu 2, kabul Y1: ürün adı ve sürüm konuyu anar, sayfada hep geçer; yanlış uyarı).
    Bulunmaması yokluğu kanıtlamaz: başka bir söyleyiş yakalanmaz. Dönüş (Denetim, satırlar); satır = {"item": sıra,
    "terms": aranan terim sayısı, "checked_urls": [adres], "found_in": [kaynak no], "found_urls": [adres],
    "found_in_claims": [iddia sırası, 0'dan]}. Adresler sunucuda adres anahtarına çevrilir (yönetici kararı 2)."""
    d = Denetim(11, "yokluk_notu", "Kaynaklarda yok notları: terim ve ifade araması (uyarı)", engelleyici=False,
                gecti_aciklamasi="not_in_sources maddelerinin terimleri ve cümle parçaları, aranmış denen sayfalarda ve kaydın "
                                 "alıntılarında aynen geçmiyor (bu yokluğu kanıtlamaz).")
    maddeler = paket.get("not_in_sources") if isinstance(paket.get("not_in_sources"), list) else []
    sira_listesi = okuma_eki_modulu.paket_kaynak_sirasi(paket)
    iddialar = paket.get("claims") if isinstance(paket.get("claims"), list) else []
    alintilar = [okuma_eki_modulu.aranacak_metin(normallestir(i["quote"])) if isinstance(i, dict)
                 and isinstance(i.get("quote"), str) else None for i in iddialar]
    hazir_sayfa = {}
    satirlar = []
    for sira, madde in enumerate(maddeler):
        if not isinstance(madde, dict) or not isinstance(madde.get("item"), str) or not isinstance(madde.get("checked"), list):
            continue
        terimler = okuma_eki_modulu.yokluk_terimleri(madde["item"])
        numaralar = [n for n in madde["checked"] if type(n) is int and 1 <= n <= len(sira_listesi)]
        if not ag:
            d.denenemedi("yokluk.ag_kapali")
            continue
        bulunan, bakilamayan = [], False
        for numara in numaralar:
            url = sira_listesi[numara - 1]
            if url not in hazir_sayfa:
                govde = metin(url)
                hazir_sayfa[url] = okuma_eki_modulu.aranacak_metin(govde) if govde is not None else None
            if hazir_sayfa[url] is None:
                bakilamayan = True
            elif okuma_eki_modulu.yokluk_eslesmesi(madde["item"], hazir_sayfa[url]):
                bulunan.append(numara)
        alintida = [n for n, hazir in enumerate(alintilar)
                    if hazir is not None and okuma_eki_modulu.yokluk_eslesmesi(madde["item"], hazir)]
        if bakilamayan:
            d.denenemedi("yokluk.kaynak_yok")
        if bulunan:
            d.uyar("yokluk.terim_sayfada", f"not_in_sources[{sira}]: maddenin bir terimi ya da cümle parçası S"
                                           + ", S".join(str(n) for n in bulunan) + " sayfasında geçiyor; yokluk beyanı yanlış olabilir.")
        if alintida:
            d.uyar("yokluk.alintida", f"not_in_sources[{sira}]: maddenin bir terimi ya da cümle parçası kaydın "
                                      + ", ".join(f"[{n + 1}]" for n in alintida) + " alıntısında geçiyor.")
        satirlar.append({"item": sira, "terms": len(terimler),
                         "checked_urls": [sira_listesi[n - 1] for n in numaralar], "found_in": bulunan,
                         "found_urls": [sira_listesi[n - 1] for n in bulunan], "found_in_claims": alintida})
    d.ayrinti = list(satirlar)
    return d, satirlar


def _guvenli(adim, sira, ad, baslik, *args, engelleyici=True, **kwargs):
    """Bir denetimin beklenmeyen hatası bütün kapıyı düşürmesin; hata metni dışarı yazılmaz."""
    try:
        return adim(*args, **kwargs)
    except Exception:
        d = Denetim(sira, ad, baslik, engelleyici=engelleyici)
        d.denenemedi("genel.ic_hata")
        return d


def _uzanti_on_ret(ek, uzanti, simdi, okuma_eki):
    """Uzantı birleştirilemedi (üst kayıt yok, gördüğü sürüm geçmişte yok, biçim): yalnız 10. denetimden oluşan ret sonucu."""
    d10 = Denetim(10, "uzanti", "Uzantı kuralları (yalnız ekler, yineleme, kapsam silme, çelişki kuşkusu)")
    for kod, metin in uzanti.get("on_hatalar") or [("uzanti.bicim", None)]:
        d10.kal(kod, metin)
    gun = simdi.date().isoformat()
    etiket, etiket_en = kanit_etiketleri([d10], 0, 0, 0, 0, [], gun, False)
    iddialar = ek.get("claims") if isinstance(ek, dict) and isinstance(ek.get("claims"), list) else []
    ozet_girdi = json.dumps(ek, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    sonuc = {
        "denetim_surumu": (SURUM if okuma_eki else SURUM_OKUMA_EKSIZ) + UZANTI_EKI,
        "calisma_zamani": simdi.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "girdi": {"tur": uzanti_modulu.UZANTI_TURU, "iddia_sayisi": len(iddialar), "kaynak_sayisi": 0,
                  "paket_sha256": hashlib.sha256(ozet_girdi).hexdigest()},
        "ag": {"kip": "yok", "atlandi": True, "istek_sayisi": 0, "istekler": []},
        "hukum": REDDEDILIR,
        "ret_nedenleri": list(dict.fromkeys(b["kod"] for b in d10.bulgular)),
        "denenemeyenler": [], "uyarilar": [], "kanit_etiketi": etiket, "kanit_etiketi_en": etiket_en,
        "gecilen_denetimler": [], "kaynaga_baglanmayan_alanlar": [], "makine_kapsam_disi": {"kaynaklar": [], "adlar": []},
        "iddia_destegi": [], "denetimler": [d10.sozluk()],
        "kanitlamadiklari": (KANITLAMADIKLARI_OKUMA_EKI if okuma_eki else KANITLAMADIKLARI) + [KANITLAMADIKLARI_UZANTI],
        "uzanti": {"ust_id": (uzanti.get("ust") or {}).get("id"), "taban_surum": (uzanti.get("ust") or {}).get("version"),
                   "birlestirildi": False},
    }
    if okuma_eki:
        sonuc["kaynak_anliklari"], sonuc["yokluk_denetimi"] = [], []
    return sonuc


def denetle(paket, *, yayindakiler=None, ag=True, getirici=None, ayarlar=None, simdi=None, tam=False, okuma_eki=False,
            uzanti=None, yonerge_genis=False):
    """Paketi bütün denetimlerden geçirir ve JSON'a yazılabilir sonuç sözlüğü döndürür.

    `okuma_eki=False` (varsayılan; sunucu ayarı `reading_extensions` kapalı): v0.2.3 kurallarının ve sonuç biçiminin aynısı
    (yeni alan sözleşmede yok, terim uyarıları, 11. denetim ve kaynak anlıkları yok, sürüm `SURUM_OKUMA_EKSIZ`).
    `okuma_eki=True`: alpha.15 okuma eki kuralları (düzeltme turu 1, yönetici kararı 1).

    `uzanti` (alpha.15, dilim 14a; yalnız `record_extension` gönderiminde, sunucu kurar): {"ek": fark paketi, "ust": üst kaydın
    güncel sürümü, "birlesik": birleşik aday (birleştirilemediyse None), "on_hatalar": [(kod, açıklama)], "bilgi": {...}}.
    Kapı birleşik paketi bütün denetimlerden geçirir ve 10. denetimi (uzantı kuralları) ekler. `uzanti=None` iken sonuç
    önceki sürümün aynısıdır.

    `yonerge_genis` (alpha.15 güvenlik; sunucu ayarı `hold_instruction_like`): 6. denetime geniş yönerge taraması eklenir
    (yalnız uyarı: doğrulama komutu, kurulum ön koşulu; uzantıda değişiklik özeti de taranır) ve kural takımının adı
    `YONERGE_GENIS_EKI` alır. Ret kuralları değişmez. `False` iken sonuç önceki sürümün aynısıdır."""
    ayarlar = ayarlar or Ayarlar()
    simdi = simdi or datetime.now(timezone.utc)
    if uzanti is not None:
        if uzanti.get("birlesik") is None:
            return _uzanti_on_ret(paket, uzanti, simdi, okuma_eki)
        paket = uzanti["birlesik"]
        if yayindakiler is not None:
            # Uzantı kendi üstüne "benziyor" uyarısı almasın (tasarım §2c).
            yayindakiler = [k for k in yayindakiler if not (isinstance(k, dict) and k.get("id") == uzanti["ust"]["id"])]
    if ag and getirici is None:
        getirici = getirme.Getirici(ayarlar=ayarlar)
    tum_kaynaklar = kaynak_listesi(paket)
    kaynaklar = tum_kaynaklar[: ayarlar.en_cok_kaynak]

    # Ağsız denetimler önce: reddedilecek paket için siteye istek atılmaz.
    d1 = _guvenli(sema.denetle, 1, "sema", "Şema ve sınırlar", paket, bugun=simdi.date(), okuma_eki=okuma_eki)
    if len(tum_kaynaklar) > ayarlar.en_cok_kaynak:
        # Y2: sınırın ötesindeki adresler denetlenemez; sessizce kesmek yerine paket reddedilir.
        d1.kal("sema.liste_boyutu", f"Pakette {len(tum_kaynaklar)} ayrı kaynak adresi var (iddialar ve kaynak listesi); "
                                    f"sınır {ayarlar.en_cok_kaynak}. Sınırın ötesi denetlenemeyeceği için ret.")
    d5 = _guvenli(gizlilik.denetle, 5, "gizli_bilgi", "Gizli bilgi", paket)
    d6 = _guvenli(yonerge.denetle, 6, "yonerge_kokusu", "Yönerge kokusu", paket,
                  **({"genis": True} if yonerge_genis else {}))
    if yonerge_genis and uzanti is not None:
        # Uzantının birleşik pakette olmayan kendi metni (değişiklik özeti) de geniş taramadan geçer.
        ek = uzanti.get("ek") if isinstance(uzanti.get("ek"), dict) else {}
        ozet = (ek.get("extends") or {}).get("change_summary") if isinstance(ek.get("extends"), dict) else None
        if isinstance(ozet, str):
            _guvenli(yonerge.genis_tara, 6, "yonerge_kokusu", "Yönerge kokusu", {"change_summary": ozet}, d6)
    d8 = _guvenli(saglayici.denetle, 8, "saglayici_kurali", "Sağlayıcı kuralı", paket)
    d9 = _guvenli(yinelenen.denetle, 9, "yinelenen_yayin", "Yinelenen yayın", paket if isinstance(paket, dict) else {},
                  yayindakiler, esik=ayarlar.yinelenen_esik, engelleyici=False)
    if uzanti is not None:
        d9 = uzanti_modulu.benzerlik_uyarisi(d9)
    statik_ret = any(adres.statik_denetle(u)[0] for u in tum_kaynaklar)
    kisa_ret = bool(_guvenli(alinti.kisa_alintilar, 4, "alinti_eslesmesi", "Kısa alıntı", paket, ayarlar))
    onceden_ret = any(d.durum == KALDI for d in (d1, d5, d6, d8)) or statik_ret or kisa_ret
    atla = ag and onceden_ret and ayarlar.reddedilende_ag_atla and not tam
    ag_acik = ag and not atla

    d2 = _guvenli(adres.denetle, 2, "kaynak_acikligi", "Kaynağın herkese açık olması", kaynaklar, getirici=getirici,
                  ag=ag_acik, ag_atla_kodu="genel.onceki_ret" if atla else None)
    sonuc3 = _guvenli(getirme.denetle, 3, "kibar_getirme", "Kibar getirme", kaynaklar, getirici=getirici, ag=ag_acik,
                      ag_atla_kodu="genel.onceki_ret" if atla else None)
    d3, sonuclar = sonuc3 if isinstance(sonuc3, tuple) else (sonuc3, {})
    d4 = _guvenli(alinti.denetle, 4, "alinti_eslesmesi", "Alıntının kaynakta aynen geçmesi", paket if isinstance(paket, dict) else {},
                  sonuclar, ag=ag_acik, ayarlar=ayarlar, ag_atla_kodu="genel.onceki_ret" if atla else None)
    d7 = _guvenli(paketler.denetle, 7, "paket_adlari", "Kurulum komutlarındaki paket adları", paket, getirici=getirici,
                  ag=ag_acik, ayarlar=ayarlar, simdi=simdi, ag_atla_kodu="genel.onceki_ret" if atla else None)

    metin = _SayfaMetinleri(sonuclar)
    ek_denetimler, yokluk_satirlari = [], []
    ust_alinti_kaydi = False
    if uzanti is not None:
        # Üst iddianın alıntısı sayfada artık yoksa bu uzantının hatası değildir: ret değil `yayimlanamaz` (sürüklenme).
        d4, ust_alinti_kaydi = uzanti_modulu.ust_alinti_ayir(d4, len(uzanti["ust"]["claims"]))
        ek_denetimler.append(_guvenli(uzanti_modulu.denetle, 10, "uzanti", "Uzantı kuralları",
                                      {**uzanti, "ust_alinti_kaydi": ust_alinti_kaydi,
                                       "sayfa_kimligi": _sayfa_kimligi(metin)}, paket))
    if okuma_eki and isinstance(paket, dict) and isinstance(paket.get("not_in_sources"), list) and paket["not_in_sources"]:
        # alpha.15: yalnız yeni alanı taşıyan pakette çalışır; eski biçimli paketin denetim listesi değişmez.
        sonuc11 = _guvenli(yokluk_denetle, 11, "yokluk_notu", "Kaynaklarda yok notları: terim ve ifade araması (uyarı)", paket,
                           metin, ag=ag_acik, engelleyici=False)
        d11, yokluk_satirlari = sonuc11 if isinstance(sonuc11, tuple) else (sonuc11, [])
        ek_denetimler.append(d11)
    try:
        anliklar = kaynak_anliklari(kaynaklar, sonuclar, metin) if ag_acik and okuma_eki else []
    except Exception:
        anliklar = []

    denetimler = sorted([d1, d2, d3, d4, d5, d6, d7, d8, d9, *ek_denetimler], key=lambda d: d.sira)
    ret = [d for d in denetimler if d.engelleyici and d.durum == KALDI]
    eksik = [d for d in denetimler if d.engelleyici and d.durum == DENENEMEDI]
    hukum = REDDEDILIR if ret else YAYIMLANAMAZ if eksik else YAYIMLANIR

    gun = simdi.date().isoformat()
    erisilen = sum(1 for s in sonuclar.values() if s.erisim == "erisildi")
    iddia_satirlari = [s for s in d4.ayrinti if "iddia" in s]
    bulunan = sum(1 for s in iddia_satirlari if s.get("eslesme") == "aynen")
    toplam_iddia = len(paket.get("claims")) if isinstance(paket, dict) and isinstance(paket.get("claims"), list) else 0
    serbest = serbest_alanlar(paket, okuma_eki)
    etiket, etiket_en = kanit_etiketleri(denetimler, len(kaynaklar), erisilen, toplam_iddia, bulunan, serbest, gun, ag_acik)

    # alpha.13b makineyle kapsam: bilgi, hüküm ve etiketi etkilemez. Yalnız ağ açıkken (sayfa elde) hesaplanır.
    try:
        makine_kapsami = kapsam.paket_kapsami(paket, sonuclar) if ag_acik else {"kaynaklar": [], "adlar": []}
    except Exception:
        makine_kapsami = {"kaynaklar": [], "adlar": []}
    # Madde 5: iddia–alıntı desteği önerisi (bilgi; kapıda ret kuralı değildir, K5 yanlış ret üretti).
    try:
        iddia_destegi = destek.oneriler(paket, sonuclar) if ag_acik else []
    except Exception:
        iddia_destegi = []
    ozet_girdi = json.dumps(paket, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sonuc = {
        "denetim_surumu": SURUM if okuma_eki else SURUM_OKUMA_EKSIZ,
        "calisma_zamani": simdi.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "girdi": {"tur": paket.get("kind") if isinstance(paket, dict) else None, "iddia_sayisi": toplam_iddia,
                  "kaynak_sayisi": len(kaynaklar), "paket_sha256": hashlib.sha256(ozet_girdi).hexdigest()},
        "ag": {"kip": "acik" if ag else "yok", "atlandi": atla,
               "istek_sayisi": len(getirici.istekler) if getirici is not None else 0,
               "istekler": list(getirici.istekler) if getirici is not None else []},
        "hukum": hukum,
        # Yalnız ret düzeyindeki bulgular: kalan bir denetimin uyarıları ret nedeni gibi gösterilmez.
        "ret_nedenleri": list(dict.fromkeys(b["kod"] for d in ret for b in d.bulgular
                                            if b["seviye"] == KALDI and b["kod"].split(".")[0] != "genel")),
        "denenemeyenler": [d.ad for d in eksik],
        "uyarilar": list(dict.fromkeys(b["kod"] for d in denetimler for b in d.bulgular if b["seviye"] == "uyari")),
        "kanit_etiketi": etiket,
        "kanit_etiketi_en": etiket_en,
        "gecilen_denetimler": [d.ad for d in denetimler if d.durum in ("gecti", "uyari")],
        "kaynaga_baglanmayan_alanlar": serbest,
        "makine_kapsam_disi": makine_kapsami,
        "iddia_destegi": iddia_destegi,
        "denetimler": [d.sozluk() for d in denetimler],
        "kanitlamadiklari": KANITLAMADIKLARI_OKUMA_EKI if okuma_eki else KANITLAMADIKLARI,
    }
    if yonerge_genis:
        sonuc["denetim_surumu"] += YONERGE_GENIS_EKI
    if uzanti is not None:
        ust = uzanti["ust"]
        onceki_bosluklar = (ust.get("publication") or {}).get("gate", {}).get("machine_not_covered")
        sonuc["denetim_surumu"] += UZANTI_EKI
        sonuc["kanitlamadiklari"] = sonuc["kanitlamadiklari"] + [KANITLAMADIKLARI_UZANTI]
        sonuc["uzanti"] = {
            "ust_id": ust["id"], "taban_surum": ust["version"], "birlestirildi": True,
            "gorulen_surum": uzanti["bilgi"].get("gorulen_surum"), "ust_iddia": len(ust["claims"]),
            "yeni_iddia": uzanti["bilgi"].get("yeni_iddia"),
            "kaldirilan_not_covered": len(uzanti["bilgi"].get("kaldirilan_not_covered") or []),
            # Makineyle kapsam: üst kaydın sayfada alıntısız bulunan bölümlerinden birleşik kayıtta artık listede olmayanlar.
            "kapanan_bosluklar": [ad for ad in onceki_bosluklar or [] if ag_acik and ad not in makine_kapsami.get("adlar", [])],
            "ust_alinti_kaydi": ust_alinti_kaydi,
        }
    if okuma_eki:
        # alpha.15 (14r2): kaynak başına getirme zamanı ve görünür metin özeti; not_in_sources araması satırları. Kapalı
        # kipte bu anahtarlar hiç yoktur (sonuç v0.2.3 biçiminde).
        sonuc["kaynak_anliklari"] = anliklar
        sonuc["yokluk_denetimi"] = yokluk_satirlari
    return sonuc
