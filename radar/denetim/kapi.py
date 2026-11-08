"""Denetimleri sırayla çalıştırır, sonuç çizelgesini, genel hükmü (yayımlanır / reddedilir / yayımlanamaz) ve tek satırlık kanıt etiketini üretir."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from . import adres, alinti, getirme, gizlilik, paketler, saglayici, sema, yinelenen, yonerge
from .ayarlar import SURUM, Ayarlar
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

# Kanıt etiketinde denetimlerin kısa adları (D4).
_ETIKET_ADI = {"sema": ("şema", "schema"), "kaynak_acikligi": ("kaynak açıklığı", "source access"),
               "kibar_getirme": ("kibar getirme", "polite fetch"), "alinti_eslesmesi": ("alıntı eşleşmesi", "quote match"),
               "gizli_bilgi": ("gizli bilgi (bilinen kalıplar)", "secrets (known patterns)"),
               "yonerge_kokusu": ("ajan yönergesi", "agent directives"), "paket_adlari": ("paket adları", "package names"),
               "saglayici_kurali": ("sağlayıcı kuralı (beyan)", "provider rule (declared)"),
               "yinelenen_yayin": ("yinelenen yayın", "duplicate check")}
# Kaynağa bağlanmayan serbest metin alanları (O6).
SERBEST_ALANLAR = [("title", "başlık", "title"), ("summary", "özet", "summary"), ("short_answer", "kısa cevap", "short answer"),
                   ("recommendations", "öneriler", "recommendations"), ("not_covered", "kapsam dışı notlar", "not-covered notes"),
                   ("answers", "cevaplanan sorular", "answered questions")]


def _ve(ogeler, baglac):
    return ogeler[0] if len(ogeler) == 1 else ", ".join(ogeler[:-1]) + f" {baglac} " + ogeler[-1]


def serbest_alanlar(paket):
    """Pakette dolu olan ve hiçbir kaynağa bağlanmayan serbest metin alanlarının adları."""
    if not isinstance(paket, dict):
        return []
    return [ad for ad, _, _ in SERBEST_ALANLAR if paket.get(ad) not in (None, "", [])]


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


def _guvenli(adim, sira, ad, baslik, *args, engelleyici=True, **kwargs):
    """Bir denetimin beklenmeyen hatası bütün kapıyı düşürmesin; hata metni dışarı yazılmaz."""
    try:
        return adim(*args, **kwargs)
    except Exception:
        d = Denetim(sira, ad, baslik, engelleyici=engelleyici)
        d.denenemedi("genel.ic_hata")
        return d


def denetle(paket, *, yayindakiler=None, ag=True, getirici=None, ayarlar=None, simdi=None, tam=False):
    """Paketi bütün denetimlerden geçirir ve JSON'a yazılabilir sonuç sözlüğü döndürür."""
    ayarlar = ayarlar or Ayarlar()
    simdi = simdi or datetime.now(timezone.utc)
    if ag and getirici is None:
        getirici = getirme.Getirici(ayarlar=ayarlar)
    tum_kaynaklar = kaynak_listesi(paket)
    kaynaklar = tum_kaynaklar[: ayarlar.en_cok_kaynak]

    # Ağsız denetimler önce: reddedilecek paket için siteye istek atılmaz.
    d1 = _guvenli(sema.denetle, 1, "sema", "Şema ve sınırlar", paket, bugun=simdi.date())
    if len(tum_kaynaklar) > ayarlar.en_cok_kaynak:
        # Y2: sınırın ötesindeki adresler denetlenemez; sessizce kesmek yerine paket reddedilir.
        d1.kal("sema.liste_boyutu", f"Pakette {len(tum_kaynaklar)} ayrı kaynak adresi var (iddialar ve kaynak listesi); "
                                    f"sınır {ayarlar.en_cok_kaynak}. Sınırın ötesi denetlenemeyeceği için ret.")
    d5 = _guvenli(gizlilik.denetle, 5, "gizli_bilgi", "Gizli bilgi", paket)
    d6 = _guvenli(yonerge.denetle, 6, "yonerge_kokusu", "Yönerge kokusu", paket)
    d8 = _guvenli(saglayici.denetle, 8, "saglayici_kurali", "Sağlayıcı kuralı", paket)
    d9 = _guvenli(yinelenen.denetle, 9, "yinelenen_yayin", "Yinelenen yayın", paket if isinstance(paket, dict) else {},
                  yayindakiler, esik=ayarlar.yinelenen_esik, engelleyici=False)
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

    denetimler = sorted([d1, d2, d3, d4, d5, d6, d7, d8, d9], key=lambda d: d.sira)
    ret = [d for d in denetimler if d.engelleyici and d.durum == KALDI]
    eksik = [d for d in denetimler if d.engelleyici and d.durum == DENENEMEDI]
    hukum = REDDEDILIR if ret else YAYIMLANAMAZ if eksik else YAYIMLANIR

    gun = simdi.date().isoformat()
    erisilen = sum(1 for s in sonuclar.values() if s.erisim == "erisildi")
    iddia_satirlari = [s for s in d4.ayrinti if "iddia" in s]
    bulunan = sum(1 for s in iddia_satirlari if s.get("eslesme") == "aynen")
    toplam_iddia = len(paket.get("claims")) if isinstance(paket, dict) and isinstance(paket.get("claims"), list) else 0
    serbest = serbest_alanlar(paket)
    etiket, etiket_en = kanit_etiketleri(denetimler, len(kaynaklar), erisilen, toplam_iddia, bulunan, serbest, gun, ag_acik)

    ozet_girdi = json.dumps(paket, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {
        "denetim_surumu": SURUM,
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
        "denetimler": [d.sozluk() for d in denetimler],
        "kanitlamadiklari": KANITLAMADIKLARI,
    }
